import os
import unittest
from unittest.mock import patch

from agent_runtime import langsmith


class _ImmediateThread:
    def __init__(self, target=None, **_kwargs):
        self.target = target

    def start(self):
        if self.target:
            self.target()


class _FakeClient:
    def __init__(self):
        self.created = []
        self.updated = []

    def create_run(self, **kwargs):
        self.created.append(dict(kwargs))

    def update_run(self, run_id=None, **kwargs):
        self.updated.append((run_id, dict(kwargs)))


class LangSmithRealtimeTests(unittest.TestCase):
    def test_async_export_retries_and_flushes(self):
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 2:
                raise RuntimeError("temporary exporter failure")

        with patch.dict(os.environ, {
            "DOCMIND_LANGSMITH_MAX_RETRIES": "2",
            "DOCMIND_LANGSMITH_RETRY_BASE_S": "0",
        }, clear=False):
            langsmith._submit_async(flaky, "test-retry")
            result = langsmith.flush(timeout=1)
        self.assertTrue(result["flushed"])
        self.assertEqual(len(attempts), 2)

    def test_realtime_root_child_and_finish_are_metadata_only(self):
        fake = _FakeClient()
        env = {
            "DOCMIND_LANGSMITH": "1",
            "LANGCHAIN_TRACING_V2": "",
            "LANGCHAIN_API_KEY": "test-key",
        }
        state = {
            "workflow_id": "wf-test",
            "request": "不要上传这段请求正文",
            "sources": ["direct"],
            "status": "awaiting_choice",
            "phase": "clarify",
            "langsmith_trace": {},
            "events": [],
            "steps": 0,
            "replans": 0,
            "review": {},
        }
        with patch.dict(os.environ, env, clear=False), \
                patch.object(langsmith, "_client", return_value=fake), \
                patch.object(langsmith.threading, "Thread", _ImmediateThread):
            trace = langsmith.start_workflow(state)
            self.assertTrue(trace["root_run_id"])
            state["langsmith_trace"] = trace
            langsmith.record_workflow_event(
                state, "task", {"task_id": "compile", "task": "不要上传工具参数",
                                  "status": "ok", "tool_args": {"secret": "x"}})
            state.update(status="completed", phase="review", steps=2)
            langsmith.finish_workflow(state)

        self.assertEqual(len(fake.created), 2)
        root, child = fake.created
        self.assertEqual(root["id"], trace["root_run_id"])
        self.assertEqual(child["parent_run_id"], root["id"])
        self.assertNotIn("不要上传", repr(child))
        self.assertNotIn("tool_args", repr(child))
        self.assertTrue(any(run_id == root["id"] and "end_time" in data
                            for run_id, data in fake.updated))

    def test_disabled_mode_is_noop(self):
        with patch.dict(os.environ, {"DOCMIND_LANGSMITH": "0", "LANGCHAIN_API_KEY": ""}, clear=False):
            self.assertEqual(langsmith.start_workflow({"workflow_id": "wf"}), {})
            self.assertFalse(langsmith.enabled())

    def test_llm_and_tool_leaf_runs_follow_active_span(self):
        fake = _FakeClient()
        env = {"DOCMIND_LANGSMITH": "1", "LANGCHAIN_API_KEY": "test-key"}
        state = {"workflow_id": "wf-leaves", "langsmith_trace": {"root_run_id": "root"}}
        with patch.dict(os.environ, env, clear=False), \
                patch.object(langsmith, "_client", return_value=fake):
            with langsmith.workflow_span(state, "execute") as parent:
                with langsmith.llm_call(session_id="s", message_count=2, input_chars=20):
                    pass
                with langsmith.tool_call(name="tool.fs", session_id="s", input_chars=8):
                    pass
        self.assertEqual([item["run_type"] for item in fake.created], ["chain", "llm", "tool"])
        self.assertEqual(fake.created[1]["parent_run_id"], parent)
        self.assertEqual(fake.created[2]["parent_run_id"], parent)

    def test_retrieval_spans_are_nested_and_content_free(self):
        fake = _FakeClient()
        with patch.dict(os.environ, {"DOCMIND_LANGSMITH": "1", "LANGCHAIN_API_KEY": "test-key"}, clear=False), \
                patch.object(langsmith, "_client", return_value=fake):
            with langsmith.workflow_span({"workflow_id": "wf-r", "langsmith_trace": {"root_run_id": "root-r"}}, "choice") as parent:
                with langsmith.retrieval_span(
                        name="retrieval.bm25", collection="code", mode="bm25",
                        query_chars=42, inputs={"top_k": 5}) as outputs:
                    outputs.update({"documents": 10, "returned": 5, "duration_ms": 1.2})
        self.assertEqual([item["run_type"] for item in fake.created], ["chain", "retriever"])
        self.assertEqual(fake.created[1]["parent_run_id"], parent)
        self.assertNotIn("query text", repr(fake.created))
        self.assertNotIn("snippet", repr(fake.created))
        self.assertTrue(any(data.get("outputs", {}).get("returned") == 5
                            for _run_id, data in fake.updated))

    def test_explicit_child_span_is_parent_for_worker_leaf_runs(self):
        fake = _FakeClient()
        with patch.dict(os.environ, {"DOCMIND_LANGSMITH": "1", "LANGCHAIN_API_KEY": "test-key"}, clear=False), \
                patch.object(langsmith, "_client", return_value=fake):
            with langsmith.child_span("root-worker", "subagent.prototype", run_type="chain") as task_run:
                with langsmith.llm_call(session_id="worker", message_count=1, input_chars=3):
                    pass
        self.assertEqual(fake.created[0]["parent_run_id"], "root-worker")
        self.assertEqual(fake.created[1]["parent_run_id"], task_run)


if __name__ == "__main__":
    unittest.main()
