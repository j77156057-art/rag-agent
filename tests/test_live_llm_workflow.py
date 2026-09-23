"""Opt-in live-provider contract checks for dynamic game workflow planning.

The test never runs by default. Enable ``DOCMIND_LLM_LIVE=1`` with a
non-mock provider and its credentials to verify the real provider's strict
JSON options and task-DAG contracts.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest

from agent_runtime.game_workflow import GameWorkflowManager, WorkflowPolicy
from llm import LLMClient


def _json_object(value):
    text = str(value or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return json.loads(text)


@unittest.skipUnless(
    os.getenv("DOCMIND_LLM_LIVE", "").strip().lower() in {"1", "true", "yes"}
    and os.getenv("LLM_PROVIDER", "mock").strip().lower() not in {"", "mock"},
    "set DOCMIND_LLM_LIVE=1 and a non-mock LLM_PROVIDER to run live workflow smoke",
)
class LiveLLMWorkflowTests(unittest.TestCase):
    def test_provider_drives_strict_options_and_dynamic_task_dag(self):
        llm = LLMClient()

        def options(_request, _plan):
            return llm.chat([{"role": "user", "content": (
                'Return JSON only: {"options":[{"id":"recommended","title":"Minimal prototype",'
                '"summary":"Playable vertical slice","recommended":true},'
                '{"id":"design_first","title":"Design first","summary":"Plan before files",'
                '"recommended":false}]}') }], stream=False)

        def tasks(_request, _selected, _plan):
            return llm.chat([{"role": "user", "content": (
                'Return JSON only: {"tasks":[{"id":"design","role":"designer",'
                '"task":"define acceptance","depends_on":[],"persona":"concise",'
                '"tools":[],"mcp":"deny","reflection":true},'
                '{"id":"verify","role":"tester","task":"verify output",'
                '"depends_on":["design"],"persona":"skeptical", "tools":[],'
                '"mcp":"deny","reflection":true}]}') }], stream=False)

        with tempfile.TemporaryDirectory(prefix="docmind-live-llm-") as storage:
            manager = GameWorkflowManager(storage)
            try:
                started = manager.start(
                    "制作一个可运行的 Godot 2D 原型", project_id="live-llm",
                    llm_enabled=True, policy=WorkflowPolicy(max_subagents=3),
                    option_generator=options, task_generator=tasks)
                self.assertEqual(started["status"], "awaiting_choice")
                options_seen = started["options"]
                self.assertEqual(sum(1 for item in options_seen if item.get("recommended")), 1)
                self.assertTrue(any(item.get("id") == "recommended" for item in options_seen))
                chosen = manager.choose(started["workflow_id"], "recommended")
                self.assertIn(chosen["status"], {"planning", "planned", "awaiting_choice"})
                # LangGraph may execute the registered plan node while
                # resuming the choice gate.  Native/fallback mode can leave a
                # separate plan gate, so both are valid provider contracts.
                planned = (chosen if chosen["status"] == "planned" else
                           manager.plan(started["workflow_id"], task_generator=tasks))
                self.assertEqual(planned["status"], "planned")
                # The provider is allowed to insert a bounded intermediate
                # task (for example prototype) when it judges that useful;
                # the contract is a valid dynamic DAG, not a hard-coded
                # two-member roster.
                task_ids = [str(item["id"]) for item in planned["tasks"]]
                self.assertGreaterEqual(len(task_ids), 2)
                self.assertEqual(len(task_ids), len(set(task_ids)))
                self.assertTrue(any(str(item.get("role")) in {"designer", "design"}
                                    for item in planned["tasks"]))
                self.assertTrue(any(str(item.get("role")) in {"tester", "test", "qa"}
                                    for item in planned["tasks"]))
                id_set = set(task_ids)
                self.assertTrue(all(set(item.get("depends_on") or []).issubset(id_set)
                                    for item in planned["tasks"]))
                self.assertTrue(all(item.get("mcp") in {"auto", "allow", "deny"}
                                    for item in planned.get("subagents", [])))
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
