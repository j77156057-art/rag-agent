"""workbench_fs 沙箱与护栏单元测试。

全程在 tempfile 临时代码库中进行，reindex=False 且删除用 .gd/.txt 等非 _CODE_EXT 文件，
保证不触碰 chroma/embedding（无需 Ollama）。git 用例在无 git 环境自动跳过。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import workbench_fs as wb  # noqa: E402


def have_git():
    return shutil.which("git") is not None


def git(args, cwd):
    subprocess.run(["git"] + args, cwd=cwd, capture_output=True, timeout=30, check=False)


def find(nodes, path):
    """按相对路径在嵌套树中查找节点。"""
    target = path.replace("\\", "/")
    stack = list(nodes)
    while stack:
        n = stack.pop()
        if n["path"] == target:
            return n
        stack.extend(n.get("children") or [])
    return None


class WbTestBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="wbfs_test_")
        wb.invalidate_status()

    def tearDown(self):
        wb.invalidate_status()
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, content="x = 1\n"):
        p = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return p

    def enable_regions(self):
        with open(os.path.join(self.root, "regions.json"), "w", encoding="utf-8") as fh:
            json.dump({"regions": [
                {"key": "values", "dir": "values", "name": "数值区", "desc": "",
                 "access": "", "depends_on": [], "exports": [], "verify": ""},
                {"key": "behaviors", "dir": "behaviors", "name": "角色行为区", "desc": "",
                 "access": "", "depends_on": ["values"], "exports": [], "verify": ""},
            ]}, fh, ensure_ascii=False)

    def init_repo(self, rel="."):
        d = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(d, exist_ok=True)
        git(["init", "-q"], cwd=d)
        git(["config", "user.email", "test@local"], cwd=d)
        git(["config", "user.name", "Test"], cwd=d)
        return d


class TestSandbox(WbTestBase):
    def test_no_root(self):
        with self.assertRaises(wb.FsError) as cm:
            wb.build_tree("")
        self.assertEqual(cm.exception.status, 400)

    def test_traversal_rejected(self):
        for bad in ("../x.py", "values/../../x.py", "/etc/passwd", "C:/Windows/x.py", ".."):
            with self.assertRaises(wb.FsError) as cm:
                wb.read_full(self.root, bad)
            self.assertEqual(cm.exception.status, 403, bad)

    def test_backslash_traversal(self):
        with self.assertRaises(wb.FsError) as cm:
            wb.read_full(self.root, r"values\..\..\secret.py")
        self.assertEqual(cm.exception.status, 403)

    def test_empty_path(self):
        with self.assertRaises(wb.FsError):
            wb.read_full(self.root, "  ")


class TestTree(WbTestBase):
    def test_structure_and_filters(self):
        self.write("values/a.py")
        self.write("values/sub/b.gd")
        self.write("note.txt", "hello")
        self.write("values/__pycache__/junk.pyc", "junk")
        self.write("values/node_modules/lib/index.js", "junk")
        self.write("数值/角色.gd", "pass")
        res = wb.build_tree(self.root)
        self.assertTrue(res["ok"])
        self.assertIsNotNone(find(res["nodes"], "values"))
        self.assertIsNotNone(find(res["nodes"], "values/a.py"))
        self.assertIsNotNone(find(res["nodes"], "values/sub/b.gd"))
        self.assertIsNotNone(find(res["nodes"], "note.txt"))
        self.assertIsNotNone(find(res["nodes"], "数值/角色.gd"))
        self.assertIsNone(find(res["nodes"], "values/__pycache__/junk.pyc"))
        self.assertIsNone(find(res["nodes"], "values/node_modules/lib/index.js"))
        a = find(res["nodes"], "values/a.py")
        self.assertEqual(a["lang"], "python")
        self.assertTrue(a["writable"])

    def test_region_metadata_when_enabled(self):
        self.enable_regions()
        self.write("values/a.py")
        self.write("loose.py")
        res = wb.build_tree(self.root)
        self.assertTrue(res["regions_enabled"])
        self.assertEqual(find(res["nodes"], "values/a.py")["region"], "values")
        self.assertEqual(find(res["nodes"], "values/a.py")["region_name"], "数值区")
        self.assertIsNone(find(res["nodes"], "loose.py")["region"])

    def test_depth_limit(self):
        self.write("a/b/c/d/e.py")
        res = wb.build_tree(self.root, depth=2)
        self.assertIsNone(find(res["nodes"], "a/b/c/d/e.py"))
        self.assertIsNotNone(find(res["nodes"], "a/b"))

    @unittest.skipUnless(have_git(), "无 git")
    def test_git_status_fields(self):
        self.enable_regions()
        self.init_repo("values")
        tracked = self.write("values/tracked.py", "v = 1\n")
        git(["add", "-A"], cwd=os.path.dirname(tracked))
        git(["commit", "-q", "-m", "init"], cwd=os.path.dirname(tracked))
        self.write("values/untracked.txt", "new")  # 提交后新建 → 未跟踪
        self.write("values/tracked.py", "v = 2\n")  # 已跟踪但有改动
        res = wb.build_tree(self.root)
        t = find(res["nodes"], "values/tracked.py")
        u = find(res["nodes"], "values/untracked.txt")
        self.assertTrue(t["tracked"])
        self.assertTrue(t["dirty"])
        self.assertFalse(u["tracked"])


class TestReadSave(WbTestBase):
    def test_read_404_and_dir(self):
        with self.assertRaises(wb.FsError) as cm:
            wb.read_full(self.root, "nope.py")
        self.assertEqual(cm.exception.status, 404)
        os.makedirs(os.path.join(self.root, "d"))
        with self.assertRaises(wb.FsError) as cm:
            wb.read_full(self.root, "d")
        self.assertEqual(cm.exception.status, 400)

    def test_read_binary_and_unsupported(self):
        self.write("pic.png", "\x00\x01PNG")
        with self.assertRaises(wb.FsError) as cm:
            wb.read_full(self.root, "pic.png")
        self.assertEqual(cm.exception.status, 415)
        self.write("blob.bin", "abc")
        with self.assertRaises(wb.FsError) as cm:
            wb.read_full(self.root, "blob.bin")
        self.assertEqual(cm.exception.status, 415)

    def test_read_utf8_bom_tolerated(self):
        p = os.path.join(self.root, "bom.gd")
        with open(p, "wb") as fh:
            fh.write("\ufeffextends Node\n".encode("utf-8"))
        r = wb.read_full(self.root, "bom.gd")
        self.assertTrue(r["content"].startswith("extends Node"))

    def test_save_new_and_unchanged(self):
        r1 = wb.save_file(self.root, "values/a.py", "x = 1\n", reindex=False)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "values", "a.py")))
        self.assertTrue(r1["changed"])
        r2 = wb.save_file(self.root, "values/a.py", "x = 1\n", if_mtime=r1["mtime"], reindex=False)
        self.assertFalse(r2["changed"])

    def test_save_syntax_error_blocks_write(self):
        p = self.write("a.py", "x = 1\n")
        with self.assertRaises(wb.FsError) as cm:
            wb.save_file(self.root, "a.py", "def bad(:\n", reindex=False)
        self.assertEqual(cm.exception.status, 422)
        self.assertEqual(cm.exception.extra.get("lineno"), 1)
        with open(p, encoding="utf-8") as fh:  # 原文件未动
            self.assertEqual(fh.read(), "x = 1\n")

    def test_save_mtime_conflict(self):
        r1 = wb.save_file(self.root, "a.py", "x = 1\n", reindex=False)
        with open(os.path.join(self.root, "a.py"), "w", encoding="utf-8") as fh:
            fh.write("x = 99\n")  # 编辑器外被改
        # 用显式过期的客户端 mtime 模拟冲突（同秒写入落在 1.5s 容差内属允许场景）
        with self.assertRaises(wb.FsError) as cm:
            wb.save_file(self.root, "a.py", "x = 2\n", if_mtime=r1["mtime"] - 100, reindex=False)
        self.assertEqual(cm.exception.status, 409)
        self.assertIn("server_mtime", cm.exception.extra)

    def test_save_protected_and_ext_whitelist(self):
        self.enable_regions()
        for bad in ("regions.json", "values/.git/config", ".docmind_backups/x.py"):
            with self.assertRaises(wb.FsError) as cm:
                wb.save_file(self.root, bad, "{}", reindex=False)
            self.assertEqual(cm.exception.status, 403, bad)
        with self.assertRaises(wb.FsError) as cm:
            wb.save_file(self.root, "evil.exe", "MZ", reindex=False)
        self.assertEqual(cm.exception.status, 403)

    def test_save_size_limit(self):
        old = wb.WB_MAX_FILE_BYTES
        wb.WB_MAX_FILE_BYTES = 64
        try:
            with self.assertRaises(wb.FsError) as cm:
                wb.save_file(self.root, "a.py", "x" * 200, reindex=False)
            self.assertEqual(cm.exception.status, 413)
        finally:
            wb.WB_MAX_FILE_BYTES = old


class TestCreateRenameDelete(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="wbfs_test_")
        wb.invalidate_status()

    def tearDown(self):
        wb.invalidate_status()
        shutil.rmtree(self.root, ignore_errors=True)

    def write(self, rel, content="x = 1\n"):
        p = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        return p

    def enable_regions(self):
        with open(os.path.join(self.root, "regions.json"), "w", encoding="utf-8") as fh:
            json.dump({"regions": [
                {"key": "values", "dir": "values", "name": "数值区", "desc": "",
                 "access": "", "depends_on": [], "exports": [], "verify": ""},
                {"key": "behaviors", "dir": "behaviors", "name": "角色行为区", "desc": "",
                 "access": "", "depends_on": ["values"], "exports": [], "verify": ""},
            ]}, fh, ensure_ascii=False)

    def test_create_file_and_folder(self):
        wb.create_path(self.root, "d1", "folder")
        self.assertTrue(os.path.isdir(os.path.join(self.root, "d1")))
        wb.create_path(self.root, "d1/a.gd", "file", "pass")
        self.assertTrue(os.path.isfile(os.path.join(self.root, "d1", "a.gd")))
        with self.assertRaises(wb.FsError) as cm:
            wb.create_path(self.root, "d1", "folder")
        self.assertEqual(cm.exception.status, 409)

    def test_rename_cross_region_blocked_but_allowed_without_regions(self):
        self.enable_regions()
        os.makedirs(os.path.join(self.root, "behaviors"), exist_ok=True)
        self.write("values/a.gd")
        with self.assertRaises(wb.FsError) as cm:
            wb.rename_path(self.root, "values/a.gd", "behaviors/a.gd")
        self.assertEqual(cm.exception.status, 403)

    def test_rename_within_region_and_disabled_mode(self):
        self.write("values/a.gd", "pass")
        wb.rename_path(self.root, "values/a.gd", "values/b.gd", reindex=False)
        self.assertFalse(os.path.isfile(os.path.join(self.root, "values", "a.gd")))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "values", "b.gd")))
        os.makedirs(os.path.join(self.root, "other"), exist_ok=True)
        wb.rename_path(self.root, "values/b.gd", "other/b.gd", reindex=False)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "other", "b.gd")))

    @unittest.skipUnless(have_git(), "无 git")
    def test_rename_tracked_uses_git_mv(self):
        self.enable_regions()
        repo = self.init_repo("values")
        self.write("values/a.gd", "pass")
        git(["add", "-A"], cwd=repo)
        git(["commit", "-q", "-m", "init"], cwd=repo)
        r = wb.rename_path(self.root, "values/a.gd", "values/b.gd", reindex=False)
        self.assertTrue(r["git_renamed"])

    def init_repo(self, rel="."):
        d = os.path.join(self.root, rel.replace("/", os.sep))
        os.makedirs(d, exist_ok=True)
        git(["init", "-q"], cwd=d)
        git(["config", "user.email", "test@local"], cwd=d)
        git(["config", "user.name", "Test"], cwd=d)
        return d

    def test_delete_untracked_requires_force(self):
        self.write("values/new.gd", "pass")
        with self.assertRaises(wb.FsError) as cm:
            wb.delete_path(self.root, "values/new.gd")
        self.assertEqual(cm.exception.status, 409)
        wb.delete_path(self.root, "values/new.gd", force=True)
        self.assertFalse(os.path.exists(os.path.join(self.root, "values", "new.gd")))

    @unittest.skipUnless(have_git(), "无 git")
    def test_delete_tracked_directly(self):
        self.enable_regions()
        repo = self.init_repo("values")
        self.write("values/old.gd", "pass")
        git(["add", "-A"], cwd=repo)
        git(["commit", "-q", "-m", "init"], cwd=repo)
        r = wb.delete_path(self.root, "values/old.gd")
        self.assertTrue(r["recoverable"])
        self.assertFalse(os.path.isfile(os.path.join(self.root, "values", "old.gd")))

    def test_delete_folder_rules(self):
        self.write("d/sub/a.txt", "a")
        with self.assertRaises(wb.FsError) as cm:
            wb.delete_path(self.root, "d")
        self.assertEqual(cm.exception.status, 409)
        # 未跟踪内容：recursive 但无 force 仍拦截
        with self.assertRaises(wb.FsError) as cm:
            wb.delete_path(self.root, "d", recursive=True)
        self.assertEqual(cm.exception.status, 409)
        wb.delete_path(self.root, "d", recursive=True, force=True)
        self.assertFalse(os.path.isdir(os.path.join(self.root, "d")))

    def test_delete_protected(self):
        with self.assertRaises(wb.FsError):
            wb.delete_path(self.root, "regions.json")

    @unittest.skipUnless(have_git(), "无 git")
    def test_gitlog(self):
        self.enable_regions()
        repo = self.init_repo("values")
        self.write("values/a.gd", "pass")
        git(["add", "-A"], cwd=repo)
        git(["commit", "-q", "-m", "first commit"], cwd=repo)
        r = wb.git_log(self.root, "values/a.gd")
        self.assertEqual(len(r["commits"]), 1)
        self.assertIn("first commit", r["commits"][0]["message"])
        with self.assertRaises(wb.FsError) as cm:
            wb.git_log(self.root, "nope.gd")
        self.assertEqual(cm.exception.status, 404)


if __name__ == "__main__":
    unittest.main(verbosity=2)
