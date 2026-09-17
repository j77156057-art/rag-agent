# -*- coding: utf-8 -*-
"""P1 引擎单实例策略 · QA 独立验证（与实现者的 test_engine_single_instance.py 互补）。

目的：不复用实现者的断言，自行构造证据覆盖
① engine_running_roots()：空 → []；存活+已死 pid → 只返回存活者且清理已死记录；
② /api/engine/start：切换前停掉"非目标 root"、可重复调用不炸；同一 root 重复 start 不误停；
③ /api/ingest_code：切项目前的停引擎路径（monkeypatch，绝不真起引擎/真 ingest）；
④ 对抗：engine_stop 抛异常时的行为（记录真实结论，非假设）。

全部离线。仅在 tests/ 下新增，不改任何源码。
"""
import asyncio
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
import game_workbench as gw  # noqa: E402


class _FakeProc:
    def __init__(self, alive):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


def _run(coro):
    return asyncio.run(coro)


class EngineRunningRootsQATests(unittest.TestCase):
    def setUp(self):
        self._old_procs = gw._ENGINE_PROCS
        self._old_embed = gw._EMBED_STATE

    def tearDown(self):
        gw._ENGINE_PROCS = self._old_procs
        gw._EMBED_STATE = self._old_embed

    def test_empty_engine_returns_empty_list(self):
        gw._ENGINE_PROCS = {}
        gw._EMBED_STATE = {}
        self.assertEqual(gw.engine_running_roots(), [])

    def test_alive_and_dead_mixed_only_alive_sorted(self):
        gw._ENGINE_PROCS = {
            "C:/p/zeta": _FakeProc(True),
            "C:/p/dead": _FakeProc(False),
            "C:/p/alpha": _FakeProc(True),
        }
        gw._EMBED_STATE = {}
        got = gw.engine_running_roots()
        self.assertEqual(got, ["C:/p/alpha", "C:/p/zeta"])   # 只存活 + 字典序
        self.assertNotIn("C:/p/dead", gw._ENGINE_PROCS)      # 已死记录被清理

    def test_none_proc_entry_is_ignored_and_cleaned(self):
        gw._ENGINE_PROCS = {"C:/p/none": None, "C:/p/live": _FakeProc(True)}
        gw._EMBED_STATE = {}
        self.assertEqual(gw.engine_running_roots(), ["C:/p/live"])
        self.assertNotIn("C:/p/none", gw._ENGINE_PROCS)


class EngineStartQATests(unittest.TestCase):
    def _patch(self, running, start_ret):
        self.stops = []

        def fake_stop(r):
            self.stops.append(r)
            return {"ok": True, "stopped": True}

        return [
            mock.patch.object(api, "_project_root_or_error", return_value="/proj/b"),
            mock.patch.object(api, "engine_running_roots", return_value=running),
            mock.patch.object(api, "engine_stop", side_effect=fake_stop),
            mock.patch.object(api, "engine_start", return_value=start_ret),
        ]

    def test_stops_other_roots_and_reports_notice(self):
        patches = self._patch(["/proj/a", "/proj/b", "/proj/c"], {"ok": True, "running": True, "pid": 1})
        for p in patches:
            p.start()
        try:
            res = _run(api.engine_start_ep(api.EngineReq()))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(sorted(self.stops), ["/proj/a", "/proj/c"])   # 目标 /proj/b 不停
        self.assertEqual(sorted(res.get("auto_stopped_roots", [])), ["/proj/a", "/proj/c"])
        self.assertIn("已自动停止其它项目的引擎", res.get("notice", ""))
        self.assertTrue(res.get("running"))

    def test_repeatable_multiple_calls_no_crash(self):
        patches = self._patch(["/proj/a", "/proj/b"], {"ok": True, "running": True})
        for p in patches:
            p.start()
        try:
            r1 = _run(api.engine_start_ep(api.EngineReq()))
            r2 = _run(api.engine_start_ep(api.EngineReq()))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(sorted(self.stops), ["/proj/a", "/proj/a"])   # 每次调用各停一次
        self.assertTrue(r1.get("ok") and r2.get("ok"))

    def test_same_root_not_stopped(self):
        patches = self._patch(["/proj/b"], {"ok": True, "running": True})
        for p in patches:
            p.start()
        try:
            res = _run(api.engine_start_ep(api.EngineReq()))
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(self.stops, [])          # 目标 root 自身不被停
        self.assertNotIn("notice", res)

    def test_engine_stop_exception_does_not_abort_start(self):
        """更新语义（实现者加容错后）：engine_stop 抛异常 → 捕获为 warning，engine_start 照常执行。"""
        started = []

        def boom(_r):
            raise RuntimeError("stop failed")

        with mock.patch.object(api, "_project_root_or_error", return_value="/proj/b"), \
             mock.patch.object(api, "engine_running_roots", return_value=["/proj/a"]), \
             mock.patch.object(api, "engine_stop", side_effect=boom), \
             mock.patch.object(api, "engine_start",
                               side_effect=lambda *_a, **_k: started.append(1) or {"ok": True, "running": True}):
            res = _run(api.engine_start_ep(api.EngineReq()))
        self.assertEqual(started, [1])                        # 启动照常执行
        self.assertTrue(res.get("running"))
        self.assertEqual(res.get("auto_stopped_roots"), [])   # 没停成功
        self.assertIn("未能停止", res.get("notice", ""))       # 记入 warning
        self.assertIn("/proj/a", res.get("notice", ""))

    def test_partial_stop_failure_keeps_going(self):
        """部分 root 停失败：成功的记 stopped、失败的记 warning，启动仍执行。"""
        def stop(r):
            if r == "/proj/bad":
                raise RuntimeError("nope")
            return {"ok": True}

        with mock.patch.object(api, "_project_root_or_error", return_value="/proj/b"), \
             mock.patch.object(api, "engine_running_roots", return_value=["/proj/ok", "/proj/bad"]), \
             mock.patch.object(api, "engine_stop", side_effect=stop), \
             mock.patch.object(api, "engine_start", return_value={"ok": True, "running": True}):
            res = _run(api.engine_start_ep(api.EngineReq()))
        self.assertEqual(res.get("auto_stopped_roots"), ["/proj/ok"])
        self.assertIn("已自动停止其它项目的引擎：/proj/ok", res.get("notice", ""))
        self.assertIn("未能停止 /proj/bad", res.get("notice", ""))


class IngestCodeQA(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="qa_ingest_")
        self.stops = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ingest_code_stops_other_engine_before_switch(self):
        def fake_stop(r):
            self.stops.append(r)
            return {"ok": True}

        with mock.patch.object(api, "engine_running_roots", return_value=["/other/proj", self.tmp]), \
             mock.patch.object(api, "engine_stop", side_effect=fake_stop), \
             mock.patch.object(api, "reset_collection", return_value=None), \
             mock.patch.object(api, "ingest_code_directory", return_value=7), \
             mock.patch.object(api, "load_project_rules", return_value=""), \
             mock.patch.object(api, "set_runtime", return_value=None), \
             mock.patch.object(api, "save_state", return_value=None), \
             mock.patch.object(api, "count", return_value=7), \
             mock.patch.object(api, "agent", mock.Mock(history=[1, 2, 3])):
            res = _run(api.ingest_code(root=self.tmp))

        self.assertTrue(res.get("ok"))
        self.assertEqual(self.stops, ["/other/proj"])        # 只停非新项目
        self.assertEqual(res.get("auto_stopped_roots"), ["/other/proj"])
        self.assertIn("已自动停止其它项目的引擎", res.get("notice", ""))
        self.assertEqual(res.get("chunks"), 7)

    def test_ingest_code_target_root_not_stopped(self):
        def fake_stop(r):
            self.stops.append(r)
            return {"ok": True}

        with mock.patch.object(api, "engine_running_roots", return_value=[self.tmp]), \
             mock.patch.object(api, "engine_stop", side_effect=fake_stop), \
             mock.patch.object(api, "reset_collection", return_value=None), \
             mock.patch.object(api, "ingest_code_directory", return_value=1), \
             mock.patch.object(api, "load_project_rules", return_value=""), \
             mock.patch.object(api, "set_runtime", return_value=None), \
             mock.patch.object(api, "save_state", return_value=None), \
             mock.patch.object(api, "count", return_value=1), \
             mock.patch.object(api, "agent", mock.Mock(history=[])):
            res = _run(api.ingest_code(root=self.tmp))

        self.assertEqual(self.stops, [])                     # 目标是新项目，不自停
        self.assertEqual(res.get("notice"), "")

    def test_ingest_code_survives_engine_stop_failure(self):
        """engine_stop 抛异常不阻断切项目：仍完成切项目，warning 进 notice。"""
        def boom(_r):
            raise RuntimeError("stop failed")

        with mock.patch.object(api, "engine_running_roots", return_value=["/other/proj"]), \
             mock.patch.object(api, "engine_stop", side_effect=boom), \
             mock.patch.object(api, "reset_collection", return_value=None), \
             mock.patch.object(api, "ingest_code_directory", return_value=5), \
             mock.patch.object(api, "load_project_rules", return_value=""), \
             mock.patch.object(api, "set_runtime", return_value=None), \
             mock.patch.object(api, "save_state", return_value=None), \
             mock.patch.object(api, "count", return_value=5), \
             mock.patch.object(api, "agent", mock.Mock(history=[])):
            res = _run(api.ingest_code(root=self.tmp))

        self.assertTrue(res.get("ok"))
        self.assertEqual(res.get("chunks"), 5)
        self.assertIn("未能停止 /other/proj", res.get("notice", ""))


if __name__ == "__main__":
    unittest.main()
