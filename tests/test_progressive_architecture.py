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

    def test_project_bug_questions_use_project_audit_context(self):
        router = ContextRouter()
        plan = router.route("你看看目前的游戏有什么bug吗", code_root="D:/project")
        joined = "\n".join(plan.messages)
        self.assertIn("当前项目缺陷审查", joined)
        self.assertIn("dev_list_bugs", joined)
        self.assertIn("不能作为当前缺陷结论或唯一证据", joined)


if __name__ == "__main__":
    unittest.main()
