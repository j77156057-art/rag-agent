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

    def test_eda_directory_merges_registry_keyword_candidates(self):
        calls = []

        def registry_fn(query):
            calls.append(query)
            if query == "kicad":
                return {"ok": True, "candidates": [{
                    "transport": "stdio", "command": "uvx", "args": ["kicad-mcp"],
                    "url": "", "env": {}, "headers": {},
                    "provenance": {"server_name": "kicad/mcp", "url": "https://github.com/kicad/mcp", "domain": "github.com"},
                    "command_unresolved": False,
                }]}
            return {"ok": True, "candidates": []}

        result = mcp_capabilities.search_directory("EDA", web_enabled=True, registry_fn=registry_fn)
        item = result["results"][0]
        self.assertIn("kicad", calls)
        option = next(o for o in item["connection_options"] if o.get("id") == "kicad/mcp")
        self.assertEqual(option["trust_tier"], "community")

    def test_eda_registry_skips_unrelated_substring_and_checks_easyeda(self):
        calls = []

        def registry_fn(query):
            calls.append(query)
            name = ("ai.jeda/jeda-ai" if query == "EDA" else
                    "io.github.VLab-Software/easyeda-pro-mcp" if query == "easyeda" else "")
            if not name:
                return {"ok": False, "candidates": []}
            return {"ok": True, "candidates": [{
                "transport": "stdio", "command": "npx", "args": ["easyeda-pro-mcp"],
                "url": "", "env": {}, "headers": {},
                "provenance": {"server_name": name, "description": "mindmaps" if query == "EDA" else "EasyEDA bridge",
                               "url": "https://github.com/example/mindmaps" if query == "EDA" else "https://github.com/VLab-Software/easyeda_mcp",
                               "domain": "github.com"},
            }]}

        result = mcp_capabilities.search_directory("EDA", web_enabled=True, registry_fn=registry_fn)
        options = result["results"][0]["connection_options"]
        self.assertIn("easyeda", calls)
        self.assertTrue(any(o.get("id") == "io.github.VLab-Software/easyeda-pro-mcp" for o in options))
        self.assertFalse(any(o.get("id") == "ai.jeda/jeda-ai" for o in options))

    def test_directory_offline_mode_never_calls_registry(self):
        def fail_if_called(_query):
            self.fail("offline mode called the Registry")

        result = mcp_capabilities.search_directory("EDA", web_enabled=False, registry_fn=fail_if_called)
        self.assertEqual(result["results"][0]["source_status"], "offline_guide")

    def test_registry_directory_drops_unsafe_candidates(self):
        def registry_fn(_query):
            return {"ok": True, "candidates": [{
                "transport": "stdio", "command": "curl", "args": ["https://evil.example/mcp"],
                "url": "", "env": {}, "headers": {},
                "provenance": {"server_name": "evil/curl", "url": "https://evil.example", "domain": "evil.example"},
                "command_unresolved": False,
            }]}

        result = mcp_capabilities.search_directory("EDA", registry_fn=registry_fn)
        self.assertFalse(any(o.get("id") == "evil/curl" for o in result["results"][0]["connection_options"]))

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
