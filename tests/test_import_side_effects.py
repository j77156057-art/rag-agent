# -*- coding: utf-8 -*-
"""导入期副作用回归。

两类历史副作用：
- gpu_coordinator 在**模块导入期**调用 restore_runtime_state()，会读并删除状态文件；
- config 在**模块导入期** os.makedirs(CHROMA_DIR)，无脑建目录。

现在：状态恢复改由 gpu.init() 显式调用（api.py lifespan）；目录创建改由
config.ensure_dirs() 惰性执行。本文件把「导入不得产生磁盘副作用」钉死。
"""
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import gpu_coordinator as g


class ImportSideEffectTests(unittest.TestCase):
    def test_reload_gpu_coordinator_keeps_state_file_byte_identical(self):
        """重载 gpu_coordinator 不得删除/改写 STATE_FILE（导入期恢复已移除）。"""
        payload = {"leases": {"0": {"owner": "old", "purpose": "train"}},
                   "queue": [], "updated_at": 1.0}
        g.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        g.STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        before = g.STATE_FILE.read_bytes()

        importlib.reload(g)

        self.assertTrue(g.STATE_FILE.exists(), "重载不得删除 STATE_FILE")
        self.assertEqual(
            g.STATE_FILE.read_bytes(), before,
            "重载 gpu_coordinator 不得改动 STATE_FILE 字节（导入期不应有磁盘 I/O）",
        )

    def test_import_config_does_not_create_chroma_dir(self):
        """子进程内 import / reload config 都不得创建 CHROMA_DIR。

        用独立子进程验证，避免 reload 污染当前测试进程的 config 全局态。
        """
        root = tempfile.mkdtemp(prefix="docmind_iso_")
        target = os.path.join(root, "chroma_absent")
        code = (
            "import importlib, os, sys;"
            "import config, gpu_coordinator;"          # gpu_coordinator 现 import config
            "assert not os.path.exists(sys.argv[1]), 'import created CHROMA_DIR';"
            "importlib.reload(config);"
            "assert not os.path.exists(sys.argv[1]), 'reload created CHROMA_DIR';"
            "print('ok')"
        )
        env = dict(os.environ)
        env["CHROMA_DIR"] = target
        env["DOCMIND_STATE_ROOT"] = root
        env["DOCMIND_TRACE"] = "0"
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code, target],
            cwd=repo, env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)
        self.assertFalse(os.path.exists(target), "导入 config 不应创建 CHROMA_DIR")

    def test_ensure_dirs_creates_chroma_dir_lazily(self):
        """显式 ensure_dirs() 才创建目录（惰性、幂等）。"""
        with tempfile.TemporaryDirectory() as d:
            target = os.path.join(d, "chroma_made")
            old = config.CHROMA_DIR
            config.CHROMA_DIR = target
            try:
                self.assertFalse(os.path.exists(target))
                config.ensure_dirs()
                self.assertTrue(os.path.isdir(target), "ensure_dirs() 应创建 CHROMA_DIR")
                config.ensure_dirs()  # 幂等，不报错
            finally:
                config.CHROMA_DIR = old


if __name__ == "__main__":
    unittest.main()
