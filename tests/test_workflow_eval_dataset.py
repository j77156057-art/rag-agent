import os
import tempfile
import unittest
from unittest.mock import patch

from agent_runtime import langsmith
from agent_runtime.workflow_eval import (
    aggregate_evaluations,
    dataset_cases,
    evaluate_case,
    load_baseline,
    regression_gate,
    save_baseline,
)


class _Dataset:
    id = "dataset-1"


class _Client:
    def __init__(self):
        self.examples = []

    def read_dataset(self, *, dataset_name):
        raise RuntimeError("missing")

    def create_dataset(self, **_kwargs):
        return _Dataset()

    def create_example(self, **kwargs):
        self.examples.append(kwargs)


class WorkflowEvalDatasetTests(unittest.TestCase):
    def test_baseline_round_trip(self):
        report = aggregate_evaluations([evaluate_case(self._state(), dataset_cases()[0])])
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "baseline.json")
            save_baseline(report, path)
            self.assertEqual(load_baseline(path), report)

    def _state(self, status="completed"):
        return {
            "workflow_id": "wf-eval",
            "status": status,
            "steps": 2,
            "replans": 0,
            "policy": {"max_steps": 24, "max_replans": 2},
            "results": {"results": {"prototype": {"status": "ok"}}},
            "review": {"ok": status == "completed"},
        }

    def test_dataset_case_and_pass_rate_gate(self):
        case = dataset_cases()[0]
        passed = evaluate_case(self._state(), case)
        failed = evaluate_case(self._state("failed"), case)
        report = aggregate_evaluations([passed, failed])
        self.assertEqual(report["pass_rate"], 0.5)
        gate = regression_gate(report, {"pass_rate": 1.0, "items": [passed]})
        self.assertTrue(gate["regressed"])
        self.assertIn("game-prototype-2d", gate["regressions"])

    def test_dataset_sync_is_metadata_only_and_disabled_without_key(self):
        with patch.dict(os.environ, {"DOCMIND_LANGSMITH": "0", "LANGCHAIN_API_KEY": ""}, clear=False):
            result = langsmith.sync_workflow_dataset()
        self.assertFalse(result["enabled"])

        fake = _Client()
        with patch.dict(os.environ, {"DOCMIND_LANGSMITH": "1", "LANGCHAIN_API_KEY": "x"}, clear=False), \
                patch.object(langsmith, "_client", return_value=fake):
            result = langsmith.sync_workflow_dataset()
        self.assertTrue(result["enabled"])
        self.assertEqual(result["examples"], len(dataset_cases()))
        self.assertNotIn("prompt", repr(fake.examples))


if __name__ == "__main__":
    unittest.main()
