import unittest

from agent_runtime.retrieval import Document
from agent_runtime.retrieval_eval import compare_reports, evaluate_retriever


class _FakeRetriever:
    def __init__(self, rows):
        self.rows = rows

    def invoke(self, query):
        return list(self.rows.get(query, []))


class RetrievalEvaluationTests(unittest.TestCase):
    def test_recall_and_mrr(self):
        retriever = _FakeRetriever({
            "jump": [
                Document(page_content="wrong", metadata={"source": "other.py"}),
                Document(page_content="right", metadata={"source": "player.py"}),
            ],
        })
        report = evaluate_retriever(retriever, [{
            "id": "jump", "query": "jump", "relevant_sources": ["player.py"]}], ks=(1, 2))
        self.assertEqual(report["metrics"]["recall_at_1"], 0.0)
        self.assertEqual(report["metrics"]["recall_at_2"], 1.0)
        self.assertEqual(report["metrics"]["mrr"], 0.5)

    def test_compare_reports_selects_best_mode(self):
        result = compare_reports({
            "dense": {"metrics": {"mrr": 0.4}},
            "hybrid": {"metrics": {"mrr": 0.8}},
        })
        self.assertEqual(result["best"], "hybrid")
        self.assertEqual(result["ranking"][0]["value"], 0.8)


if __name__ == "__main__":
    unittest.main()
