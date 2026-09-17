# -*- coding: utf-8 -*-
"""引擎单实例策略（P1）回归测试。

背景：切项目 / 启动新引擎时，旧项目的引擎进程仍在后台跑（占 GPU、UI 找不到它，
变成"孤儿"）。修复新增 game_workbench.engine_running_roots()，并在
/api/engine/start 与 /api/ingest_code 中于切换前停掉"非目标 root"的引擎。

覆盖（全部离线，不真起引擎）：
① engine_running_roots() 无引擎时返回 []；
② engine_running_roots() 只返回存活进程（已死 pid 被剔除并清理）；
③ engine_start_ep 在其它 root 正在跑时会调用 engine_stop 停掉它们，并返回提示；
④ engine_start_ep 对"目标 root 自身"不重复 stop（不误伤）。
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
import game_workbench as gw  # noqa: E402


class _FakeProc:
    """最小进程替身：poll() 返回 None=存活，其它值=已退出。"""

    def __init__(self, alive: bool):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


class EngineRunningRootsTests(unittest.TestCase):
    def setUp(self):
        self._old_procs = gw._ENGINE_PROCS
        self._old_embed = gw._EMBED_STATE

    def tearDown(self):
        gw._ENGINE_PROCS = self._old_procs
        gw._EMBED_STATE = self._old_embed

    def test_no_engine_returns_empty(self):
        gw._ENGINE_PROCS = {}
        gw._EMBED_STATE = {}
        self.assertEqual(gw.engine_running_roots(), [])

    def test_only_alive_roots_returned(self):
        gw._ENGINE_PROCS = {
            "C:/proj/live": _FakeProc(True),
            "C:/proj/dead": _FakeProc(False),
        }
        gw._EMBED_STATE = {}
        self.assertEqual(gw.engine_running_roots(), ["C:/proj/live"])
        # engine_reap_dead() 顺带把已死的记录清理出表
        self.assertNotIn("C:/proj/dead", gw._ENGINE_PROCS)


class EngineStartSingleInstanceTests(unittest.TestCase):
    def test_start_stops_other_roots(self):
        calls = []

        def fake_stop(r):
            calls.append(r)
            return {"ok": True, "stopped": True}

        def fake_start(*_a, **_k):
            return {"ok": True, "running": True, "pid": 1234}

        with mock.patch.object(api, "_project_root_or_error", return_value="/proj/b"), \
             mock.patch.object(api, "engine_running_roots", return_value=["/proj/a", "/proj/b"]), \
             mock.patch.object(api, "engine_stop", side_effect=fake_stop), \
             mock.patch.object(api, "engine_start", side_effect=fake_start):
            res = asyncio.run(api.engine_start_ep(api.EngineReq()))

        self.assertEqual(calls, ["/proj/a"])  # 只停非目标 root
        self.assertEqual(res.get("auto_stopped_roots"), ["/proj/a"])
        self.assertIn("已自动停止其它项目的引擎", res.get("notice", ""))
        self.assertTrue(res.get("running"))

    def test_start_does_not_stop_target_root(self):
        calls = []

        def fake_stop(r):
            calls.append(r)
            return {"ok": True}

        with mock.patch.object(api, "_project_root_or_error", return_value="/proj/b"), \
             mock.patch.object(api, "engine_running_roots", return_value=["/proj/b"]), \
             mock.patch.object(api, "engine_stop", side_effect=fake_stop), \
             mock.patch.object(api, "engine_start", return_value={"ok": True, "running": True}):
            res = asyncio.run(api.engine_start_ep(api.EngineReq()))

        self.assertEqual(calls, [])          # 目标 root 不重复 stop（由 engine_start 自身处理）
        self.assertNotIn("notice", res)      # 没有停别的引擎 → 无提示


if __name__ == '__main__':
    unittest.main()
