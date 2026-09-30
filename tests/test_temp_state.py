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
import time
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
        now = time.time()
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


    @unittest.skipUnless(os.name == "nt", "改名探针依赖 Windows 的打开句柄语义")
    def test_rename_probe_spares_a_live_profile_and_takes_a_dead_one(self):
        """这条是 `sweep_stale_orphans` 的全部依据：活的 Chromium 目录改不动名。

        第三方目录（playwright 的 user_data_dir）我们插不上归属标记，所以只能靠「改名是否
        成功」证明无人持有。

        断言必须数【内容】而不是看目录在不在：`shutil.rmtree(ignore_errors=True)` 删到被锁
        的文件就会停下，目录照样留着，但没锁的那一半已经被清空了——只断「目录还在」的话，
        把探针拿掉测试也过（实测如此）。这和 `sweep_stale_profiles` 上栽的是同一个坑。
        """
        from agent_runtime import visual_acceptance as visual
        with tempfile.TemporaryDirectory() as sandbox:
            profile = pathlib.Path(sandbox) / "playwright_chromiumdev_profile-live"
            profile.mkdir()
            proc, job, error = visual.spawn_browser_in_job(
                [visual._edge_binary(), *visual._BASE_FLAGS,
                 "--user-data-dir=%s" % str(profile), "about:blank"])
            self.assertIsNotNone(proc, error)
            try:
                time.sleep(4.0)                       # 让 Edge 打开 lockfile / 建好缓存
                entries = lambda: {p.relative_to(profile).as_posix()
                                   for p in profile.rglob("*")}
                before = entries()
                self.assertGreater(len(before), 5,
                                   "profile 没被真正使用，这条什么都没断")
                removed = temp_state.sweep_stale_orphans(sandbox, ("playwright_",), 0.0)
                self.assertEqual(removed, [], "改名探针没能保住正在使用的浏览器目录")
                # 只查「原有文件有没有消失」。Edge 在扫描期间还会新建文件，用集合相等断言
                # 会在正向情形下偶发假红（实测被这样误判过一次）。
                vanished = before - entries()
                self.assertEqual(vanished, set(),
                                 "目录还在，但 %d 个原有文件被删了——部分删除同样是损坏别人：%s"
                                 % (len(vanished), sorted(vanished)[:5]))
            finally:
                visual._close_reaper(job)
                proc.kill()
                proc.wait(timeout=15)
            time.sleep(2.0)
            removed = temp_state.sweep_stale_orphans(sandbox, ("playwright_",), 0.0)
            self.assertEqual([os.path.basename(p) for p in removed],
                             ["playwright_chromiumdev_profile-live"],
                             "浏览器已经没了，探针却仍然拒收——阈值或前缀写错了")
            self.assertFalse(profile.exists())

    def test_stale_orphan_sweep_respects_age_floor_and_prefixes(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as sandbox:
            young = pathlib.Path(sandbox) / "docmind_ac_young"
            old = pathlib.Path(sandbox) / "docmind_ac_old"
            cache = pathlib.Path(sandbox) / "playwright-download-keepme"
            foreign = pathlib.Path(sandbox) / "not_ours_old"
            for d in (young, old, cache, foreign):
                d.mkdir()
                os.utime(d, (now - 3600, now - 3600))   # 一小时前：在 2 小时阈值之内
            os.utime(old, (now - 86400, now - 86400))
            os.utime(cache, (now - 86400, now - 86400))
            os.utime(foreign, (now - 86400, now - 86400))
            removed = [os.path.basename(p) for p in
                       temp_state.sweep_stale_orphans(sandbox, ("docmind_ac_",), 7200.0)]
            self.assertEqual(removed, ["docmind_ac_old"], "年龄阈值与前缀都得生效")
            self.assertTrue(young.is_dir())
            self.assertTrue(cache.is_dir(), "下载缓存必须活着：本机 CDN 拉不到 chromium")
            self.assertTrue(foreign.is_dir())


if __name__ == "__main__":
    unittest.main()
