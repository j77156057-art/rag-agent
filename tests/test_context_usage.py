# -*- coding: utf-8 -*-
"""上下文窗口用量与按窗口压缩的回归测试（全部离线，不触网）。

覆盖：
① Agent.context_stats 字段与高占用分级；
② run() 每轮开/收尾各推一次 SSE context 事件；
③ 历史回放窗口按模型预算缩放（大窗口多带轮次，小窗口只带最近几轮）；
④ 端到端：小预算模型多轮后触发压缩，notice 后进度条回落；
⑤ GET /api/context 刷新恢复用量。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent as agent_mod  # noqa: E402
import sessions  # noqa: E402


class _FakeLLM:
    provider = "fake"
    model = "fake-1"

    def __init__(self, scripts=(), capability=None, prompt_budget=0, token_fn=None):
        self.scripts = list(scripts)
        self.i = 0
        self.last_usage = {"prompt_tokens": 7, "completion_tokens": 3}
        self.capability = capability or {"context_window": 32768, "thinking": "none", "cloud": False}
        # 0 表示走 Agent 的 PROMPT_TOKEN_BUDGET 兜底（模拟旧假客户端）
        self.prompt_budget = prompt_budget
        self._token_fn = token_fn

    def chat(self, messages, stream=True, **kw):
        script = self.scripts[min(self.i, len(self.scripts) - 1)] if self.scripts else "Final Answer: ok"
        self.i += 1
        self.last_usage = {"prompt_tokens": 7, "completion_tokens": 3}
        if stream:
            def g():
                for ch in script:
                    yield ch
            return g()
        return script

    def count_tokens(self, text):
        if self._token_fn:
            return self._token_fn(text)
        return max(0, len(text) // 3)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_ctx_")
        self._old_dir = sessions.SESSIONS_DIR
        sessions.SESSIONS_DIR = os.path.join(self.tmp, "sessions")

    def tearDown(self):
        sessions.SESSIONS_DIR = self._old_dir


class ContextStatsTests(_Base):
    def test_stats_fields_and_percent_range(self):
        a = agent_mod.Agent(llm=_FakeLLM(), session_id="s1")
        st = a.context_stats("你好")
        self.assertEqual(set(st), {"used_tokens", "context_window", "prompt_budget", "percent", "level"})
        self.assertEqual(st["context_window"], 32768)
        self.assertGreaterEqual(st["percent"], 0)
        self.assertLessEqual(st["percent"], 100)
        self.assertGreater(st["used_tokens"], 0)
        self.assertEqual(st["level"], "ok")

    def test_level_high_and_warn(self):
        win = 100000
        llm = _FakeLLM(
            capability={"context_window": win, "thinking": "none", "cloud": True},
            token_fn=lambda _t: int(win * 0.75),  # 75% -> high
        )
        self.assertEqual(agent_mod.Agent(llm=llm, session_id="lv").context_stats("q")["level"], "high")
        llm._token_fn = lambda _t: int(win * 0.95)                    # 95% -> warn
        self.assertEqual(agent_mod.Agent(llm=llm, session_id="lv").context_stats("q")["level"], "warn")
        llm._token_fn = lambda _t: 10                                # 极低 -> ok
        self.assertEqual(agent_mod.Agent(llm=llm, session_id="lv").context_stats("q")["level"], "ok")


class ContextEventTests(_Base):
    def test_run_emits_context_events_start_and_end(self):
        llm = _FakeLLM(["Final Answer: A"])
        evs = list(agent_mod.Agent(llm=llm, session_id="ev").run("q1", stream=True))
        ctx = [e for e in evs if e.get("type") == "context"]
        self.assertEqual(len(ctx), 2, "开工与收尾各应推一次 context 事件")
        for e in ctx:
            self.assertIn("percent", e)
            self.assertIn("used_tokens", e)
            self.assertIn("context_window", e)
            self.assertIn("level", e)
        # 收尾时本轮问答已入历史，用量不应低于开工时
        self.assertGreaterEqual(ctx[-1]["used_tokens"], ctx[0]["used_tokens"])

    def test_ephemeral_agent_no_context_event(self):
        # 无 session_id 的临时 Agent 不刷 UI 用量
        evs = list(agent_mod.Agent(llm=_FakeLLM(["Final Answer: x"])).run("q", stream=True))
        self.assertFalse([e for e in evs if e.get("type") == "context"])


class HistoryWindowTests(_Base):
    def _agent_with_history(self, n, budget, chars=300):
        llm = _FakeLLM(prompt_budget=budget)
        a = agent_mod.Agent(llm=llm, session_id="hw")
        a.history = [{"user": "u" * chars, "assistant": "a" * chars, "ts": ""} for _ in range(n)]
        return a

    def test_small_budget_keeps_only_recent_turns(self):
        # 每轮约 600 字符 ≈ 200 token；预算 1000 -> 保留目标 350 -> 只装得下最近 1 轮
        a = self._agent_with_history(10, budget=1000)
        window = a._history_window()
        self.assertEqual(len(window), 1)
        self.assertEqual(window[0]["user"], "u" * 300)

    def test_large_budget_carries_more_turns(self):
        # 预算 4000 -> 保留目标 1400 -> 约 7 轮（受 AGENT_HISTORY_TURNS 硬上限保护）
        small = self._agent_with_history(10, budget=1000)
        large = self._agent_with_history(10, budget=4000)
        self.assertLess(len(small._history_window()), len(large._history_window()))
        self.assertGreaterEqual(len(large._history_window()), 6)

    def test_history_never_exceeds_hard_ceiling(self):
        # 预算极大也不得回放超过 AGENT_HISTORY_TURNS 轮
        from config import AGENT_HISTORY_TURNS
        a = self._agent_with_history(AGENT_HISTORY_TURNS + 50, budget=10 ** 8, chars=10)
        self.assertEqual(len(a._history_window()), AGENT_HISTORY_TURNS)


class EndToEndCompactionTests(_Base):
    def test_small_budget_compacts_and_refreshes_usage(self):
        # 预算压到 100：触发阈值 60、保留目标 35 token，每轮约 200 token
        llm = _FakeLLM(["Final Answer: ok"] * 6, prompt_budget=100)
        a = agent_mod.Agent(llm=llm, session_id="e2e")
        notices, ctx_after_notice = [], []
        saw_notice = False
        for _ in range(4):
            for ev in a.run("问" * 300, stream=True):
                if ev.get("type") == "notice":
                    notices.append(ev.get("text", ""))
                    saw_notice = True
                if ev.get("type") == "context" and saw_notice:
                    ctx_after_notice.append(ev)
        self.assertTrue(notices, "小预算模型多轮后应触发自动压缩")
        self.assertIn("压缩", notices[0])
        # 压缩后保留的最近原文轮数不低于下限
        self.assertGreaterEqual(len(a.history), sessions.KEEP_TURNS_MIN)
        self.assertTrue(a.summary)
        # 压缩后那条 context 事件把进度条刷回去（最近窗口体积远小于触发阈值）
        self.assertTrue(ctx_after_notice)
        self.assertLess(ctx_after_notice[-1]["percent"], 70)


class ContextApiTests(_Base):
    def test_unknown_session_inactive(self):
        from api import app
        from starlette.testclient import TestClient
        with TestClient(app) as c:
            r = c.get("/api/context?session_id=__no_such_session__")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertFalse(data["active"])

    def test_returns_cached_snapshot(self):
        import api
        from api import app
        from starlette.testclient import TestClient

        class _StubAgent:
            last_context = {
                "used_tokens": 1234, "context_window": 16384,
                "prompt_budget": 12288, "percent": 8, "level": "ok",
            }

            def context_stats(self, question=""):
                return self.last_context

        key = "__ctx_stub__"
        api._SESSION_AGENTS[key] = _StubAgent()
        try:
            with TestClient(app) as c:
                data = c.get(f"/api/context?session_id={key}").json()
        finally:
            api._SESSION_AGENTS.pop(key, None)
        self.assertTrue(data["active"])
        self.assertEqual(data["used_tokens"], 1234)
        self.assertEqual(data["context_window"], 16384)
        self.assertEqual(data["percent"], 8)


if __name__ == "__main__":
    unittest.main()
