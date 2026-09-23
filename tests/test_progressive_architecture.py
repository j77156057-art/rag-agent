import unittest

from agent_runtime.context_router import ContextRouter
from agent_runtime.tools import Capability, SideEffect, ToolSpec
from tools import TOOLS, tool_schemas


class ProgressiveArchitectureTests(unittest.TestCase):
    def test_registry_is_fully_typed(self):
        self.assertTrue(TOOLS)
        self.assertTrue(all(isinstance(spec, ToolSpec) for spec in TOOLS.values()))
        write = TOOLS["apply_edit"]
        self.assertEqual(write.capability, Capability.WRITE_LOCAL)
        self.assertEqual(write.side_effect, SideEffect.MUTATING)
        self.assertFalse(write.parallel_safe)
        self.assertIn("input_schema", write)
        self.assertIn("owner_app", write)

    def test_structured_schema_preserves_metadata_contract(self):
        schema = tool_schemas(["search_code"])[0]["function"]
        self.assertEqual(schema["parameters"]["properties"]["query"]["type"], "string")
        self.assertIn("input", schema["parameters"]["properties"])

    def test_context_router_only_exposes_web_when_needed(self):
        router = ContextRouter()
        ordinary = router.route("请解释这个函数", code_root="D:/project", web_enabled=True)
        current = router.route("请搜索最新 Godot 文档", code_root="D:/project", web_enabled=True)
        self.assertNotIn("web", ordinary.tool_groups)
        self.assertIn("web", current.tool_groups)
        self.assertIn("web", current.sources)


if __name__ == "__main__":
    unittest.main()
