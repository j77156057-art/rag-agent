# -*- coding: utf-8 -*-
"""`temp_state` 的归属标记与孤儿回收契约。

背景：`config.STATE_ROOT`（测试隔离）与 `dev_eval` 的 fixture 目录建在系统 temp 下，
被硬杀时没人删，实测攒到 1,794 个目录 / 239 MB / 1.9 万个文件。这里锁住回收判据。
"""
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import temp_state  # noqa: E402


class TempStateTests(unittest.TestCase):
    def test_pid_alive_matrix(self):
        """六个方向都要对：判不出来时算活着，只有「找不到」才算死。"""
        exited = subprocess.Popen([sys.executable, "-c", "pass"])
        exited.wait(timeout=30)
        running = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])
        try:
            self.assertTrue(temp_state.pid_alive(os.getpid()))
            self.assertTrue(temp_state.pid_alive(running.pid))
            self.assertFalse(temp_state.pid_alive(exited.pid))
            self.assertTrue(temp_state.pid_alive(0), "pid 0 不许猜，按活着处理")
            self.assertFalse(temp_state.pid_alive(4_000_000))
        finally:
            running.kill()
            running.wait(timeout=10)

    def test_sweep_needs_the_owner_pid_to_be_gone(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=30)
        now = __import__("time").time()
        with tempfile.TemporaryDirectory() as root:
            def make(name, owner):
                path = pathlib.Path(root) / name
                path.mkdir()
                if owner is not None:
                    (path / temp_state.OWNER_FILE).write_text(str(owner), encoding="ascii")
                # mtime 一律压到一天前：证明回收不看时间，只看归属
                os.utime(path, (now - 86400, now - 86400))
                return path

            abandoned = make("docmind_test_state_aaaaaaaa", dead.pid)
            in_use = make("docmind_test_state_bbbbbbbb", os.getpid())
            unmarked = make("docmind_test_state_cccccccc", None)
            foreign = make("someone_elses_state_dddddddd", dead.pid)

            removed = [os.path.basename(p) for p in
                       temp_state.sweep_orphans(root, ("docmind_test_state_",))]

            self.assertEqual(removed, ["docmind_test_state_aaaaaaaa"])
            self.assertTrue(in_use.is_dir(), "主人还活着就不能删")
            self.assertTrue(unmarked.is_dir(), "没有归属标记 = 判断不了归谁，一律不碰")
            self.assertTrue(foreign.is_dir(), "前缀不匹配一律不碰")
            self.assertEqual(temp_state.sweep_orphans(os.path.join(root, "nope"),
                                                      ("docmind_test_state_",)), [])

    def test_claim_makes_a_directory_attributable(self):
        with tempfile.TemporaryDirectory() as root:
            target = pathlib.Path(root) / "docmind_test_state_x"
            target.mkdir()
            self.assertIsNone(temp_state.owner_of(target))
            temp_state.claim(target)
            self.assertEqual(temp_state.owner_of(target), os.getpid())

    def test_release_reports_whether_the_directory_actually_went_away(self):
        with tempfile.TemporaryDirectory() as root:
            target = pathlib.Path(root) / "gone"
            target.mkdir()
            (target / "f.txt").write_text("x", encoding="utf-8")
            self.assertTrue(temp_state.release(target))
            self.assertFalse(target.exists())
            self.assertTrue(temp_state.release(str(target)), "重复释放不得报错")
            target.mkdir()
            lock = target / "lock.bin"
            lock.write_text("x", encoding="utf-8")
            handle = open(lock, "rb")
            try:
                released = temp_state.release(target)
                if os.name == "nt":
                    self.assertFalse(released, "目录被占用时 release 必须如实报 False")
            finally:
                handle.close()

    def test_config_registers_an_owner_and_cleans_its_own_state_root(self):
        """跑测试时 config 会自建 STATE_ROOT：它必须带归属，且进程退出时被回收。

        端到端：起一个子进程 import config，读它拿到的 STATE_ROOT 与归属标记；子进程自然
        退出后再看目录有没有真的消失。atexit 没注册、或注册错了路径，这里都会红。

        子进程必须先 `import pytest`：`config` 是按「pytest 在不在 sys.modules」判定测试
        隔离的，裸 `python -c` 不满足，那样 STATE_ROOT 压根不会被创建，这条会静默空转。
        """
        code = (
            "import sys, json; sys.path.insert(0, r%r);"
            "import pytest;"
            "import temp_state, config;"
            "from pathlib import Path;"
            "root = str(config.STATE_ROOT);"
            "print(json.dumps({'root': root, 'owner': temp_state.owner_of(Path(root)),"
            " 'isolated': bool(config._TEST_STATE_ISOLATED)}))" % str(REPO)
        )
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, timeout=180, cwd=str(REPO))
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        info = json.loads(out.stdout.strip().splitlines()[-1])
        self.assertTrue(info["isolated"],
                        "子进程没进入测试隔离状态，这条会静默空转——判据本身要能红")
        self.assertIsNotNone(info["owner"], "STATE_ROOT 必须写上归属 PID")
        self.assertFalse(pathlib.Path(info["root"]).exists(),
                         "子进程已正常退出，它的 STATE_ROOT 应当被 atexit 回收")


if __name__ == "__main__":
    unittest.main()
