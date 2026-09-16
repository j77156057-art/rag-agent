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


class ExplicitCodeRootPrecedenceTests(unittest.TestCase):
    """P1 回归：显式设置的 code_root 不得被持久化状态覆盖。

    `_apply_persisted_state()` 从 import 期搬到 lifespan 后，演示/校验脚本
    「先 set_runtime('code_root', …) 再启动应用」的顺序被打破：lifespan 里的无条件
    恢复会冲掉脚本显式指定的临时工程。这里钉死「显式设置优先于持久化恢复」。

    隔离方式沿用本文件既有做法：子进程 + DOCMIND_STATE_ROOT 指向临时目录，
    绝不触碰仓库根的 STATE_FILE。
    """

    def test_explicit_code_root_not_clobbered_by_persisted_state(self):
        # 目录 A：持久化状态里记录的 code_root（有效目录）
        state_root = tempfile.mkdtemp(prefix="docmind_pstate_")
        dir_a = tempfile.mkdtemp(prefix="docmind_root_a_")
        # 目录 B：调用方在启动前显式设置的 code_root（有效目录，且 A != B）
        dir_b = tempfile.mkdtemp(prefix="docmind_root_b_")
        self.assertNotEqual(os.path.realpath(dir_a), os.path.realpath(dir_b))
        _write_state(state_root, {"code_root": dir_a})

        code = (
            "import sys, config;"
            "config.set_runtime('code_root', sys.argv[1]);"
            "config._apply_persisted_state();"
            "assert config.get_runtime('code_root') == sys.argv[1], "
            "'显式设置的 code_root 不应被持久化状态覆盖';"
            "print('ok')"
        )
        env = dict(os.environ)
        env["DOCMIND_STATE_ROOT"] = state_root
        env["DOCMIND_TRACE"] = "0"
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code, dir_b],
            cwd=repo, env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_code_root_still_restored_when_not_preset(self):
        # 正向对照：未预设时报文仍能从状态文件恢复（_RUNTIME 先清掉 code_root）
        state_root = tempfile.mkdtemp(prefix="docmind_pstate_")
        dir_a = tempfile.mkdtemp(prefix="docmind_root_a_")
        _write_state(state_root, {"code_root": dir_a})

        code = (
            "import sys, config;"
            "config._RUNTIME.pop('code_root', None);"
            "config._apply_persisted_state();"
            "assert (config.get_runtime('code_root') or '') == sys.argv[1], "
            "'未预设时应从状态文件恢复 code_root';"
            "print('ok')"
        )
        env = dict(os.environ)
        env["DOCMIND_STATE_ROOT"] = state_root
        env["DOCMIND_TRACE"] = "0"
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code, dir_a],
            cwd=repo, env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
