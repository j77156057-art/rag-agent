# -*- coding: utf-8 -*-
"""黄金题自动打分与回归门（离线规则，不触网）。"""
import json
import os
import tempfile
import unittest

import agent_eval


def _rec(final="", actions=None, expect=None, error=None, qid="Q1"):
    r = {"id": qid, "question": "q", "final": final, "actions": actions or [], "expect": expect or {}}
    if error:
        r["error"] = error
    return r


class ScorerTests(unittest.TestCase):
    def test_must_include_pass_and_fail(self):
        ok = agent_eval.score_record(_rec("见 player.gd:18", expect={"must_include": ["player.gd:18"]}))
        self.assertTrue(ok["passed"])
        bad = agent_eval.score_record(_rec("不知道", expect={"must_include": ["player.gd:18"]}))
        self.assertFalse(bad["passed"])
        self.assertEqual(bad["score"], 0.0)

    def test_any_of(self):
        r = _rec("enemy.gd:5", expect={"any_of": [".gd:", "behaviors/"]})
        self.assertTrue(agent_eval.score_record(r)["passed"])
        r2 = _rec("没有命中", expect={"any_of": [".gd:", "behaviors/"]})
        self.assertFalse(agent_eval.score_record(r2)["passed"])

    def test_hallucination_marker_fails(self):
        r = _rec("答案在 player.py 第 3 行", expect={"must_not_include": ["player.py"]})
        self.assertFalse(agent_eval.score_record(r)["passed"])

    def test_must_call_and_action_bounds(self):
        r = _rec("x", actions=["search_code(query: a)", "read_file(path: b)"],
                 expect={"must_call": ["search_code"], "min_actions": 2, "max_actions": 3})
        self.assertTrue(agent_eval.score_record(r)["passed"])
        r2 = _rec("x", actions=["grep(pattern: a)"], expect={"must_call": ["search_code"]})
        self.assertFalse(agent_eval.score_record(r2)["passed"])
        r3 = _rec("x", actions=["a", "b", "c", "d"], expect={"max_actions": 2})
        self.assertFalse(agent_eval.score_record(r3)["passed"])

    def test_regex(self):
        self.assertTrue(agent_eval.score_record(
            _rec("a.gd:12", expect={"regex": r"\w+\.gd:\d+"}))["passed"])
        self.assertFalse(agent_eval.score_record(
            _rec("无行号", expect={"regex": r"\w+\.gd:\d+"}))["passed"])

    def test_error_record_fails(self):
        s = agent_eval.score_record(_rec("x", expect={"no_error": True}, error="boom"))
        self.assertFalse(s["passed"])

    def test_unscored_when_expect_empty(self):
        s = agent_eval.score_record({"id": "Q", "final": "x", "actions": []})
        self.assertFalse(s["scored"])
        self.assertFalse(s["passed"])

    def test_partial_score(self):
        s = agent_eval.score_record(_rec("只有 player.gd:18",
                                         expect={"must_include": ["player.gd:18", "enemy.gd:20"]}))
        self.assertFalse(s["passed"])
        self.assertAlmostEqual(s["score"], 0.5)


class GateTests(unittest.TestCase):
    def _write(self, path, rows):
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    def test_gate_detects_regression(self):
        tmp = tempfile.mkdtemp(prefix="dm_eval_")
        base = os.path.join(tmp, "b.jsonl")
        cur = os.path.join(tmp, "c.jsonl")
        exp = {"must_include": ["ok"]}
        self._write(base, [_rec("ok", expect=exp), _rec("ok", expect=exp, qid="Q2")])
        self._write(cur, [_rec("nope", expect=exp), _rec("ok", expect=exp, qid="Q2")])
        report = agent_eval.score_file(cur)
        gate = agent_eval.compare_to_baseline(report, base)
        self.assertTrue(gate["regressed"])
        self.assertEqual(gate["regressions"], ["Q1"])
        self.assertEqual(report["passed"], 1)
        self.assertEqual(report["failed"], 1)
        self.assertEqual(report["pass_rate"], 0.5)

    def test_gate_passes_on_improvement(self):
        tmp = tempfile.mkdtemp(prefix="dm_eval_")
        base = os.path.join(tmp, "b.jsonl")
        cur = os.path.join(tmp, "c.jsonl")
        exp = {"must_include": ["ok"]}
        self._write(base, [_rec("nope", expect=exp)])
        self._write(cur, [_rec("ok", expect=exp)])
        report = agent_eval.score_file(cur)
        gate = agent_eval.compare_to_baseline(report, base)
        self.assertFalse(gate["regressed"])
        self.assertEqual(gate["improvements"], ["Q1"])

    def test_main_exit_code_on_regression(self):
        tmp = tempfile.mkdtemp(prefix="dm_eval_")
        base = os.path.join(tmp, "b.jsonl")
        cur = os.path.join(tmp, "c.jsonl")
        exp = {"must_include": ["ok"]}
        self._write(base, [_rec("ok", expect=exp)])
        self._write(cur, [_rec("nope", expect=exp)])
        self.assertEqual(agent_eval.main(["--results", cur, "--baseline", base, "--json"]), 1)
        self.assertEqual(agent_eval.main(["--results", cur, "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
