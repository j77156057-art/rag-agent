# -*- coding: utf-8 -*-
"""P2 项目数据层回归（projects.py + config 迁移 + sessions 分桶）。

全部离线、隔离：把 `config.STATE_FILE` 与 `sessions.SESSIONS_DIR` 指到临时目录，绝不触碰
仓库根或测试进程共享状态。特性开关 `DOCMIND_PROJECTS` 为**动态读取**（见 projects.enabled），
故测试直接 `mock.patch.dict(os.environ)` 即可，无需 `importlib.reload`。

覆盖：
① project_id 稳定化（同目录同 id；斜杠/大小写归一；不同目录不同 id）；
② 迁移幂等（STATE_FILE 仅有 code_root → 登记为项目并设为当前；再调一次不重复登记）；
③ 显式设置优先（set_runtime('code_root', X) 不被持久化值 Y 覆盖）；
④ 会话分桶（A 的目录有文件、B 没有；list/load 按项目隔离）；
⑤ 向后兼容（不传 project_id 时按「当前项目」工作）；
⑥ 特性开关（DOCMIND_PROJECTS=0 → code_collection 回 legacy 名、sessions 走旧扁平路径）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402
import projects  # noqa: E402
import sessions  # noqa: E402


class ProjectsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_projects_")
        self._old_state_file = config.STATE_FILE
        self._old_sessions_dir = sessions.SESSIONS_DIR
        self._old_runtime = dict(config._RUNTIME)
        config.STATE_FILE = os.path.join(self.tmp, ".docmind_state.json")
        sessions.SESSIONS_DIR = os.path.join(self.tmp, "sessions")
        self._env = mock.patch.dict(os.environ, {"DOCMIND_PROJECTS": "1"})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        config.STATE_FILE = self._old_state_file
        sessions.SESSIONS_DIR = self._old_sessions_dir
        config._RUNTIME.clear()
        config._RUNTIME.update(self._old_runtime)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mkdir(self, name):
        d = os.path.join(self.tmp, name)
        os.makedirs(d, exist_ok=True)
        return d


class ProjectIdTests(ProjectsBase):
    def test_same_dir_same_id_different_dir_different_id(self):
        a = self._mkdir("Alpha")
        ida1 = projects.ensure_project(a)
        ida2 = projects.ensure_project(a)
        self.assertEqual(ida1, ida2, "同一目录两次 ensure_project 必须同 id")
        self.assertEqual(ida1, projects.project_id(a))
        self.assertTrue(ida1.startswith("prj-") and len(ida1) == 16)
        b = self._mkdir("Beta")
        self.assertNotEqual(projects.ensure_project(b), ida1)

    def test_project_id_normalizes_path_variants(self):
        a = self._mkdir("Gamma")
        base_id = projects.project_id(a)
        # 斜杠方向差异 → 同 id（abspath 归一）
        self.assertEqual(projects.project_id(a.replace(os.sep, "/")), base_id)
        if os.name == "nt":
            # Windows：盘符/大小写由 normcase 归一 → 同 id
            self.assertEqual(projects.project_id(a.upper()), base_id)
            self.assertEqual(projects.project_id(a.lower()), base_id)


class MigrationTests(ProjectsBase):
    def _write_state(self, payload):
        with open(config.STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def test_migration_registers_code_root_and_is_idempotent(self):
        d = self._mkdir("MigProj")
        self._write_state({"code_root": d})
        config._RUNTIME.pop("code_root", None)

        config._apply_persisted_state()
        pid = projects.project_id(d)
        rows1 = projects.list_projects()
        self.assertEqual(len(rows1), 1, "老状态应被登记为一个项目")
        self.assertEqual(rows1[0]["project_id"], pid)
        self.assertEqual(projects.current_project_id(), pid)
        with open(config.STATE_FILE, encoding="utf-8") as f:
            raw = json.load(f)
        self.assertEqual(raw.get("current_project_id"), pid)

        # 第二次调用：幂等，不重复登记
        config._apply_persisted_state()
        rows2 = projects.list_projects()
        self.assertEqual(len(rows2), 1, "重复调用不得重复登记")
        self.assertEqual(rows2[0]["project_id"], pid)

    def test_explicit_code_root_not_clobbered(self):
        d_state = self._mkdir("FromState")
        d_explicit = self._mkdir("Explicit")
        self._write_state({"code_root": d_state})
        config.set_runtime("code_root", d_explicit)
        config._apply_persisted_state()
        self.assertEqual(config.get_runtime("code_root"), d_explicit,
                         "显式设置的 code_root 不应被持久化值覆盖")


class SessionBucketTests(ProjectsBase):
    def test_sessions_isolated_per_project(self):
        a, b = "prj-aaa", "prj-bbb"
        self.assertTrue(sessions.save("s1", [{"user": "u", "assistant": "a"}], project_id=a))
        dir_a = os.path.join(sessions.SESSIONS_DIR, sessions._slug(a))
        dir_b = os.path.join(sessions.SESSIONS_DIR, sessions._slug(b))
        self.assertTrue(os.path.isfile(os.path.join(dir_a, sessions._slug("s1") + ".json")))
        self.assertFalse(os.path.isdir(dir_b), "B 项目不应有目录/文件")

        self.assertIn("s1", [x["session_id"] for x in sessions.list_sessions(project_id=a)])
        self.assertEqual(sessions.list_sessions(project_id=b), [])
        self.assertEqual(sessions.load("s1", project_id=b).get("turns"), [])

    def test_default_project_is_current(self):
        d = self._mkdir("CurProj")
        pid = projects.ensure_project(d)
        self.assertTrue(projects.set_current(pid))

        self.assertTrue(sessions.save("sid-cur", [{"user": "q", "assistant": "r"}]))
        self.assertEqual(len(sessions.load("sid-cur").get("turns")), 1)
        self.assertEqual(sessions.history("sid-cur")[0]["user"], "q")
        self.assertEqual(sessions.summary_text("sid-cur"), "")
        self.assertIn("sid-cur", [x["session_id"] for x in sessions.list_sessions()])
        # 落在「当前项目」桶里
        expect = os.path.join(sessions.SESSIONS_DIR, sessions._slug(pid), sessions._slug("sid-cur") + ".json")
        self.assertTrue(os.path.isfile(expect))

    def test_remove_project_only_unregisters(self):
        d = self._mkdir("RmvProj")
        pid = projects.ensure_project(d)
        self.assertTrue(projects.set_current(pid))
        sessions.save("keep", [{"user": "u", "assistant": "a"}], project_id=pid)

        self.assertTrue(projects.remove_project(pid))
        self.assertIsNone(projects.get_project(pid))
        # 磁盘上的会话不被删除
        self.assertEqual(len(sessions.load("keep", project_id=pid).get("turns")), 1)
        self.assertFalse(projects.remove_project(pid), "重复删除应返回 False")


class FeatureFlagTests(ProjectsBase):
    def test_code_collection_enabled(self):
        d = self._mkdir("ColProj")
        pid = projects.ensure_project(d)
        self.assertTrue(projects.set_current(pid))
        self.assertEqual(projects.code_collection(), "docmind_code__" + pid)
        self.assertEqual(projects.code_collection(pid), "docmind_code__" + pid)

    def test_disabled_flag_falls_back_to_legacy(self):
        d = self._mkdir("FlagProj")
        pid = projects.ensure_project(d)
        self.assertTrue(projects.set_current(pid))

        with mock.patch.dict(os.environ, {"DOCMIND_PROJECTS": "0"}):
            self.assertFalse(projects.enabled())
            self.assertEqual(projects.current_project_id(), projects.LEGACY_ID)
            self.assertEqual(projects.code_collection(), config.CODE_COLLECTION_NAME)
            self.assertEqual(projects.code_collection(pid), config.CODE_COLLECTION_NAME)
            # sessions 走旧扁平路径：文件直接落在 SESSIONS_DIR 下，不建项目子目录
            self.assertTrue(sessions.save("leg", [{"user": "x", "assistant": "y"}]))
            flat = os.path.join(sessions.SESSIONS_DIR, sessions._slug("leg") + ".json")
            self.assertTrue(os.path.isfile(flat))
            self.assertFalse(os.path.isdir(os.path.join(sessions.SESSIONS_DIR, sessions._slug(pid))))

        # 恢复开关后回到项目语义
        self.assertEqual(projects.code_collection(), "docmind_code__" + pid)
        self.assertEqual(projects.current_project_id(), pid)


class LegacyReadThroughTests(ProjectsBase):
    """P2 读穿透：迁移后有「当前项目」，旧扁平目录里的历史会话仍须可见/可读/可删。"""

    def setUp(self):
        super().setUp()
        d = self._mkdir("LegacyProj")
        self.pid = projects.ensure_project(d)
        self.assertTrue(projects.set_current(self.pid))
        # 直接写旧扁平目录，模拟既有安装的历史会话
        self.legacy_sid = "old-1"
        self.legacy_file = self._write_flat(self.legacy_sid, [{"user": "oldq", "assistant": "olda"}])

    def _write_flat(self, sid, turns, summary=None):
        os.makedirs(sessions.SESSIONS_DIR, exist_ok=True)
        p = os.path.join(sessions.SESSIONS_DIR, sessions._slug(sid) + ".json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump({"session_id": sid, "updated_at": "2024-01-01T00:00:00",
                       "summary": summary or "", "turns": turns}, f)
        return p

    def test_list_sees_legacy_and_bucket_without_dup(self):
        self.assertTrue(sessions.save("new-1", [{"user": "nq", "assistant": "na"}]))
        ids = [x["session_id"] for x in sessions.list_sessions()]
        self.assertIn(self.legacy_sid, ids, "旧扁平目录的历史会话必须仍可见")
        self.assertIn("new-1", ids)
        self.assertEqual(len(ids), len(set(ids)), "不得出现重复条目")

    def test_load_reads_legacy_turns(self):
        data = sessions.load(self.legacy_sid)
        self.assertEqual(len(data.get("turns")), 1)
        self.assertEqual(data["turns"][0]["user"], "oldq")
        self.assertEqual(sessions.history(self.legacy_sid)[0]["user"], "oldq")

    def test_delete_removes_legacy_file(self):
        self.assertTrue(os.path.isfile(self.legacy_file))
        self.assertTrue(sessions.delete(self.legacy_sid))
        self.assertFalse(os.path.isfile(self.legacy_file), "删除必须同时清掉旧扁平目录文件")

    def test_save_writes_bucket_only(self):
        self.assertTrue(sessions.save("fresh-9", [{"user": "u", "assistant": "a"}]))
        bucket = os.path.join(sessions.SESSIONS_DIR, sessions._slug(self.pid), sessions._slug("fresh-9") + ".json")
        flat = os.path.join(sessions.SESSIONS_DIR, sessions._slug("fresh-9") + ".json")
        self.assertTrue(os.path.isfile(bucket))
        self.assertFalse(os.path.isfile(flat), "新会话不得写进旧扁平目录")

    def test_bucket_priority_over_legacy_same_sid(self):
        # 同一 sid 两处内容不同 → 项目桶优先，去重稳定（不抖动）
        self._write_flat("dup-1", [{"user": "legacy-q", "assistant": "legacy-a"}])
        sessions.save("dup-1", [{"user": "bucket-q", "assistant": "bucket-a"}])
        self.assertEqual(sessions.load("dup-1")["turns"][0]["user"], "bucket-q")
        rows = [x for x in sessions.list_sessions() if x["session_id"] == "dup-1"]
        self.assertEqual(len(rows), 1, "同一 sid 只出现一次")

    def test_legacy_mode_lists_flat_once(self):
        self._write_flat("solo-1", [{"user": "q", "assistant": "a"}])
        with mock.patch.dict(os.environ, {"DOCMIND_PROJECTS": "0"}):
            ids = [x["session_id"] for x in sessions.list_sessions()]
        self.assertEqual(ids.count("solo-1"), 1, "legacy 模式不得重复计数")


if __name__ == '__main__':
    unittest.main()
