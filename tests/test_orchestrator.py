# -*- coding: utf-8 -*-
"""多代理编排器：任务图校验、分波并行、上下游上下文、失败阻断、结果合成（离线）。"""
import json
import os
import tempfile
import time
import unittest

import agent as agent_mod
import agent_trace
import orchestrator as orch
import sessions


class ParsePlanTests(unittest.TestCase):
    def test_accepts_list_and_object(self):
        tasks = orch.parse_plan([{"id": "a", "task": "t"}])
        self.assertEqual(tasks[0]["id"], "a")
        self.assertEqual(tasks[0]["role"], "researcher")      # 默认角色
        self.assertFalse(tasks[0]["optional"])
        tasks = orch.parse_plan({"tasks": [{"id": "a", "task": "t", "role": "CODER"}]})
        self.assertEqual(tasks[0]["role"], "coder")           # 角色归一为小写

    def test_deps_as_string_and_alias(self):
        t = orch.parse_plan([{"id": "a", "task": "x"}, {"id": "b", "task": "y", "after": "a"}])
        self.assertEqual(t[1]["depends_on"], ["a"])
        t2 = orch.parse_plan([{"id": "a", "task": "x"}, {"id": "b", "task": "y", "depends_on": "a, a"}])
        self.assertEqual(t2[1]["depends_on"], ["a", "a"])

    def test_auto_id_when_missing(self):
        tasks = orch.parse_plan([{"task": "x"}, {"task": "y"}])
        self.assertEqual([t["id"] for t in tasks], ["t1", "t2"])

    def test_rejects_bad_plans(self):
        cases = [
            ([], "空"),
            ([{"id": "a", "task": ""}], "描述"),
            ([{"id": "a", "task": "x"}, {"id": "a", "task": "y"}], "重复"),
            ([{"id": "a", "task": "x", "depends_on": ["zz"]}], "不存在"),
            ([{"id": "a", "task": "x", "depends_on": ["a"]}], "自身"),
            (["nope"], "不是对象"),
            (None, "任务数组"),
        ]
        for raw, kw in cases:
            with self.assertRaises(orch.PlanError, msg=str(raw)):
                orch.parse_plan(raw)

    def test_max_tasks_guard(self):
        many = [{"id": f"t{i}", "task": "x"} for i in range(orch.MAX_TASKS + 1)]
        with self.assertRaises(orch.PlanError):
            orch.parse_plan(many)


class TopologyTests(unittest.TestCase):
    def _w(self, spec):
        return orch.topological_waves(orch.parse_plan(spec))

    def test_chain(self):
        self.assertEqual(self._w([{"id": "a", "task": "x"},
                                  {"id": "b", "task": "y", "depends_on": ["a"]},
                                  {"id": "c", "task": "z", "depends_on": ["b"]}]),
                         [["a"], ["b"], ["c"]])

    def test_diamond(self):
        waves = self._w([{"id": "a", "task": "x"},
                         {"id": "b", "task": "y", "depends_on": ["a"]},
                         {"id": "c", "task": "z", "depends_on": ["a"]},
                         {"id": "d", "task": "w", "depends_on": ["b", "c"]}])
        self.assertEqual(waves[0], ["a"])
        self.assertEqual(sorted(waves[1]), ["b", "c"])
        self.assertEqual(waves[2], ["d"])

    def test_cycle_is_rejected(self):
        with self.assertRaises(orch.PlanError):
            self._w([{"id": "a", "task": "x", "depends_on": ["b"]},
                     {"id": "b", "task": "y", "depends_on": ["a"]}])


class RunPlanTests(unittest.TestCase):
    def _plan(self):
        return orch.parse_plan([
            {"id": "a", "task": "甲"},
            {"id": "b", "task": "乙", "depends_on": ["a"]},
            {"id": "c", "task": "丙", "depends_on": ["a"]},
        ])

    def test_waves_parallel_and_context_passed(self):
        seen = {}

        def runner(task, ctx):
            seen[task["id"]] = dict(ctx)
            if task["id"] == "a":
                time.sleep(0.2)
            return {"status": "ok", "conclusion": f"结论文本-{task['id']}", "steps": 1}

        t0 = time.monotonic()
        rep = orch.run_plan(self._plan(), runner, max_parallel=4)
        elapsed = time.monotonic() - t0
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["waves"], [["a"], ["b", "c"]])
        self.assertEqual(rep["n_ok"], 3)
        self.assertLess(elapsed, 0.32, f"同波未并行：{elapsed:.2f}s")
        # 下游拿到上游结论，上游拿不到下游
        self.assertEqual(seen["a"], {})
        self.assertEqual(seen["b"].get("a"), "结论文本-a")
        self.assertEqual(seen["c"].get("a"), "结论文本-a")

    def test_failure_blocks_downstream(self):
        def runner(task, ctx):
            if task["id"] == "a":
                return {"status": "failed", "conclusion": "", "error": "故意失败"}
            return {"status": "ok", "conclusion": "ok"}

        rep = orch.run_plan(self._plan(), runner)
        self.assertFalse(rep["ok"])
        self.assertEqual(rep["results"]["a"]["status"], "failed")
        self.assertEqual(rep["results"]["b"]["status"], "blocked")
        self.assertIn("上游 a", rep["results"]["b"]["error"])
        self.assertEqual(rep["blocked"], ["b", "c"])

    def test_optional_upstream_failure_does_not_block(self):
        plan = orch.parse_plan([{"id": "a", "task": "x", "optional": True},
                                {"id": "b", "task": "y", "depends_on": ["a"]}])

        def runner(task, ctx):
            if task["id"] == "a":
                return {"status": "failed", "error": "可选失败"}
            return {"status": "ok", "conclusion": "b 跑完了"}

        rep = orch.run_plan(plan, runner)
        self.assertEqual(rep["results"]["b"]["status"], "ok")
        self.assertTrue(rep["ok"])

    def test_runner_exception_is_contained(self):
        def runner(task, ctx):
            if task["id"] == "a":
                raise RuntimeError("boom")
            return {"status": "ok", "conclusion": "fine"}

        rep = orch.run_plan(self._plan(), runner)
        self.assertEqual(rep["results"]["a"]["status"], "failed")
        self.assertIn("boom", rep["results"]["a"]["error"])
        self.assertEqual(rep["results"]["c"]["status"], "blocked")

    def test_synth_runs_and_reports(self):
        def runner(task, ctx):
            return {"status": "ok", "conclusion": f"C-{task['id']}"}

        captured = {}

        def synth(tasks, results):
            captured["ids"] = [t["id"] for t in tasks]
            return "合成后的最终答复"

        rep = orch.run_plan(self._plan(), runner, synth_runner=synth)
        self.assertEqual(rep["merged"], "合成后的最终答复")
        self.assertEqual(captured["ids"], ["a", "b", "c"])

    def test_synth_skipped_when_all_failed(self):
        def runner(task, ctx):
            return {"status": "failed", "error": "nope"}

        rep = orch.run_plan(self._plan(), runner, synth_runner=lambda t, r: "不该被调用")
        self.assertEqual(rep["merged"], "")

    def test_synth_exception_degrades_gracefully(self):
        def runner(task, ctx):
            return {"status": "ok", "conclusion": "c"}

        def synth(tasks, results):
            raise RuntimeError("synth boom")

        rep = orch.run_plan(self._plan(), runner, synth_runner=synth)
        self.assertIn("结果合成失败", rep["merged"])

    def test_format_report(self):
        def runner(task, ctx):
            return {"status": "ok", "conclusion": "x" * 10}

        rep = orch.run_plan(self._plan(), runner, synth_runner=lambda t, r: "MERGED")
        text = orch.format_report(rep)
        self.assertIn("编排完成", text)
        self.assertIn("[ok] a", text)
        self.assertIn("MERGED", text)


class _CloningLLM:
    """父实例按脚本回放；clone 出的子实例固定回一句 Final Answer。"""
    provider = "deepseek"
    model = "cloning"

    def __init__(self, parent_steps=None, child_reply="Final Answer: 子代理结论 OK"):
        self.parent_steps = parent_steps or []
        self.child_reply = child_reply
        self.i = 0
        self.last_usage = {}
        self.last_tool_calls = []

    def clone(self):
        return _CloningLLM([("text", self.child_reply)], self.child_reply)

    def chat(self, messages, stream=True, **kw):
        spec = self.parent_steps[min(self.i, len(self.parent_steps) - 1)] if self.parent_steps \
            else ("text", self.child_reply)
        self.i += 1
        self.last_usage = {"prompt_tokens": 4, "completion_tokens": 2}
        self.last_tool_calls = []
        content = ""
        if spec[0] == "tools":
            self.last_tool_calls = [{"id": f"c{i}", "name": n, "arguments": json.dumps(a)}
                                    for i, (n, a) in enumerate(spec[1])]
        else:
            content = spec[1]
        if stream:
            def g():
                for ch in content:
                    yield ch
            return g()
        return content

    def count_tokens(self, text):
        return max(1, len(text) // 3)


class AgentOrchestrateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="dm_orch_")
        self._t = agent_trace.TRACE_FILE
        self._s = sessions.SESSIONS_DIR
        agent_trace.TRACE_FILE = os.path.join(self.tmp, "t.jsonl")
        sessions.SESSIONS_DIR = os.path.join(self.tmp, "s")

    def tearDown(self):
        agent_trace.TRACE_FILE = self._t
        sessions.SESSIONS_DIR = self._s

    def test_agent_orchestrate_returns_report(self):
        a = agent_mod.Agent(llm=_CloningLLM())
        rep = a.orchestrate({"tasks": [{"id": "a", "role": "researcher", "task": "查 A"},
                                       {"id": "b", "role": "reviewer", "task": "评审 B",
                                        "depends_on": ["a"]}]})
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["n_ok"], 2)
        self.assertIn("子代理结论", rep["results"]["a"]["conclusion"])
        self.assertTrue(rep["merged"])

    def test_agent_orchestrate_bad_plan(self):
        a = agent_mod.Agent(llm=_CloningLLM())
        rep = a.orchestrate({"tasks": [{"id": "a", "task": "x", "depends_on": ["zz"]}]})
        self.assertFalse(rep["ok"])
        self.assertIn("任务图不合法", rep["error"])

    def test_orchestrate_tool_via_run(self):
        plan = {"tasks": [{"id": "a", "role": "researcher", "task": "查 A"}], "synth": True}
        llm = _CloningLLM([
            ("text", "Thought: 拆一下\nAction: orchestrate\nAction Input: " + json.dumps(plan)),
            ("text", "Final Answer: 编排结果已汇总"),
        ])
        a = agent_mod.Agent(llm=llm)
        events = list(a.run("复杂任务", stream=True))
        obs = " ".join(e["text"] for e in events if e["type"] == "observation")
        self.assertIn("编排完成", obs, obs[:200])
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(agent_trace.recent(1)[0]["n_steps"], 1)

    def test_orchestrate_tool_bad_json(self):
        a = agent_mod.Agent(llm=_CloningLLM())
        out = a._orchestrate_tool("{不是 json")
        self.assertIn("参数错误", out)

    def test_child_without_final_degrades_instead_of_empty(self):
        """子代理用光步数也没给出 Final Answer 时，结论必须是"过程要点"而非空串——
        否则下游任务会拿到空上下文，编排器还会把"没结论"当成没问题。"""
        class _NeverFinal:
            provider = "deepseek"
            model = "nf"

            def __init__(self):
                self.i = 0
                self.last_usage = {}
                self.last_tool_calls = []

            def clone(self):
                return _NeverFinal()

            def chat(self, messages, stream=True, **kw):
                self.last_usage = {"prompt_tokens": 3, "completion_tokens": 1}
                self.last_tool_calls = []
                self.i += 1
                s = (f"Thought: 继续检索第 {self.i} 轮，先看看相关实现\n"
                     f"Action: search_code\nAction Input: query: term{self.i}")
                if stream:
                    def g():
                        for ch in s:
                            yield ch
                    return g()
                return s

            def count_tokens(self, text):
                return 1

        a = agent_mod.Agent(llm=_NeverFinal())
        out = a._run_child("researcher", "查一些东西")
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["conclusion"].strip(), "空结论会让下游失去上下文")
        self.assertTrue(out.get("degraded"), "应标记为降级（过程要点）")


if __name__ == "__main__":
    unittest.main()
