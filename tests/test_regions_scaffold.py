"""分区可视化一键创建 / 补齐导出桩单元测试。

全程在 tempfile 临时代码库中进行，直接调用 regions.scaffold_region /
fill_region_exports / list_regions（显式传 root，不碰全局 code_root）。
git 用例在无 git 环境自动跳过。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import regions  # noqa: E402


def have_git():
    return shutil.which("git") is not None


def git(args, cwd):
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True,
                          timeout=30, check=False, text=True)


class RegionScaffoldTestBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="region_scaffold_")
        # assets/values 带导出，behaviors 无导出；../evil 应被归一化层丢弃
        self.write_config([
            {"key": "assets", "dir": "assets", "name": "素材区", "desc": "美术资源",
             "access": "dev_asset_get", "depends_on": [],
             "exports": ["manifest.json"], "verify": "builtin:json"},
            {"key": "values", "dir": "values", "name": "数值区", "desc": "数值表",
             "access": "直接读", "depends_on": [],
             "exports": ["balance.schema.json"], "verify": "builtin:json"},
            {"key": "behaviors", "dir": "behaviors", "name": "角色行为区", "desc": "FSM",
             "access": "直接读", "depends_on": ["values", "assets"],
             "exports": [], "verify": "builtin:py"},
            {"key": "evil", "dir": "../evil-escape", "name": "逃逸区", "desc": "",
             "access": "", "depends_on": [], "exports": [], "verify": ""},
        ])

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def write_config(self, regs):
        with open(os.path.join(self.root, "regions.json"), "w", encoding="utf-8") as fh:
            json.dump({"regions": regs}, fh, ensure_ascii=False)

    def mkdir(self, rel):
        d = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(d, exist_ok=True)
        return d

    def write_file(self, rel, content):
        p = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return p

    def by_key(self, rows):
        return {r["key"]: r for r in rows}


class TestFillRegionExports(RegionScaffoldTestBase):
    def test_unknown_key(self):
        ok, msg = regions.fill_region_exports(self.root, "nope")
        self.assertFalse(ok)
        self.assertIn("未知分区", msg)

    def test_dir_missing(self):
        ok, msg = regions.fill_region_exports(self.root, "assets")
        self.assertFalse(ok)
        self.assertIn("尚未创建", msg)

    def test_creates_json_stub(self):
        self.mkdir("values")
        ok, detail = regions.fill_region_exports(self.root, "values")
        self.assertTrue(ok)
        self.assertEqual(detail["created_files"], ["balance.schema.json"])
        with open(os.path.join(self.root, "values", "balance.schema.json"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{}\n")

    def test_idempotent(self):
        self.mkdir("values")
        ok, first = regions.fill_region_exports(self.root, "values")
        self.assertTrue(ok)
        ok, second = regions.fill_region_exports(self.root, "values")
        self.assertTrue(ok)
        self.assertEqual(second["created_files"], [])

    def test_preserves_existing_content(self):
        self.write_file("values/balance.schema.json", '{"hp": 100}\n')
        ok, detail = regions.fill_region_exports(self.root, "values")
        self.assertTrue(ok)
        self.assertEqual(detail["created_files"], [])
        with open(os.path.join(self.root, "values", "balance.schema.json"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), '{"hp": 100}\n')

    def test_malicious_dir_dropped(self):
        # ../evil-escape 被 _normalize_regions 丢弃，不得在 root 外创建目录
        ok, msg = regions.fill_region_exports(self.root, "evil")
        self.assertFalse(ok)
        self.assertFalse(os.path.isdir(os.path.join(os.path.dirname(self.root), "evil-escape")))


class TestScaffoldRegion(RegionScaffoldTestBase):
    def test_unknown_key(self):
        ok, msg = regions.scaffold_region(self.root, "ghost")
        self.assertFalse(ok)
        self.assertIn("未知分区", msg)

    def test_creates_dir_stub_and_readme(self):
        ok, detail = regions.scaffold_region(self.root, "assets")
        self.assertTrue(ok)
        self.assertTrue(detail["created_dir"])
        self.assertIn("manifest.json", detail["created_files"])
        self.assertIn("README.md", detail["created_files"])
        with open(os.path.join(self.root, "assets", "manifest.json"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{}\n")
        with open(os.path.join(self.root, "assets", "README.md"), encoding="utf-8") as fh:
            readme = fh.read()
        self.assertIn("素材区", readme)
        self.assertIn("纳入项目主仓库统一版本管理", readme)
        # 无 git 兄弟分区：不得自行 git init
        self.assertFalse(os.path.isdir(os.path.join(self.root, "assets", ".git")))

    def test_no_exports_region_only_readme(self):
        ok, detail = regions.scaffold_region(self.root, "behaviors")
        self.assertTrue(ok)
        self.assertEqual(detail["created_files"], ["README.md"])

    def test_idempotent_keeps_readme(self):
        ok, _ = regions.scaffold_region(self.root, "assets")
        self.assertTrue(ok)
        readme_path = os.path.join(self.root, "assets", "README.md")
        with open(readme_path, "w", encoding="utf-8") as fh:
            fh.write("# 用户改过的 README\n")
        ok, detail = regions.scaffold_region(self.root, "assets")
        self.assertTrue(ok)
        self.assertFalse(detail["created_dir"])
        self.assertEqual(detail["created_files"], [])
        with open(readme_path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "# 用户改过的 README\n")

    def test_non_git_siblings_no_git_init(self):
        self.mkdir("values")  # 兄弟分区存在但不是独立 git 仓库
        ok, _ = regions.scaffold_region(self.root, "behaviors")
        self.assertTrue(ok)
        self.assertFalse(os.path.isdir(os.path.join(self.root, "behaviors", ".git")))

    @unittest.skipUnless(have_git(), "环境无 git")
    def test_git_siblings_trigger_git_init(self):
        d = self.mkdir("values")
        git(["init", "-q"], cwd=d)
        git(["config", "user.email", "test@local"], cwd=d)
        git(["config", "user.name", "Test"], cwd=d)
        self.write_file("values/balance.schema.json", "{}\n")
        git(["add", "-A"], cwd=d)
        git(["commit", "-q", "-m", "base"], cwd=d)
        ok, _ = regions.scaffold_region(self.root, "behaviors")
        self.assertTrue(ok)
        git_dir = os.path.join(self.root, "behaviors", ".git")
        self.assertTrue(os.path.isdir(git_dir))
        log = git(["log", "--oneline"], cwd=os.path.join(self.root, "behaviors"))
        self.assertIn("initialize region", log.stdout)

    def test_malicious_dir_never_created(self):
        ok, _ = regions.scaffold_region(self.root, "evil")
        self.assertFalse(ok)
        self.assertFalse(os.path.isdir(os.path.join(os.path.dirname(self.root), "evil-escape")))


class TestListRegionsMissingExports(RegionScaffoldTestBase):
    def test_field_states(self):
        # 目录未创建：missing_exports 恒为空
        rows = self.by_key(regions.list_regions(self.root))
        self.assertEqual(rows["assets"]["missing_exports"], [])

        # 目录已存在但缺桩：列出缺失文件
        self.mkdir("values")
        rows = self.by_key(regions.list_regions(self.root))
        self.assertEqual(rows["values"]["missing_exports"], ["balance.schema.json"])

        # 补齐后清空
        regions.fill_region_exports(self.root, "values")
        rows = self.by_key(regions.list_regions(self.root))
        self.assertEqual(rows["values"]["missing_exports"], [])


class TestContractsIntegration(RegionScaffoldTestBase):
    def test_green_after_scaffold_and_fill(self):
        self.mkdir("values")
        ok_s, _ = regions.scaffold_region(self.root, "assets")
        ok_f, _ = regions.fill_region_exports(self.root, "values")
        self.assertTrue(ok_s and ok_f)
        result = regions.verify_contracts(self.root)
        self.assertTrue(result["ok"], msg=str(result["errors"]))


if __name__ == "__main__":
    unittest.main()
