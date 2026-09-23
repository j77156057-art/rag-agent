import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api_routes.agent import build_router


class RetrievalApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = FastAPI()
        app.include_router(build_router(object()))
        cls.client = TestClient(app)

    def test_evaluate_returns_mode_comparison(self):
        report = {
            "collection": "docs",
            "modes": {
                "dense": {"metrics": {"mrr": 0.25}},
                "hybrid": {"metrics": {"mrr": 0.75}},
            },
        }
        with patch("api_routes.agent.evaluate_modes", return_value=report) as evaluate:
            response = self.client.post("/api/agent/retrieval/evaluate", json={
                "cases": [{"id": "movement", "query": "movement",
                           "relevant_sources": ["player.py"]}],
                "collection": "docs", "top_k": 5,
                "modes": ["dense", "hybrid"], "metric": "mrr", "ks": [1, 3],
            })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["comparison"]["best"], "hybrid")
        self.assertEqual(body["comparison"]["metric"], "mrr")
        self.assertEqual(evaluate.call_args.kwargs["collection"], "docs")
        self.assertEqual(evaluate.call_args.kwargs["modes"], ["dense", "hybrid"])
        self.assertEqual(evaluate.call_args.kwargs["ks"], (1, 3))

    def test_evaluate_rejects_empty_cases(self):
        response = self.client.post("/api/agent/retrieval/evaluate", json={"cases": []})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["ok"])

    def test_status_exposes_lexical_runtime_diagnostics(self):
        with patch("api_routes.agent.retrieval_status", return_value={
                "backend": "hybrid", "bm25": True, "lexical_persistence": True}):
            response = self.client.get("/api/agent/retrieval/status?collection=docs&top_k=3")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertTrue(response.json()["retrieval"]["lexical_persistence"])


if __name__ == "__main__":
    unittest.main()
