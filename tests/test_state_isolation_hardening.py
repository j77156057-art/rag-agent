# -*- coding: utf-8 -*-
"""R3 + R4 回归：测试隔离判据收紧 + 隔离模式下相对 env 路径以 STATE_ROOT 为基准。

R3：`_running_under_unittest()` 只在真正的 `python -m unittest` 进程成立；
    `python -c "import unittest, config"` 这类非测试进程不得被隔离。
R4：隔离生效时，相对路径 env（CHROMA_DIR 等）应挂到 STATE_ROOT 下，而非相对 cwd。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_R4_PROBE_SRC = """# -*- coding: utf-8 -*-
import os
import unittest
import config


class Probe(unittest.TestCase):
    def test_probe(self):
        config.ensure_dirs()
        print("R4_ROOT=", config.STATE_ROOT)
        print("R4_CHROMA=", config.CHROMA_DIR)
        print("R4_REPO_CHROMA=", os.path.join(config.BASE_DIR, ".chroma"))
"""


def _under(path, root) -> bool:
    path = os.path.abspath(str(path))
    root = os.path.abspath(str(root))
    return path == root or path.startswith(root + os.sep)


def _extract(text, prefix):
    for line in text.splitlines():
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    raise AssertionError(f"未找到 {prefix}\n--- output ---\n{text}")


class R3JudgeTests(unittest.TestCase):
    def test_plain_import_unittest_not_treated_as_test_process(self):
        # python -c "import unittest, config" 不是 -m unittest 进程 → 不应被隔离
        env = dict(os.environ)
        env.pop("DOCMIND_STATE_ROOT", None)
        code = ("import unittest, config;"
                "print('SAME=', config.STATE_ROOT == config.BASE_DIR)")
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=_REPO, env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SAME= True", proc.stdout,
                      "import unittest 的普通进程不应被判定为测试进程")


class R4StatePathTests(unittest.TestCase):
    def test_state_path_rebases_relative_when_isolated(self):
        old_flag, old_root = config._TEST_STATE_ISOLATED, config.STATE_ROOT
        old_env = os.environ.get("CHROMA_DIR")
        try:
            config._TEST_STATE_ISOLATED = True
            config.STATE_ROOT = os.path.join(tempfile.gettempdir(), "dm_r4_root")
            os.environ["CHROMA_DIR"] = "./.chroma"
            self.assertEqual(
                config.state_path("CHROMA_DIR", "DEF"),
                os.path.join(config.STATE_ROOT, "./.chroma"))
        finally:
            config._TEST_STATE_ISOLATED = old_flag
            config.STATE_ROOT = old_root
            if old_env is None:
                os.environ.pop("CHROMA_DIR", None)
            else:
                os.environ["CHROMA_DIR"] = old_env

    def test_state_path_keeps_absolute_when_isolated(self):
        old_flag = config._TEST_STATE_ISOLATED
        old_env = os.environ.get("CHROMA_DIR")
        try:
            config._TEST_STATE_ISOLATED = True
            os.environ["CHROMA_DIR"] = os.path.join("Z:", "abs", ".chroma") if os.name == "nt" else "/abs/.chroma"
            self.assertEqual(config.state_path("CHROMA_DIR", "DEF"), os.environ["CHROMA_DIR"])
        finally:
            config._TEST_STATE_ISOLATED = old_flag
            if old_env is None:
                os.environ.pop("CHROMA_DIR", None)
            else:
                os.environ["CHROMA_DIR"] = old_env

    def test_state_path_keeps_relative_when_not_isolated(self):
        old_flag = config._TEST_STATE_ISOLATED
        old_env = os.environ.get("CHROMA_DIR")
        try:
            config._TEST_STATE_ISOLATED = False
            os.environ["CHROMA_DIR"] = "./.chroma"
            self.assertEqual(config.state_path("CHROMA_DIR", "DEF"), "./.chroma")
        finally:
            config._TEST_STATE_ISOLATED = old_flag
            if old_env is None:
                os.environ.pop("CHROMA_DIR", None)
            else:
                os.environ["CHROMA_DIR"] = old_env

    def test_relative_env_rebased_to_state_root_in_isolated_process(self):
        # 真·隔离进程：CHROMA_DIR=./.chroma 应落到该进程的 STATE_ROOT 下，而非仓库根
        tmp = tempfile.mkdtemp(prefix="docmind_r4mod_")
        try:
            with open(os.path.join(tmp, "r4probe.py"), "w", encoding="utf-8") as f:
                f.write(_R4_PROBE_SRC)
            env = dict(os.environ)
            env.pop("DOCMIND_STATE_ROOT", None)   # 隔离生效前提
            env["CHROMA_DIR"] = "./.chroma"
            env["PYTHONPATH"] = tmp + os.pathsep + env.get("PYTHONPATH", "")
            proc = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "r4probe"],
                cwd=_REPO, env=env, capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            root = _extract(proc.stdout, "R4_ROOT=")
            chroma = _extract(proc.stdout, "R4_CHROMA=")
            self.assertTrue(_under(chroma, root),
                            f"隔离进程的 CHROMA_DIR({chroma}) 应落在 STATE_ROOT({root}) 下")
            self.assertNotEqual(os.path.abspath(chroma), os.path.join(_REPO, ".chroma"),
                                "隔离进程不得把相对 CHROMA_DIR 解析到仓库根")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class R5PytestIsolationTests(unittest.TestCase):
    """R5：pytest 会话同样必须隔离 STATE_ROOT。

    历史事故：隔离只判 `python -m unittest`，pytest 跑 test_api_model_config.py
    时把 mock/zz-unit-ctx 等夹具值写进真实 .docmind_state.json，用户重启后云端
    模型被打回离线 mock。
    """

    _PROBE_SRC = """# -*- coding: utf-8 -*-
import config

def test_pytest_state_root_isolated():
    assert config._running_under_pytest() is True
    assert config._TEST_STATE_ISOLATED is True
    assert config.STATE_ROOT != config.BASE_DIR
    print("R5_ROOT=" + config.STATE_ROOT)
"""

    def test_pytest_process_uses_isolated_state_root(self):
        tmp = tempfile.mkdtemp(prefix="docmind_r5pytest_")
        probe = os.path.join(tmp, "test_r5_probe.py")
        try:
            with open(probe, "w", encoding="utf-8") as f:
                f.write(self._PROBE_SRC)
            env = dict(os.environ)
            env.pop("DOCMIND_STATE_ROOT", None)
            env.pop("DOCMIND_NO_TEST_ISOLATION", None)
            env["PYTHONPATH"] = _REPO + os.pathsep + env.get("PYTHONPATH", "")
            proc = subprocess.run(
                [sys.executable, "-B", "-m", "pytest", "-q", "-s", probe],
                cwd=_REPO, env=env, capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            root = _extract(proc.stdout, "R5_ROOT=")
            self.assertNotEqual(os.path.abspath(root), _REPO,
                                "pytest 进程不得把 STATE_ROOT 留在仓库根")
            self.assertNotEqual(os.path.abspath(root), config.BASE_DIR)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_plain_import_config_not_treated_as_pytest(self):
        # 普通 python 进程（无 pytest）不得命中 pytest 隔离判据
        env = dict(os.environ)
        env.pop("DOCMIND_STATE_ROOT", None)
        code = "import config; print('PYTEST_FLAG=', config._running_under_pytest())"
        proc = subprocess.run(
            [sys.executable, "-B", "-c", code],
            cwd=_REPO, env=env, capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("PYTEST_FLAG= False", proc.stdout)


if __name__ == "__main__":
    unittest.main()
