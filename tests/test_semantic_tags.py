"""阶段 1：语义业务标签 / 大白话定位 / 分区卡片 单元测试。

全程 tempfile，不触网：
- LLM 用注入的假 llm_call，默认走规则降级（use_llm=False）。
- 向量检索用注入的假 vector_search，不碰 chroma/embedding。
- git 用例在无 git 环境自动跳过。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import semantic_tags as st  # noqa: E402
import workbench_fs as wb  # noqa: E402


def have_git():
    return shutil.which("git") is not None


def git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, capture_output=True, timeout=30, check=False)


class TagTestBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="stags_test_")
        wb.invalidate_status()
        st.invalidate_scan(self.root)

    def tearDown(self):
        wb.invalidate_status()
        st.invalidate_scan(self.root)
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, content):
        p = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return p

    def enable_regions(self):
        with open(os.path.join(self.root, "regions.json"), "w", encoding="utf-8") as fh:
            json.dump({"regions": [
                {"key": "values", "dir": "values", "name": "数值区",
                 "desc": "玩家与敌人的数值配置", "access": "", "depends_on": [],
                 "exports": [], "verify": ""},
                {"key": "behaviors", "dir": "behaviors", "name": "角色行为区",
                 "desc": "角色行为逻辑", "access": "", "depends_on": ["values"],
                 "exports": [], "verify": ""},
            ]}, fh, ensure_ascii=False)

    def store(self):
        return st.load_store(self.root)


class TestRuleTagging(TagTestBase):
    def test_rules_hit_game_concepts(self):
        self.write("scripts/player/player_stats.gd",
                   "extends Node\nvar max_hp = 100\nvar attack_power = 10\n"
                   "func take_damage(amount):\n    max_hp -= amount\n")
        self.write("scripts/enemy/enemy_ai.gd",
                   "extends Node\nfunc patrol(): pass\nfunc chase_target(): pass\n")
        r = st.ensure_tags(self.root, use_llm=False)
        self.assertEqual(r["tagged_files"], 2)
        self.assertEqual(r["tagged_rules"], 2)
        self.assertEqual(r["pending"], 0)
        tags = r["files"]["scripts/player/player_stats.gd"]["tags"]
        self.assertTrue(any("数值" in t or "生命" in t or "玩家" in t for t in tags))
        # 落盘在 .docmind 下
        self.assertTrue(os.path.isfile(st.store_path(self.root)))

    def test_contract_files_excluded(self):
        self.write("regions.json", '{"regions": []}')
        self.write("scripts/a.gd", "var hp = 1\n")
        r = st.ensure_tags(self.root, use_llm=False)
        self.assertNotIn("regions.json", r["files"])
        self.assertIn("scripts/a.gd", r["files"])

    def test_unknown_file_gets_fallback_tag(self):
        self.write("scripts/zzz.gd", "extends Node\n")
        r = st.ensure_tags(self.root, use_llm=False)
        rec = r["files"]["scripts/zzz.gd"]
        self.assertTrue(rec["tags"])
        self.assertEqual(rec["origin"], "rules")


class TestLlmBatch(TagTestBase):
    def test_llm_tags_used_and_fenced_json_parsed(self):
        self.write("scripts/player/player_stats.gd",
                   "extends Node\nvar hp = 1\n")

        def fake_llm(prompt):
            return "```json\n" + json.dumps({
                "results": [{
                    "path": "scripts/player/player_stats.gd",
                    "tags": ["玩家属性", "伤害承受"],
                    "summary": "管理玩家生命值",
                }]
            }, ensure_ascii=False) + "\n```"

        r = st.ensure_tags(self.root, force=True, llm_call=fake_llm)
        rec = r["files"]["scripts/player/player_stats.gd"]
        self.assertEqual(rec["origin"], "llm")
        self.assertEqual(rec["tags"], ["玩家属性", "伤害承受"])
        self.assertEqual(rec["summary"], "管理玩家生命值")

    def test_llm_failure_falls_back_to_rules(self):
        self.write("scripts/player/player_stats.gd", "var hp = 1\n")

        def boom(prompt):
            raise RuntimeError("ollama down")

        r = st.ensure_tags(self.root, force=True, llm_call=boom)
        rec = r["files"]["scripts/player/player_stats.gd"]
        self.assertEqual(rec["origin"], "rules")
        self.assertTrue(rec["tags"])

    def test_llm_garbage_falls_back_to_rules(self):
        self.write("scripts/player/player_stats.gd", "var hp = 1\n")
        r = st.ensure_tags(self.root, force=True, llm_call=lambda p: "我无法理解")
        self.assertEqual(r["files"]["scripts/player/player_stats.gd"]["origin"], "rules")


class TestIncrementalAndManual(TagTestBase):
    def _seed(self):
        self.write("scripts/a.gd", "var hp = 1\n")
        return st.ensure_tags(self.root, use_llm=False)

    def test_modified_file_becomes_pending(self):
        self._seed()
        # 直接改文件（跳过 save_file），再标记 on_saved：自动记录应被删，待刷新 +1
        time.sleep(0.02)
        with open(os.path.join(self.root, "scripts", "a.gd"), "a", encoding="utf-8") as fh:
            fh.write("\nvar defense = 2\n")
        st.invalidate_scan(self.root)
        st.on_saved(self.root, "scripts/a.gd")
        status = st.tag_status(self.root)
        self.assertEqual(status["pending"], 1)
        self.assertEqual(status["tagged_files"], 0)

    def test_manual_tags_survive_refresh_and_save(self):
        self.write("scripts/a.gd", "var hp = 1\n")
        st.ensure_tags(self.root, use_llm=False)
        st.manual_update(self.root, "scripts/a.gd", ["我的自定义标签"])
        # 自动刷新不覆盖人工标签
        st.ensure_tags(self.root, use_llm=False)
        self.assertEqual(
            self.store()["files"]["scripts/a.gd"]["tags"], ["我的自定义标签"])
        self.assertEqual(self.store()["files"]["scripts/a.gd"]["origin"], "manual")
        # 保存后人工标签仍在（stale 标记，由 status 计入 pending，可人工决定）
        with open(os.path.join(self.root, "scripts", "a.gd"), "a", encoding="utf-8") as fh:
            fh.write("\nvar x = 2\n")
        st.invalidate_scan(self.root)
        st.on_saved(self.root, "scripts/a.gd")
        rec = self.store()["files"]["scripts/a.gd"]
        self.assertEqual(rec["tags"], ["我的自定义标签"])
        self.assertTrue(rec["stale"])

    def test_manual_rejects_empty_and_missing(self):
        self.write("scripts/a.gd", "var hp = 1\n")
        with self.assertRaises(ValueError):
            st.manual_update(self.root, "scripts/a.gd", [])
        with self.assertRaises(FileNotFoundError):
            st.manual_update(self.root, "scripts/nope.gd", ["x"])

    def test_deleted_records_are_garbage_collected(self):
        self._seed()
        os.remove(os.path.join(self.root, "scripts", "a.gd"))
        st.invalidate_scan(self.root)
        r = st.ensure_tags(self.root, use_llm=False)
        self.assertEqual(r["removed"], 1)
        self.assertNotIn("scripts/a.gd", self.store()["files"])

    def test_limit_caps_one_run(self):
        for i in range(3):
            self.write(f"scripts/f{i}.gd", "var hp = 1\n")
        r = st.ensure_tags(self.root, use_llm=False, limit=2)
        self.assertEqual(r["tagged_files"], 2)
        self.assertEqual(r["pending"], 1)
        r2 = st.ensure_tags(self.root, use_llm=False)
        self.assertEqual(r2["tagged_files"], 3)
        self.assertEqual(r2["pending"], 0)


class TestWriteHooks(TagTestBase):
    def test_save_rename_delete_keep_store_fresh(self):
        rel = "scripts/player.gd"
        self.write(rel, "var hp = 1\n")
        st.ensure_tags(self.root, use_llm=False)
        self.assertIn(rel, self.store()["files"])

        # save：内容变化 → 记录失效
        wb.save_file(self.root, rel, "var hp = 2\nvar mp = 3\n", reindex=False)
        self.assertNotIn(rel, self.store()["files"])

        # 重新标注 + rename → 记录跟随
        st.ensure_tags(self.root, use_llm=False)
        wb.rename_path(self.root, rel, "scripts/hero.gd", reindex=False)
        self.assertIn("scripts/hero.gd", self.store()["files"])
        self.assertNotIn(rel, self.store()["files"])

        # delete → 记录消失（未跟踪文件需 force）
        wb.delete_path(self.root, "scripts/hero.gd", force=True)
        self.assertNotIn("scripts/hero.gd", self.store()["files"])


class TestLocate(TagTestBase):
    @staticmethod
    def fake_vector(hits):
        """hits: [(source, symbol, line)] → 模拟 chroma 返回。"""
        def _search(q, k):
            return {
                "documents": [["body"] * len(hits)],
                "metadatas": [[{"source": s, "symbol": sym, "start_line": ln}
                               for s, sym, ln in hits]],
            }
        return _search

    def test_local_tag_and_filename_match(self):
        self.enable_regions()
        self.write("values/player_stats.gd",
                   "var max_hp = 100\nvar attack = 10\n")
        st.ensure_tags(self.root, use_llm=False)
        r = st.locate(self.root, "数值", vector_search=lambda q, k:
                      {"documents": [[]], "metadatas": [[]]})
        paths = [f["path"] for f in r["files"]]
        self.assertIn("values/player_stats.gd", paths)
        # 分区名命中
        self.assertTrue(any(x["key"] == "values" for x in r["regions"]))

    def test_vector_hit_merges_with_region_info(self):
        self.enable_regions()
        self.write("values/player_stats.gd", "var hp = 1\n")
        r = st.locate(self.root, "玩家受伤扣多少血",
                      vector_search=self.fake_vector([
                          ("values/player_stats.gd", "take_damage", 12)]))
        row = next(f for f in r["files"] if f["path"] == "values/player_stats.gd")
        self.assertEqual(row["line"], 12)
        self.assertEqual(row["symbol"], "take_damage")
        self.assertEqual(row["region"], "values")
        self.assertEqual(row["region_name"], "数值区")
        self.assertIn("语义向量检索", row["reasons"])

    def test_vector_backend_down_degrades_gracefully(self):
        self.write("scripts/a.gd", "var hp = 1\n")
        st.ensure_tags(self.root, use_llm=False)

        def boom(q, k):
            raise ConnectionError("no chroma")

        r = st.locate(self.root, "生命", vector_search=boom)
        self.assertTrue(r["degraded"])
        # 文件名/规则路径仍然有结果
        self.assertTrue(r["files"])


@unittest.skipUnless(have_git(), "环境无 git")
class TestRegionCards(TagTestBase):
    def test_cards_have_dirty_count_and_last_commit(self):
        self.enable_regions()
        os.makedirs(os.path.join(self.root, "values"), exist_ok=True)
        git(["init", "-q"], cwd=self.root)
        git(["config", "user.email", "t@t"], cwd=self.root)
        git(["config", "user.name", "T"], cwd=self.root)
        self.write("values/stats.gd", "var hp = 1\n")
        git(["add", "values/stats.gd"], cwd=self.root)
        git(["commit", "-q", "-m", "feat: 初始化数值"], cwd=self.root)
        # 再来一笔未提交改动
        with open(os.path.join(self.root, "values", "stats.gd"), "a",
                  encoding="utf-8") as fh:
            fh.write("var mp = 2\n")

        cards = wb.build_region_cards(self.root)
        by_key = {c["key"]: c for c in cards["regions"]}
        values = by_key["values"]
        self.assertTrue(values["exists"])
        self.assertEqual(values["dirty_count"], 1)
        self.assertEqual(values["last_commit"]["message"], "feat: 初始化数值")
        self.assertTrue(values["last_commit"]["hash"])
        # 未创建的分区不报错
        self.assertIsNone(by_key["behaviors"]["last_commit"])


if __name__ == "__main__":
    unittest.main()
