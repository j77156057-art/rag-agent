import tempfile
import unittest

from agent_runtime.game_workflow import GameWorkflowManager


class WorkflowTimelineTests(unittest.TestCase):
    def test_timeline_keeps_longer_history_than_sse_window_and_survives_restart(self):
        root = tempfile.mkdtemp()
        manager = GameWorkflowManager(root)
        workflow_id = manager.start("记录长任务时间线") ["workflow_id"]
        state = manager._load(workflow_id)
        initial = len(state.timeline)
        for index in range(120):
            manager._event(state, "timeline_test", index=index)
        manager._save(state)

        current = manager.get(workflow_id)
        self.assertEqual(len(current["events"]), min(100, initial + 120))
        self.assertEqual(len(current["timeline"]), initial + 120)
        self.assertEqual(current["timeline"][-1]["index"], 119)

        restarted = GameWorkflowManager(root)
        recovered = restarted.get(workflow_id)
        self.assertEqual(len(recovered["timeline"]), initial + 120)
        self.assertEqual(recovered["timeline"][-1]["index"], 119)


if __name__ == "__main__":
    unittest.main()
