# -*- coding: utf-8 -*-
"""批次 D 的 Agent 侧回归测试（全部离线，不触网）。

覆盖：
① D3——历史回放窗口整段只发 1 次网络计数（不再逐轮 O(N) 次 count_tokens）；
② D4——Agent.run 逐请求开关（仅关键字）在运行期生效、在任何出口都还原，
   且不再污染共享单例（正常结束 / 生成器 close() / 异常三条出口都验证）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agent as agent_mod  # noqa: E402


class _FakeLLM:
    provider = "fake"
    model = "fake-1"

    def __init__(self, scripts=(), prompt_budget=0, token_fn=None):
        self.scripts = list(scripts)
        self.i = 0
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.capability = {"context_window": 32768, "thinking": "none", "cloud": False}
        self.prompt_budget = prompt_budget
        self._token_fn = token_fn

    def chat(self, messages, stream=True, **kw):
        script = self.scripts[min(self.i, len(self.scripts) - 1)] if self.scripts else "Final Answer: ok"
        self.i += 1
        self.last_usage = {"prompt_tokens": 1, "completion_tokens": 1}
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


class HistoryWindowCountTests(unittest.TestCase):
    """D3：历史回放只发 1 次网络计数。"""

    def _agent(self, budget, n=10, chars=300):
        llm = _FakeLLM(prompt_budget=budget)
        a = agent_mod.Agent(llm=llm, session_id="hwc")
        a.history = [{"user": "u" * chars, "assistant": "a" * chars, "ts": ""} for _ in range(n)]
        return a

    def test_single_network_count_over_whole_history(self):
        calls = []

        def counting(text):
            calls.append(len(text))
            return max(0, len(text) // 3)

        llm = _FakeLLM(prompt_budget=4000, token_fn=counting)
        a = agent_mod.Agent(llm=llm, session_id="hwc")
        a.history = [{"user": "u" * 300, "assistant": "a" * 300, "ts": ""} for _ in range(10)]
        a._history_window()
        self.assertEqual(len(calls), 1, "整段历史回放只应发 1 次网络计数")

    def test_empty_history_no_count(self):
        calls = []
        llm = _FakeLLM(prompt_budget=4000, token_fn=lambda t: (calls.append(t), 0)[1])
        a = agent_mod.Agent(llm=llm, session_id="hwc")
        self.assertEqual(a._history_window(), [])
        self.assertEqual(calls, [], "无历史不应发起任何计数")

    def test_small_budget_keeps_recent_only(self):
        # 与旧语义一致：超预算时从近到远保留，至少留下最近 1 轮
        a = self._agent(budget=1000)
        w = a._history_window()
        self.assertEqual(len(w), 1)
        self.assertEqual(w[0]["user"], "u" * 300)

    def test_large_budget_keeps_all_candidates(self):
        a = self._agent(budget=10 ** 8)
        from config import AGENT_HISTORY_TURNS
        self.assertEqual(len(a._history_window()), min(10, AGENT_HISTORY_TURNS))


class RunSwitchOverrideTests(unittest.TestCase):
    """D4：逐请求开关（仅关键字）运行期生效、任何出口还原、不污染单例。"""

    def _agent(self):
        a = agent_mod.Agent(llm=_FakeLLM(["Final Answer: A"]))
        a.web_enabled = False
        a.thinking_enabled = None
        a.tool_mode = "react"
        a.plan_mode = False
        return a

    def _capture_switches(self):
        """临时包裹 Agent._run，记录运行期真实生效的 4 个开关 + llm 值。"""
        seen = {}
        orig = agent_mod.Agent._run

        def spy(self, question, **kw):
            seen["web_enabled"] = self.web_enabled
            seen["thinking_enabled"] = self.thinking_enabled
            seen["tool_mode"] = self.tool_mode
            seen["plan_mode"] = self.plan_mode
            seen["llm"] = self.llm
            yield from orig(self, question, **kw)

        agent_mod.Agent._run = spy
        return seen, orig

    def test_overrides_effective_during_run_and_restored(self):
        a = self._agent()
        seen, orig = self._capture_switches()
        try:
            list(a.run("q", stream=True, web_enabled=True, thinking_enabled=True,
                       tool_mode="native", plan_mode=True))
        finally:
            agent_mod.Agent._run = orig
        self.assertEqual(
            {k: seen[k] for k in ("web_enabled", "thinking_enabled", "tool_mode", "plan_mode")},
            {"web_enabled": True, "thinking_enabled": True,
             "tool_mode": "native", "plan_mode": True})
        self.assertEqual(
            (a.web_enabled, a.thinking_enabled, a.tool_mode, a.plan_mode),
            (False, None, "react", False))

    def test_llm_override_effective_and_restored(self):
        # R1：运行期用覆盖的 llm（云端 client），结束后还原为原 llm（本地）
        a = self._agent()
        local = a.llm
        cloud = _FakeLLM(["Final Answer: B"])
        seen, orig = self._capture_switches()
        try:
            list(a.run("q", stream=True, llm=cloud))
        finally:
            agent_mod.Agent._run = orig
        self.assertIs(seen["llm"], cloud, "运行期应使用覆盖后的 llm")
        self.assertIs(a.llm, local, "运行结束后应还原为原 llm")

    def test_llm_none_keeps_original_llm(self):
        a = self._agent()
        local = a.llm
        seen, orig = self._capture_switches()
        try:
            list(a.run("q", stream=True))   # llm=None → 不改
        finally:
            agent_mod.Agent._run = orig
        self.assertIs(seen["llm"], local)
        self.assertIs(a.llm, local)

    def test_none_leaves_switch_unchanged(self):
        a = self._agent()
        a.thinking_enabled = True   # 基线非 None：传 None 不应改动它
        seen, orig = self._capture_switches()
        try:
            list(a.run("q", stream=True))   # 4 个开关全部 None
        finally:
            agent_mod.Agent._run = orig
        self.assertTrue(seen["thinking_enabled"])
        self.assertFalse(seen["web_enabled"])
        self.assertEqual(seen["tool_mode"], "react")
        self.assertFalse(seen["plan_mode"])
        self.assertEqual(
            (a.web_enabled, a.thinking_enabled, a.tool_mode, a.plan_mode),
            (False, True, "react", False))

    def test_close_restores_switches(self):
        # 客户端断连：生成器被 close（抛 GeneratorExit）也必须还原
        a = self._agent()
        a.thinking_enabled = True
        gen = a.run("q", stream=True, web_enabled=True, thinking_enabled=False,
                    tool_mode="native", plan_mode=True)
        next(gen)          # 取第一个事件后断言覆盖已生效、随后模拟断开
        self.assertTrue(a.web_enabled)
        self.assertFalse(a.thinking_enabled)
        self.assertEqual(a.tool_mode, "native")
        self.assertTrue(a.plan_mode)
        gen.close()
        self.assertEqual(
            (a.web_enabled, a.thinking_enabled, a.tool_mode, a.plan_mode),
            (False, True, "react", False))

    def test_exception_restores_switches(self):
        class _BoomLLM(_FakeLLM):
            def chat(self, *args, **kw):
                raise RuntimeError("boom")

        a = agent_mod.Agent(llm=_BoomLLM(["x"]))
        a.web_enabled = False
        a.tool_mode = "react"
        with self.assertRaises(RuntimeError):
            list(a.run("q", stream=True, web_enabled=True, tool_mode="native"))
        self.assertEqual((a.web_enabled, a.tool_mode), (False, "react"))


if __name__ == "__main__":
    unittest.main()
