import os
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import tools
from config import set_runtime


class FileConflictTests(unittest.TestCase):
    def test_apply_edit_fails_closed_when_file_changes_before_commit(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "main.txt")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("before")
            old_root = tools.get_runtime("code_root")
            old_confirm = tools.get_runtime("edit_confirm")
            set_runtime("code_root", root)
            set_runtime("edit_confirm", False)
            tools.clear_read_files()

            @contextmanager
            def race(_target):
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write("parallel result")
                yield

            try:
                with patch.object(tools, "_file_lock", race):
                    result = tools.apply_edit(
                        "path: main.txt\nold_text: before\nnew_text: model result")
                self.assertIn("并行修改冲突", result)
                with open(path, encoding="utf-8") as handle:
                    self.assertEqual(handle.read(), "parallel result")
            finally:
                tools.clear_read_files()
                set_runtime("code_root", old_root or "")
                set_runtime("edit_confirm", old_confirm)


if __name__ == "__main__":
    unittest.main()
