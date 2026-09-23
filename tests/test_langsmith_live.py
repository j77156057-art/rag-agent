"""Opt-in LangSmith network smoke test.

This test is intentionally skipped unless an operator supplies a real API key
and explicitly enables it.  It sends metadata-only workflow events, never user
prompts, tool arguments, or tool output.
"""
import os
import unittest

from agent_runtime import langsmith


@unittest.skipUnless(
    os.getenv("DOCMIND_LANGSMITH_LIVE", "").strip().lower() in {"1", "true", "yes"}
    and bool(os.getenv("LANGCHAIN_API_KEY", "").strip()),
    "set DOCMIND_LANGSMITH_LIVE=1 and LANGCHAIN_API_KEY to run live LangSmith smoke test",
)
class LangSmithLiveTests(unittest.TestCase):
    def test_metadata_root_child_flush_and_close(self):
        state = {
            "workflow_id": "live-smoke",
            "request": "redacted",
            "sources": ["smoke"],
            "status": "awaiting_choice",
            "phase": "clarify",
            "events": [],
            "steps": 0,
            "replans": 0,
            "review": {},
            "langsmith_trace": {},
        }
        trace = langsmith.start_workflow(state)
        self.assertTrue(trace.get("root_run_id"))
        state["langsmith_trace"] = trace
        langsmith.record_workflow_event(state, "choice", {"option_count": 2})
        state.update(status="completed", phase="review", steps=1)
        langsmith.finish_workflow(state)
        result = langsmith.flush(timeout=20)
        self.assertTrue(result["flushed"], result)
        self.assertFalse(result["errors"], result)


if __name__ == "__main__":
    unittest.main()
