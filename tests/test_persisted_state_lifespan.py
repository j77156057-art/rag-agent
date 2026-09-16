# -*- coding: utf-8 -*-
"""D-追加回归：持久化状态恢复从「导入期」搬到「应用启动期」。

- 过去 `import config` 会立刻调用 `_apply_persisted_state()`（导入即磁盘读）；
- 现在该调用移到 api.py 的 lifespan（紧接 ensure_dirs() 之后、gpu.init() 之前）。

本文件钉死两点：
① 子进程内 `import config` **不再**恢复持久化状态（无导入期磁盘读）；
② `_apply_persisted_state()` 本身仍能正确恢复（说明搬迁后功能等价）；
③ 应用 lifespan 确实调用了它（恰好一次）。
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402


def _write_state(root, payload):
    with open(os.path.join(root, ".docmind_state.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


class ImportNoLongerRestoresTests(unittest.TestCase):
    def test_import_config_does_not_restore_persisted_state(self):
        root = tempfile.mkdtemp(prefix="docmind_pstate_")
        _write_state(root, {
            "code_root": root,
            "context_window_overrides": {"mock/m1": 71000},
            "gpu_idle_unload_seconds": 120.0,
        })
        code = (
            "import config;"
            "assert config.get_context_window_override('mock', 'm1') is None, "
            "'import config 不应再恢复自定义窗口';"
            "assert not config.get_runtime('code_root'), "
            "'import config 不应再恢复 code_root';"
            "print('ok')"
        )
        env = dict(os.environ)
        env["DOCMIND_STATE_ROOT"] = root
        env["DOCMIND_TRACE"] = "0"
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=repo, env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_apply_persisted_state_still_restores(self):
        root = tempfile.mkdtemp(prefix="docmind_pstate_")
        _write_state(root, {
            "code_root": root,
            "context_window_overrides": {"mock/m1": 71000},
            "gpu_idle_unload_seconds": 120.0,
        })
        code = (
            "import sys, config;"
            "config._apply_persisted_state();"
            "assert config.get_context_window_override('mock', 'm1') == 71000, "
            "'显式恢复应生效';"
            "assert (config.get_runtime('code_root') or '') == sys.argv[1], "
            "'显式恢复应生效（有效目录）';"
            "assert config.get_runtime('gpu_idle_unload_seconds') == 120.0;"
            "print('ok')"
        )
        env = dict(os.environ)
        env["DOCMIND_STATE_ROOT"] = root
        env["DOCMIND_TRACE"] = "0"
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code, root],
            cwd=repo, env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)


class LifespanRestoresTests(unittest.TestCase):
    def test_lifespan_invokes_apply_persisted_state_once(self):
        import api
        from starlette.testclient import TestClient

        self.assertIs(api._apply_persisted_state, config._apply_persisted_state)
        calls = []
        orig = api._apply_persisted_state
        api._apply_persisted_state = lambda: calls.append(True)
        try:
            with TestClient(api.app):
                pass
        finally:
            api._apply_persisted_state = orig
        self.assertEqual(len(calls), 1, "应用启动期应恰好调用一次 _apply_persisted_state")


if __name__ == "__main__":
    unittest.main()
