import tempfile
import unittest

from agent_runtime.cockpit_policy import (QUEUE_APPROVABLE, pending_gate_requests)
from game_workbench import approval, list_approval_records, require_approval


class CockpitPolicyTests(unittest.TestCase):
    def test_blocked_action_is_queued_and_resolved_by_existing_gate(self):
        root = tempfile.mkdtemp()
        self.assertTrue(require_approval(root, "install_tool", "python:pytest==8.4")
                        ["approval_required"])
        self.assertTrue(require_approval(root, "install_tool", "python:pytest==8.4")
                        ["approval_required"])
        requests = pending_gate_requests(root, list_approval_records(root))
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["risk"], "L2")
        approval(root, "install_tool", "workbench-user", True, "python:pytest==8.4")
        self.assertIsNone(require_approval(root, "install_tool", "python:pytest==8.4"))
        self.assertFalse(pending_gate_requests(root, list_approval_records(root)))

    def test_mcp_requests_are_visible_but_not_approvable_in_cockpit(self):
        root = tempfile.mkdtemp()
        require_approval(root, "mcp_server", "eda:abc123")
        request = pending_gate_requests(root, list_approval_records(root))[0]
        self.assertEqual(request["action"], "mcp_server")
        self.assertNotIn(request["action"], QUEUE_APPROVABLE)


if __name__ == "__main__":
    unittest.main()
