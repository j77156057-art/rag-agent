import os
import tempfile
import unittest
from unittest.mock import patch

import config
import mcp_capabilities


class McpCapabilityDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="docmind_mcp_caps_")
        self.project = os.path.join(self.tmp.name, "project")
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.project)
        os.makedirs(self.state)
        self.old_state_root = config.STATE_ROOT
        config.STATE_ROOT = self.state

    def tearDown(self):
        config.STATE_ROOT = self.old_state_root
        self.tmp.cleanup()

    def test_eda_directory_search_returns_safe_connection_guide(self):
        result = mcp_capabilities.search_directory(
            "EDA", web_enabled=True,
            search_fn=lambda _query: "official https://github.com/example/eda-mcp",
        )
        self.assertTrue(result["ok"])
        item = result["results"][0]
        self.assertEqual(item["id"], "eda")
        self.assertIn("pcb", item["capabilities"])
        self.assertEqual(item["sources"], ["https://github.com/example/eda-mcp"])
        # Search snippets are evidence only and must never become executable commands.
        self.assertEqual(item["template"]["command"], "")

    def test_discovery_requires_approval_before_routing(self):
        tools = [{
            "name": "pcb_design_rule_check",
            "description": "Run PCB DRC and export a BOM",
            "input_schema": {"type": "object", "properties": {"board": {"type": "string"}}},
        }]
        with patch("mcp_client.list_tools", return_value={"ok": True, "tools": tools}):
            result = mcp_capabilities.discover(self.project, "eda-local")
        self.assertTrue(result["ok"])
        self.assertEqual(result["candidate"]["domain"], "eda")
        self.assertEqual(mcp_capabilities.active_for(self.project, "eda-local"), {})

        decision = mcp_capabilities.approve(self.project, "eda-local", True)
        self.assertTrue(decision["ok"])
        active = mcp_capabilities.active_for(self.project, "eda-local")
        self.assertEqual(active["status"], "active")
        self.assertIn("drc", active["capabilities"])


if __name__ == "__main__":
    unittest.main()
