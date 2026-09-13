"""list_dir 参数解析回归测试。

弱模型常以 list_dir(behaviors/) 内联裸路径调用（剥引号后 arg 无 path: 前缀），
旧实现静默回退根目录，导致模型连续三次"看到相同结果"被空转护栏强制收尾。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tools  # noqa: E402
from config import set_runtime  # noqa: E402


class ListDirBarePathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="docmind-listdir-")
        os.makedirs(os.path.join(self.tmp, "behaviors"))
        os.makedirs(os.path.join(self.tmp, "values"))
        with open(os.path.join(self.tmp, "behaviors", "player.gd"), "w", encoding="utf-8") as f:
            f.write("extends Node\n")
        with open(os.path.join(self.tmp, "root.txt"), "w", encoding="utf-8") as f:
            f.write("x")
        set_runtime("code_root", self.tmp)

    def tearDown(self):
        set_runtime("code_root", "")

    def test_bare_path_with_slash(self):
        out = tools.list_dir("behaviors/")
        self.assertIn("behaviors", out)
        self.assertIn("player.gd", out)
        self.assertNotIn("root.txt", out)  # 不能回退根目录

    def test_bare_path_without_slash(self):
        out = tools.list_dir("behaviors")
        self.assertIn("player.gd", out)
        self.assertNotIn("root.txt", out)

    def test_quoted_bare_path(self):
        out = tools.list_dir('"behaviors/"')
        self.assertIn("player.gd", out)

    def test_keyed_form_still_works(self):
        out = tools.list_dir("path: values")
        self.assertIn("values", out)
        self.assertNotIn("player.gd", out)

    def test_empty_lists_root(self):
        out = tools.list_dir("")
        self.assertIn("root.txt", out)
        self.assertIn("behaviors", out)

    def test_missing_dir_message(self):
        out = tools.list_dir("nope/")
        self.assertIn("不是目录", out)

    def test_traversal_rejected(self):
        out = tools.list_dir("../../")
        self.assertIn("拒绝访问", out)


if __name__ == "__main__":
    unittest.main()
