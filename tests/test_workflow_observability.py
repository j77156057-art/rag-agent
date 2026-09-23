import tempfile
import unittest

from agent_runtime.game_workflow import GameWorkflowManager, StateGraph, WorkflowPolicy


class WorkflowObservabilityTests(unittest.TestCase):
    @unittest.skipIf(StateGraph is None, "LangGraph 未安装")
    def test_deferred_option_generation_returns_before_provider_call(self):
        calls = []

        def options(prompt, _plan):
            calls.append(prompt)
            return {"options": [{"id": "fast", "title": "快速原型",
                                 "summary": "先做可运行版本", "recommended": True}]}

        with tempfile.TemporaryDirectory() as root:
            manager = GameWorkflowManager(root)
            try:
                state = manager.start(
                    "创建一个可验证的游戏原型",
                    llm_enabled=True,
                    option_generator=options,
                    defer_option_generation=True,
                    policy=WorkflowPolicy(provider_retries=0),
                )
                self.assertEqual(state["status"], "generating_options")
                self.assertEqual(calls, [], "启动接口不应同步等待模型方案")
                self.assertGreater(len(state["options"]), 0, "应先返回本地候选避免空白卡住")

                ready = manager.generate_options(state["workflow_id"])
                self.assertEqual(ready["status"], "awaiting_choice")
                self.assertEqual(calls, ["创建一个可验证的游戏原型"])
                self.assertEqual(ready["options"][0]["id"], "fast")
            finally:
                manager.close()

    def test_public_state_contains_bounded_runtime_counters(self):
        with tempfile.TemporaryDirectory() as root:
            manager = GameWorkflowManager(root)
            try:
                state = manager.start("创建一个可验证的游戏原型", llm_enabled=False)
                workflow_id = state["workflow_id"]
                loaded = manager._load(workflow_id)
                manager._event(loaded, "subagent_start", task_id="design")
                manager._event(loaded, "subagent_complete", task_id="design",
                               status="ok", elapsed_ms=12,
                               tokens={"in": 10, "out": 4, "total": 14})
                manager._event(loaded, "before_mcp", tool="search")
                manager._event(loaded, "task_blocked", task_id="verify", status="blocked")
                public = manager._save(loaded).public()
                telemetry = public["observability"]
                self.assertGreaterEqual(telemetry["events"], 5)
                self.assertEqual(telemetry["subagents_started"], 1)
                self.assertEqual(telemetry["subagents_completed"], 1)
                self.assertEqual(telemetry["tool_events"], 0)
                self.assertEqual(telemetry["mcp_events"], 1)
                self.assertEqual(telemetry["failures"], 1)
                self.assertEqual(telemetry["total_tokens"], 14)
                self.assertGreaterEqual(telemetry["duration_ms"], 0)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
