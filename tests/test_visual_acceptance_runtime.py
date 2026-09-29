# -*- coding: utf-8 -*-
"""正式开发舱视觉适配器与预览证据链的离线契约测试。"""
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_runtime.preview_adapters import build_preview_bundle
from agent_runtime.visual_acceptance import (VisualAcceptanceError, _entry_file,
                                             _reap_with_parent, _close_reaper,
                                             sweep_stale_profiles)


def _pid_alive(pid: int) -> bool:
    """只读地判断 PID 是否存在。Windows 上 os.kill(pid, 0) 会直接终止目标进程，不能用。"""
    probe = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                           capture_output=True, text=True, timeout=10)
    return "\n%d\t" % pid in probe.stdout


class VisualAcceptanceRuntimeTests(unittest.TestCase):
    def test_entry_file_is_project_scoped(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = os.path.abspath(root)
            with open(os.path.join(root_path, "index.html"), "w", encoding="utf-8") as handle:
                handle.write("<h1>preview</h1>")
            self.assertTrue(_entry_file(__import__("pathlib").Path(root_path)).name == "index.html")
            with self.assertRaises(VisualAcceptanceError):
                _entry_file(__import__("pathlib").Path(root_path), "../outside.html")

    def test_tool_report_registers_screenshot_artifact(self):
        import tools

        report = {
            "passed": True,
            "image": "aGVsbG8=",
            "screenshot": ".docmind/visual-evidence/run/preview.png",
            "checks": {"page_loaded": True},
            "artifacts": [{
                "id": "visual-preview", "kind": "image",
                "path": ".docmind/visual-evidence/run/preview.png",
                "label": "真实浏览器预览截图",
            }],
        }
        with patch.object(tools, "_get_code_root", return_value="C:/project"), \
             patch("agent_runtime.visual_acceptance.capture_project_preview", return_value=report):
            result = tools.preview_project("entry: index.html")
        self.assertTrue(result.ok)
        self.assertEqual(result.data["images"], ["aGVsbG8="])
        self.assertEqual(result.artifacts[0]["kind"], "image")

    def test_failed_report_without_image_stays_a_tool_failure(self):
        import tools

        report = {
            "passed": False,
            "checks": {"page_loaded": False},
            "runtime_errors": ["页面脚本异常"],
            "artifacts": [],
        }
        with (
            patch.object(tools, "_get_code_root", return_value="C:/project"),
            patch("agent_runtime.visual_acceptance.capture_project_preview", return_value=report),
        ):
            result = tools.preview_project("entry: index.html")
        self.assertFalse(result.ok)
        self.assertEqual(result.error_kind, "visual_acceptance_failed")
        self.assertEqual(result.data["images"], [])
        self.assertIn("未通过", result.text)

    def test_workflow_preview_keeps_visual_artifact(self):
        bundle = build_preview_bundle({
            "workflow_id": "wf-visual", "status": "completed",
            "review": {"ok": True}, "results": {"results": {
                "tester": {"status": "ok", "artifacts": [{
                    "id": "visual-preview", "kind": "image",
                    "path": ".docmind/visual-evidence/run/preview.png",
                    "label": "真实浏览器预览截图",
                }]},
            }},
        })
        self.assertEqual(bundle["counts"]["by_kind"]["image"], 1)
        self.assertEqual(bundle["artifacts"][0]["adapter"], "generic")

    @unittest.skipUnless(os.name == "nt", "作业对象是 Windows 机制")
    def test_reaper_reaps_the_whole_tree_when_the_owner_dies(self):
        """父进程被硬杀时，浏览器进程树必须由操作系统回收。

        这条断的是 462 个 headless Edge 的真实成因：Agent 会话被杀，`finally` 跑不到，
        而 taskkill 恰好就写在 `finally` 里。测的是机制本身，所以不起真浏览器。
        """
        sleeper = "import time;time.sleep(%d)" % 120
        with tempfile.TemporaryDirectory() as mailbox:
            pidfile = os.path.join(mailbox, "child.txt")
            parent = subprocess.Popen(
                [sys.executable, "-c",
                 "import subprocess,sys,time;"
                 "gp=subprocess.Popen([sys.executable,'-c',%r]);open(%r,'w').write(str(gp.pid));"
                 "time.sleep(120)" % (sleeper, pidfile)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            job = _reap_with_parent(parent)
            self.assertIsNotNone(job, "作业对象没绑上——兜底 taskkill 之外就没有第二道保险了")
            grandchild = None
            try:
                deadline = time.time() + 10
                while not os.path.exists(pidfile) and time.time() < deadline:
                    time.sleep(0.05)
                grandchild = int(pathlib.Path(pidfile).read_text() or "0")
                self.assertIsNone(parent.poll(), "子进程应当还活着，否则这条测试什么都没断")
                _close_reaper(job)
                job = None
                deadline = time.time() + 15
                while parent.poll() is None and time.time() < deadline:
                    time.sleep(0.1)
                self.assertIsNotNone(parent.poll(), "关闭作业句柄后浏览器进程没有跟着退出")
                deadline = time.time() + 15
                while _pid_alive(grandchild) and time.time() < deadline:
                    time.sleep(0.2)
                self.assertFalse(_pid_alive(grandchild),
                                 "孙进程不在作业里，Chromium 的 renderer/gpu 子进程就是这么漏掉的")
            finally:
                if job is not None:
                    _close_reaper(job)
                for proc in (parent,):
                    if proc.poll() is None:
                        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       timeout=10)

    @unittest.skipUnless(os.name == "nt", "复现的是 Edge 在 Windows 上的退出文件锁")
    def test_profile_release_survives_the_lock_edge_leaves_on_the_way_out(self):
        """`finally` 跑到了也可能没删干净，所以删 profile 必须自己重试。

        正常退出实测 10/10 次留下目录：Edge 还锁着自己的文件我们就开始 rmtree，而
        `TemporaryDirectory(ignore_cleanup_errors=True)` 只是不抛异常，不等于删掉了。
        """
        from agent_runtime.visual_acceptance import _release_profile
        with tempfile.TemporaryDirectory() as outer:
            profile = tempfile.TemporaryDirectory(dir=outer, ignore_cleanup_errors=True)
            lockfile = os.path.join(profile.name, "leveldb.lock")
            pathlib.Path(lockfile).write_text("x")
            handle = open(lockfile, "rb")
            try:
                self.assertFalse(_release_profile(profile), "文件被占用时应当如实报告没删净")
                self.assertTrue(os.path.isdir(profile.name))
            finally:
                handle.close()
            self.assertTrue(_release_profile(profile))
            self.assertFalse(os.path.isdir(profile.name))

    def test_profile_sweep_touches_only_our_own_stale_directories(self):
        now = time.time()
        with tempfile.TemporaryDirectory() as root:
            stale = os.path.join(root, "docmind-visual-preview-aaaaaaaa")
            fresh = os.path.join(root, "docmind-visual-preview-bbbbbbbb")
            foreign = os.path.join(root, "someone-else-preview-cccccccc")
            not_a_dir = os.path.join(root, "docmind-visual-preview-dddddddd.txt")
            for path in (stale, fresh, foreign):
                os.mkdir(path)
                os.utime(path, (now - 24 * 3600, now - 24 * 3600))
            os.utime(fresh, (now, now))
            pathlib.Path(not_a_dir).write_text("x")
            os.utime(not_a_dir, (now - 24 * 3600, now - 24 * 3600))

            removed = sweep_stale_profiles(root)

            self.assertEqual([os.path.basename(path) for path in removed],
                             ["docmind-visual-preview-aaaaaaaa"])
            self.assertTrue(os.path.isdir(fresh), "并发同事正在用的 profile 不能被删")
            self.assertTrue(os.path.isdir(foreign), "不是我们的前缀一律不碰")
            self.assertTrue(os.path.isfile(not_a_dir))
            self.assertEqual(sweep_stale_profiles(os.path.join(root, "nope")), [])


if __name__ == "__main__":
    unittest.main()
