import tempfile
import unittest

from agent_runtime.game_workflow import GameWorkflowManager


class WorkflowObservabilityTests(unittest.TestCase):
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
