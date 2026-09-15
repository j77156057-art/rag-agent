# -*- coding: utf-8 -*-
"""trace 账本 + 会话隔离/持久化/压缩 的回归测试（全部离线，不触网）。"""
import os
import tempfile
import time
import unittest

import agent as agent_mod
import agent_trace
import sessions


class _FakeLLM:
    """按脚本回放的假 LLM：逐字符流式，并回报固定 token 用量。"""
    provider = "fake"
    model = "fake-1"

    def __init__(self, scripts):
        self.scripts = scripts
        self.i = 0
        self.last_usage = {}

    def chat(self, messages, stream=True, **kw):
        script = self.scripts[min(self.i, len(self.scripts) - 1)]
        self.i += 1
        self.last_usage = {"prompt_tokens": 7, "completion_tokens": 3}
        if stream:
            def g():
                for ch in script:
                    yield ch
            return g()
        return script

    def count_tokens(self, text):
        return max(0, len(text) // 3)


class _IsoBase(unittest.TestCase):
    """把 trace 文件与会话目录重定向到临时目录，避免污染仓库/相互干扰。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_iso_")
        self._old_trace = agent_trace.TRACE_FILE
        self._old_dir = sessions.SESSIONS_DIR
        self._old_enabled = agent_trace.TRACE_ENABLED
        agent_trace.TRACE_FILE = os.path.join(self.tmp, "traces.jsonl")
        sessions.SESSIONS_DIR = os.path.join(self.tmp, "sessions")
        agent_trace.TRACE_ENABLED = True

    def tearDown(self):
        agent_trace.TRACE_FILE = self._old_trace
        sessions.SESSIONS_DIR = self._old_dir
        agent_trace.TRACE_ENABLED = self._old_enabled


class TraceRecordTests(_IsoBase):
    def test_turn_record_fields(self):
        t = agent_trace.Turn(session_id="s", provider="p", model="m", question="hello")
        t.snapshot_prompt([{"role": "user", "content": "hello"}])
        t.add_usage({"prompt_tokens": 10, "completion_tokens": 4})
        t.tool_step("search_code", "query: x", 12.0, "obs", True)
        t.llm_step(30.0, "stop")
        t.finish("completed", final_text="ans")
        r = t.to_record()
        self.assertEqual(r["total_tokens"], 14)
        self.assertEqual(r["n_steps"], 1)
        self.assertEqual(r["messages_count"], 1)
        self.assertEqual(r["outcome"], "completed")
        self.assertTrue(r["messages_hash"])
        self.assertGreaterEqual(r["elapsed_ms"], 0)

    def test_messages_hash_is_stable_and_content_addressed(self):
        a = agent_trace.messages_hash([{"role": "user", "content": "x"}])
        b = agent_trace.messages_hash([{"role": "user", "content": "x"}])
        c = agent_trace.messages_hash([{"role": "user", "content": "y"}])
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_recent_and_summary(self):
        for i in range(3):
            t = agent_trace.Turn(session_id="s", provider="mock", model="m", question="q")
            t.add_usage({"prompt_tokens": 2, "completion_tokens": 1})
            t.finish("completed")
            agent_trace.record(t.to_record())
        rows = agent_trace.recent(10)
        self.assertEqual(len(rows), 3)
        s = agent_trace.summary()
        self.assertEqual(s["turns"], 3)
        self.assertEqual(s["total_tokens"], 9)
        self.assertEqual(s["by_provider"]["mock"]["turns"], 3)

    def test_disabled_trace_writes_nothing(self):
        agent_trace.TRACE_ENABLED = False
        t = agent_trace.Turn(session_id="s", provider="mock", model="m")
        t.finish("completed")
        self.assertFalse(agent_trace.record(t.to_record()))
        self.assertEqual(agent_trace.recent(10), [])


class AgentTraceIntegrationTests(_IsoBase):
    def test_agent_run_records_one_trace(self):
        a = agent_mod.Agent(llm=_FakeLLM(["Final Answer: 你好"]), session_id="s-trace")
        events = list(a.run("hi", stream=True))
        self.assertEqual(events[-1]["type"], "final")
        rows = agent_trace.recent(10)
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertEqual(r["session_id"], "s-trace")
        self.assertEqual(r["provider"], "fake")
        self.assertEqual(r["outcome"], "completed")
        self.assertGreater(r["prompt_tokens"], 0)
        self.assertGreater(r["completion_tokens"], 0)
        self.assertIsNotNone(r["messages_hash"])

    def test_tool_step_is_recorded(self):
        a = agent_mod.Agent(llm=_FakeLLM([
            "Thought: 查一下\nAction: read_file\nAction Input: path: agent.py",
            "Final Answer: 读完了",
        ]), session_id="s-tool")
        list(a.run("读文件", stream=True))
        r = agent_trace.recent(1)[0]
        self.assertEqual(r["n_steps"], 1)
        self.assertEqual(r["actions"], ["read_file"])
        self.assertIn("read_file", [s["action"] for s in r["steps"]])

    def test_deadline_aborts_turn(self):
        a = agent_mod.Agent(llm=_FakeLLM(["Final Answer: 不该到达"]), session_id="s-dl")
        events = list(a.run("q", stream=True, deadline=time.monotonic() - 1))
        self.assertEqual(events[-1]["type"], "final")
        self.assertIn("时间上限", events[-1]["text"])
        self.assertEqual(agent_trace.recent(1)[0]["outcome"], "deadline_exceeded")

    def test_client_disconnect_marks_aborted(self):
        a = agent_mod.Agent(llm=_FakeLLM(["Thought: 慢慢想" * 5]), session_id="s-abort")
        gen = a.run("q", stream=True)
        next(gen)          # 取第一个 token 后模拟前端断开
        gen.close()
        rows = agent_trace.recent(10)
        self.assertTrue(rows and rows[-1]["aborted"])


class SessionIsolationTests(_IsoBase):
    def test_history_persisted_per_session(self):
        list(agent_mod.Agent(llm=_FakeLLM(["Final Answer: A"]), session_id="s1").run("q1", stream=True))
        list(agent_mod.Agent(llm=_FakeLLM(["Final Answer: B"]), session_id="s2").run("q2", stream=True))
        self.assertEqual([t["user"] for t in sessions.history("s1")], ["q1"])
        self.assertEqual([t["user"] for t in sessions.history("s2")], ["q2"])

    def test_history_survives_new_agent(self):
        list(agent_mod.Agent(llm=_FakeLLM(["Final Answer: A"]), session_id="keep").run("q1", stream=True))
        # 新建 Agent（模拟进程重启）：应从会话文件恢复历史
        a2 = agent_mod.Agent(llm=_FakeLLM(["Final Answer: B"]), session_id="keep")
        self.assertEqual([t["user"] for t in a2.history], ["q1"])

    def test_no_session_id_is_ephemeral(self):
        # 无 session_id：保留进程内记忆（与旧行为一致），但绝不落盘。
        a = agent_mod.Agent(llm=_FakeLLM(["Final Answer: x"]))
        list(a.run("q", stream=True))
        self.assertEqual([t["user"] for t in a.history], ["q"])
        self.assertFalse(os.path.isdir(sessions.SESSIONS_DIR) and os.listdir(sessions.SESSIONS_DIR),
                         "无 session_id 时不应写入任何会话文件")

    def test_compaction_folds_old_turns_into_summary(self):
        turns = [{"user": f"q{i}", "assistant": f"a{i}"} for i in range(20)]
        kept, summary = sessions.maybe_compact("long", turns, None)
        self.assertEqual(len(kept), sessions.KEEP_TURNS)
        self.assertIn("摘要", summary)
        self.assertEqual(kept[-1]["user"], "q19")

    def test_slug_blocks_path_traversal(self):
        self.assertNotIn("/", sessions._slug("../../evil"))
        self.assertNotIn("..", sessions._slug("../../evil"))

    def test_list_sessions(self):
        list(agent_mod.Agent(llm=_FakeLLM(["Final Answer: A"]), session_id="ls1").run("q", stream=True))
        ids = [s["session_id"] for s in sessions.list_sessions()]
        self.assertIn("ls1", ids)


if __name__ == "__main__":
    unittest.main()
