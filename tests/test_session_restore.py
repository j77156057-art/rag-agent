# -*- coding: utf-8 -*-
"""会话历史回灌的回归测试（全部离线，不触网）。

背景：开发台（/workbench）重开后 AI 助手对话为空。后端其实已把每轮问答落盘，
断链在前端；修复新增了 `GET /api/sessions/{session_id}` 供前端回灌历史。
本测试守住这个新端点，并防止它把同路径的 DELETE 覆盖掉（本项目有「同路径同方法
重复注册会静默覆盖」的历史坑，见 test_api_routes.py）。

覆盖：
① `GET /api/sessions/{id}` 对不存在的 id 返回 ok=True + 空 turns（不是 404）；
② 先用 sessions.save() 造一段历史，再 GET 能取回长度与内容都对的 turns；
③ `DELETE /api/sessions/{id}` 仍然可用（未被 GET 覆盖），删除后 GET 变空；
④ 路由表里 GET / DELETE 同路径共存且各注册一次。
"""
import collections
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import api  # noqa: E402
import sessions  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


def _routes():
    out = []
    for route in api.app.routes:
        path = getattr(route, 'path', '')
        if not path:
            continue
        for method in sorted(getattr(route, 'methods', []) or []):
            if method in ('HEAD', 'OPTIONS'):
                continue
            out.append((path, method))
    return out


class SessionRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_sess_restore_")
        self._old_dir = sessions.SESSIONS_DIR
        sessions.SESSIONS_DIR = os.path.join(self.tmp, "sessions")
        self.client = TestClient(api.app)
        self.sid = "web-test-restore"

    def tearDown(self):
        sessions.SESSIONS_DIR = self._old_dir

    def test_get_missing_session_returns_empty_not_404(self):
        """不存在的会话返回 200 + ok=True + 空 turns（前端据此静默处理）。"""
        resp = self.client.get(f"/api/sessions/{self.sid}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("session_id"), self.sid)
        self.assertEqual(body.get("turns"), [])
        self.assertEqual(body.get("summary"), "")

    def test_get_returns_saved_turns(self):
        """造一段历史后 GET 能取回，长度与内容对得上。"""
        turns = [
            {"user": "玩家受伤数值在哪？", "assistant": "在 player_stats.gd 的 max_health。"},
            {"user": "那受击逻辑呢？", "assistant": "见 damage_calc.gd::take_damage。"},
        ]
        self.assertTrue(sessions.save(self.sid, turns, "早期摘要"))

        resp = self.client.get(f"/api/sessions/{self.sid}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("session_id"), self.sid)
        got = body.get("turns") or []
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["user"], turns[0]["user"])
        self.assertEqual(got[0]["assistant"], turns[0]["assistant"])
        self.assertEqual(got[1]["assistant"], turns[1]["assistant"])
        # ts 由 sessions.save 补全，GET 原样带回
        self.assertTrue(got[0].get("ts"))
        self.assertEqual(body.get("summary"), "早期摘要")
        self.assertTrue(body.get("updated_at"))

    def test_delete_still_works_and_not_shadowed(self):
        """DELETE 与新 GET 同路径共存；删除后 GET 取回空 turns（证明两方法都在线）。"""
        sessions.save(self.sid, [{"user": "hi", "assistant": "hello"}])
        self.assertEqual(len((self.client.get(f"/api/sessions/{self.sid}").json().get("turns") or [])), 1)

        resp = self.client.delete(f"/api/sessions/{self.sid}")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json().get("ok"))

        body = self.client.get(f"/api/sessions/{self.sid}").json()
        self.assertEqual(body.get("turns"), [])

    def test_session_detail_routes_registered_once(self):
        """GET / DELETE 同路径不同方法，各注册一次、无重复覆盖。"""
        counter = collections.Counter(_routes())
        path = "/api/sessions/{session_id}"
        self.assertEqual(counter[(path, "GET")], 1, "GET %s 未注册或重复注册" % path)
        self.assertEqual(counter[(path, "DELETE")], 1, "DELETE %s 未注册或重复注册" % path)


if __name__ == '__main__':
    unittest.main()
