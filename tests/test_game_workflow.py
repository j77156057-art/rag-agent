import tempfile
import unittest
import os
from unittest.mock import patch

from agent_runtime.game_workflow import GameWorkflowManager, WorkflowError, WorkflowPolicy, review_output
from agent_runtime.tools import SideEffect, execute_tool, tool_idempotency_scope
from agent_runtime.context_router import (allocate_layer_budgets, compress_context,
                                           compress_context_async, compress_layers,
                                           compress_text, layer_snapshot_digest,
                                           summarize_subagent_result,
                                           compress_layers_durable, persistent_compression_worker)
from agent_runtime.workflow_eval import compare_evaluation, evaluate_workflow


class GameWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.manager = GameWorkflowManager(tempfile.mkdtemp())

    def test_choice_research_and_custom_paths(self):
        first = self.manager.start("做一个 2D 横版游戏原型", project_root="D:/project")
        self.assertEqual(first["status"], "awaiting_choice")
        wid = first["workflow_id"]
        waiting = self.manager.choose(wid, "web_research")
        self.assertEqual(waiting["status"], "awaiting_research")
        chosen = self.manager.apply_research(wid, "Godot 4 官方文档摘要")
        self.assertEqual(chosen["status"], "awaiting_choice")
        custom = self.manager.choose(wid, "custom", custom_request="只做一个可运行的移动和跳跃样例")
        self.assertEqual(custom["status"], "awaiting_choice")

    def test_plan_approval_execute_and_review(self):
        state = self.manager.start("创建一个最小游戏原型", policy=WorkflowPolicy(max_subagents=2))
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        planned = self.manager.plan(wid, [{"id": "a", "role": "tester", "task": "验证输入"}])
        self.assertEqual(planned["status"], "planned")
        blocked = self.manager.execute(wid, lambda task, context: {
            "status": "ok", "conclusion": "验证通过", "steps": 1})
        self.assertEqual(blocked["status"], "awaiting_approval")
        self.manager.approve(wid, True)
        done = self.manager.execute(wid, lambda task, context: {
            "status": "ok", "conclusion": "验证通过", "steps": 1})
        self.assertEqual(done["status"], "completed")
        self.assertTrue(done["review"]["ok"])

    def test_approval_can_resume_and_execute_in_one_call_when_callback_survives(self):
        state = self.manager.start("自动审批后执行", policy=WorkflowPolicy(max_subagents=1))
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        self.manager.plan(wid, [{"id": "a", "role": "tester", "task": "验证"}])
        self.manager.execute(wid, lambda _task, _context: {
            "status": "ok", "conclusion": "自动完成", "steps": 1})
        done = self.manager.approve(wid, True, auto_execute=True)
        self.assertEqual(done["status"], "completed")
        self.assertTrue(any(event["kind"] == "auto_execute_start" for event in done["events"]))

    def test_successful_workflow_creates_skill_candidate_without_auto_saving(self):
        state = self.manager.start("沉淀可复用原型流程", experience_enabled=True,
                                   policy=WorkflowPolicy(approval_mode="high"))
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        self.manager.plan(wid, [{"id": "a", "task": "验证"}])
        done = self.manager.execute(wid, lambda *_: {
            "status": "ok", "conclusion": "通过", "steps": 1})
        self.assertEqual(done["status"], "completed")
        candidate = done["skill_candidate"]
        self.assertEqual(candidate["status"], "pending_approval")
        self.assertFalse(any(item.get("source") == "user" and
                             item.get("name") == candidate["name"]
                             for item in __import__("skills").list_skills().get("items", [])))

    def test_workflow_evaluator_and_regression_compare(self):
        state = self.manager.start("评估流程", policy=WorkflowPolicy(approval_mode="high"))
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        self.manager.plan(wid, [{"id": "a", "task": "验证"}])
        done = self.manager.execute(wid, lambda *_: {
            "status": "ok", "conclusion": "通过", "steps": 1})
        evaluation = self.manager.evaluate(wid)
        self.assertTrue(evaluation["passed"])
        degraded = dict(evaluation, passed=False,
                        checks=[dict(item, ok=False) if item["name"] == "review_ok" else item
                                for item in evaluation["checks"]])
        regression = compare_evaluation(degraded, evaluation)
        self.assertTrue(regression["regressed"])

    def test_auto_execute_after_restart_degrades_to_explicit_execute(self):
        state_root = tempfile.mkdtemp()
        first = GameWorkflowManager(state_root)
        state = first.start("重启后审批", policy=WorkflowPolicy(max_subagents=1))
        wid = state["workflow_id"]
        first.choose(wid, "recommended")
        first.plan(wid, [{"id": "a", "task": "验证"}])
        first.execute(wid, lambda *_: {"status": "ok", "conclusion": "ok", "steps": 1})
        restarted = GameWorkflowManager(state_root)
        approved = restarted.approve(wid, True, auto_execute=True)
        self.assertEqual(approved["status"], "planned")
        self.assertTrue(any(event["kind"] == "auto_execute_unavailable" for event in approved["events"]))

    def test_auto_execute_after_restart_rebuilds_callbacks_when_resolver_is_configured(self):
        state_root = tempfile.mkdtemp()
        first = GameWorkflowManager(state_root)
        state = first.start("重启后恢复执行", policy=WorkflowPolicy(max_subagents=1))
        wid = state["workflow_id"]
        first.choose(wid, "recommended")
        first.plan(wid, [{"id": "a", "task": "验证"}])
        first.execute(wid, lambda *_: {"status": "ok", "conclusion": "ok", "steps": 1},
                      session_id="session-rebuilt")
        restarted = GameWorkflowManager(state_root)
        restarted.set_execution_resolver(lambda _state: {
            "runner": lambda *_: {"status": "ok", "conclusion": "恢复后完成", "steps": 1}})
        done = restarted.approve(wid, True, auto_execute=True)
        self.assertEqual(done["status"], "completed")
        self.assertTrue(any(event["kind"] == "auto_execute_reconstructed"
                            for event in done["events"]))

    def test_startup_recovery_scheduler_resumes_approved_workflow(self):
        state_root = tempfile.mkdtemp()
        first = GameWorkflowManager(state_root)
        state = first.start("启动恢复调度", policy=WorkflowPolicy(max_subagents=1))
        wid = state["workflow_id"]
        first.choose(wid, "recommended")
        first.plan(wid, [{"id": "a", "task": "验证"}])
        first.execute(wid, lambda *_: {"status": "ok", "conclusion": "ok", "steps": 1},
                      session_id="session-scheduler")
        first.approve(wid, True)
        restarted = GameWorkflowManager(state_root)
        restarted.set_execution_resolver(lambda _state: {
            "runner": lambda *_: {"status": "ok", "conclusion": "调度恢复完成", "steps": 1}})
        recovered = restarted.recover_pending()
        self.assertEqual(recovered[0]["workflow_id"], wid)
        self.assertEqual(restarted.get(wid)["status"], "completed")

    def test_output_review_detects_replacement_chars_and_failures(self):
        result = review_output({"status": "ok", "text": "坏\ufffd结果", "steps": [{"ok": False}]})
        self.assertFalse(result["ok"])
        self.assertFalse(result["checks"]["no_replacement_chars"])

    def test_llm_options_are_validated_and_keep_intervention_choices(self):
        state = self.manager.start(
            "做一个 2D 游戏",
            option_generator=lambda _request, _plan: (
                "```json\n{\"options\":[{\"id\":\"small\",\"title\":\"小原型\","
                "\"summary\":\"先做移动与碰撞\",\"recommended\":true}]}\n```"))
        ids = [item["id"] for item in state["options"]]
        self.assertIn("small", ids)
        self.assertIn("custom", ids)
        self.assertIn("web_research", ids)
        self.assertEqual(state["events"][-1]["option_source"], "llm")

    def test_option_and_plan_generators_execute_in_graph_nodes(self):
        state = self.manager.start(
            "做一个 2D 游戏",
            option_generator=lambda _request, _plan: {
                "options": [{"id": "small", "title": "小原型",
                             "summary": "先做移动与碰撞", "recommended": True}]},
            task_generator=lambda _request, _selected, _plan: {
                "tasks": [{"id": "impl", "role": "coder", "task": "实现原型", "depends_on": []},
                           {"id": "verify", "role": "tester", "task": "验证原型", "depends_on": ["impl"]}]})
        self.assertIn("small", [item["id"] for item in state["options"]])
        chosen = self.manager.choose(state["workflow_id"], "small")
        self.assertEqual(chosen["status"], "planned")
        self.assertEqual([task["id"] for task in chosen["tasks"]], ["impl", "verify"])
        self.assertEqual(chosen["graph_state"]["pending_interrupts"][0]["value"]["kind"],
                         "approval")

    def test_graph_provider_retries_are_checkpointed(self):
        research_calls = []
        option_calls = []

        def research(_query):
            research_calls.append(1)
            if len(research_calls) == 1:
                raise RuntimeError("temporary web failure")
            return "第二次检索成功"

        def options(_request, _plan):
            option_calls.append(1)
            if len(option_calls) == 1:
                raise RuntimeError("temporary llm failure")
            return {"options": [{"id": "retry", "title": "重试方案",
                                 "summary": "重试后得到的方案", "recommended": True}]}

        state = self.manager.start("带重试的联网方案", research_runner=research,
                                   option_generator=options,
                                   policy=WorkflowPolicy(provider_retries=2))
        self.assertEqual(len(option_calls), 2)
        researched = self.manager.choose(state["workflow_id"], "web_research")
        self.assertEqual(researched["status"], "awaiting_choice")
        self.assertEqual(len(research_calls), 2)
        self.assertEqual(len(option_calls), 3)
        self.assertEqual(len(researched["graph_state"]["research_attempts"]), 2)

    def test_research_refines_options_with_llm_and_falls_back_on_bad_output(self):
        state = self.manager.start("做一个联网资料驱动的 2D 游戏")
        wid = state["workflow_id"]
        self.manager.choose(wid, "web_research")
        refined = self.manager.apply_research(
            wid, "Godot 4 CharacterBody2D 文档摘要",
            option_generator=lambda _request, _plan: {
                "options": [{"id": "godot", "title": "Godot 原型",
                             "summary": "按官方节点模型实现移动和碰撞", "recommended": True,
                             "source": "web"}]})
        self.assertEqual(refined["events"][-1]["option_source"], "llm")
        self.assertIn("godot", [item["id"] for item in refined["options"]])

    def test_registered_research_runs_inside_graph_and_checkpoints_normalized_output(self):
        state = self.manager.start(
            "先查资料再做 2D 游戏",
            research_runner=lambda _query: "Godot 4 CharacterBody2D 官方文档摘要",
            option_generator=lambda _request, _plan: {
                "options": [{"id": "godot", "title": "Godot 方案",
                             "summary": "基于官方角色节点实现", "recommended": True,
                             "source": "web"}]})
        researched = self.manager.choose(state["workflow_id"], "web_research")
        self.assertEqual(researched["status"], "awaiting_choice")
        self.assertIn("godot", [item["id"] for item in researched["options"]])
        self.assertEqual(researched["graph_state"]["research_findings"],
                         "Godot 4 CharacterBody2D 官方文档摘要")
        self.assertEqual(researched["graph_state"]["option_source"], "llm")
        self.assertEqual(researched["graph_state"]["pending_interrupts"][0]["value"]["kind"],
                         "choice")

    def test_graph_research_failure_keeps_external_research_interrupt(self):
        def fail(_query):
            raise RuntimeError("offline")
        state = self.manager.start("联网失败后等待人工结果", research_runner=fail)
        waiting = self.manager.choose(state["workflow_id"], "web_research")
        self.assertEqual(waiting["status"], "awaiting_research")
        self.assertEqual(waiting["graph_state"]["pending_interrupts"][0]["value"]["kind"],
                         "research")

    def test_dynamic_task_plan_validates_dag_and_uses_safe_fallback(self):
        state = self.manager.start("制作带战斗验证的游戏")
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        planned = self.manager.plan(wid, task_generator=lambda _request, _selected, _plan: {
            "tasks": [
                {"id": "combat", "role": "coder", "task": "实现战斗循环", "depends_on": []},
                {"id": "playtest", "role": "tester", "task": "验证战斗循环", "depends_on": ["combat"]},
            ]})
        self.assertEqual(planned["events"][-1]["task_source"], "llm")
        self.assertEqual([task["id"] for task in planned["tasks"]], ["combat", "playtest"])

        state2 = self.manager.start("制作带循环依赖的游戏")
        wid2 = state2["workflow_id"]
        self.manager.choose(wid2, "recommended")
        fallback = self.manager.plan(wid2, task_generator=lambda *_args: {
            "tasks": [{"id": "a", "role": "coder", "task": "坏图", "depends_on": ["b"]},
                       {"id": "b", "role": "coder", "task": "坏图", "depends_on": ["a"]}]})
        self.assertEqual(fallback["events"][-1]["task_source"], "deterministic")
        self.assertEqual([task["id"] for task in fallback["tasks"]], ["design", "prototype", "verify"])

    def test_dynamic_subagent_policy_is_persisted_and_exposed(self):
        state = self.manager.start("制作需要联网查证的游戏原型")
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        planned = self.manager.plan(wid, [{
            "id": "research", "role": "researcher", "task": "查证引擎输入 API",
            "persona": "严谨的引擎文档考据员", "tools": ["search_code", "web_search"],
            "mcp": "deny", "reflection": True,
        }])
        task = planned["tasks"][0]
        self.assertEqual(task["persona"], "严谨的引擎文档考据员")
        self.assertEqual(task["mcp"], "deny")
        self.assertTrue(task["reflection"])
        self.assertEqual(planned["subagents"][0]["tools"], ["search_code", "web_search"])
        self.assertEqual(planned["subagents"][0]["status"], "pending")

    def test_subagent_roster_is_updated_by_live_lifecycle_events(self):
        state = self.manager.start(
            "实时显示团队执行状态",
            policy=WorkflowPolicy(approval_mode="high", max_subagents=1),
        )
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        self.manager.plan(wid, [{
            "id": "verify", "role": "tester", "task": "运行最小验证",
            "persona": "谨慎的 QA", "mcp": "deny", "reflection": True,
        }])
        done = self.manager.execute(wid, lambda *_: {
            "status": "ok", "conclusion": "验证通过", "steps": 2,
            "reflection": {"ok": True},
        })
        kinds = [event["kind"] for event in done["events"]]
        self.assertIn("subagent_start", kinds)
        self.assertIn("subagent_complete", kinds)
        roster = done["subagents"][0]
        self.assertEqual(roster["status"], "ok")
        self.assertEqual(roster["steps"], 2)
        self.assertTrue(roster["reflection_result"]["ok"])

    def test_langgraph_checkpoint_is_inspectable(self):
        state = self.manager.start("做一个小游戏")
        checkpoint = self.manager.checkpoint(state["workflow_id"])
        self.assertIn(checkpoint["backend"], {"langgraph", "native"})
        if checkpoint["backend"] == "langgraph":
            self.assertEqual(checkpoint["state"]["status"], "awaiting_choice")

    def test_postgres_checkpoint_configuration_degrades_with_diagnostic(self):
        manager = GameWorkflowManager(tempfile.mkdtemp(), checkpoint_dsn="postgresql://invalid")
        try:
            self.assertTrue(manager.persistent_checkpoint)
            self.assertIn(manager.checkpoint_backend, {"langgraph-sqlite", "langgraph-postgres"})
            if manager.checkpoint_backend != "langgraph-postgres":
                self.assertTrue(manager.checkpoint_error)
        finally:
            manager.close()

    def test_checkpoint_health_reports_backend_and_strict_mode(self):
        health = self.manager.checkpoint_health(probe=False)
        self.assertIn(health["backend"], {"langgraph-sqlite", "langgraph-memory", "native"})
        self.assertFalse(health["required"])
        with patch.dict(os.environ, {"DOCMIND_CHECKPOINT_POSTGRES_REQUIRED": "1"}, clear=False), \
                patch("agent_runtime.game_workflow.PostgresSaver", None), \
                patch("agent_runtime.game_workflow.ConnectionPool", None):
            with self.assertRaises(RuntimeError):
                GameWorkflowManager(tempfile.mkdtemp(), checkpoint_dsn="postgresql://required")

    def test_workflow_hook_can_block_approval_by_policy(self):
        state = self.manager.start(
            "钩子审核", policy=WorkflowPolicy(hook_failure="block"))
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        self.manager.plan(wid, [{"id": "a", "task": "验证"}])
        self.manager.execute(wid, lambda *_: {"status": "ok", "conclusion": "ok", "steps": 1})
        with patch("hooks.run_workflow", return_value={
                "blocked": True, "reason": "需要人工确认", "errors": []}):
            with self.assertRaises(WorkflowError):
                self.manager.approve(wid, True)
        self.assertEqual(self.manager.get(wid)["status"], "interrupted")

    def test_langgraph_checkpoint_preserves_research_gate(self):
        state = self.manager.start("联网后再决定")
        wid = state["workflow_id"]
        self.manager.choose(wid, "web_research")
        checkpoint = self.manager.checkpoint(wid)
        if checkpoint["backend"] == "langgraph":
            self.assertEqual(checkpoint["state"]["phase"], "research")
            self.assertEqual(checkpoint["state"]["status"], "awaiting_research")
            self.assertEqual(checkpoint["state"]["event"], "research_gate")

    def test_human_gates_resume_from_sqlite_after_manager_restart(self):
        state_root = tempfile.mkdtemp()
        first_manager = GameWorkflowManager(state_root)
        started = first_manager.start("联网后再决定")
        wid = started["workflow_id"]
        self.assertEqual(started["graph_state"]["pending_interrupts"][0]["value"]["kind"], "choice")
        first_manager.choose(wid, "web_research")
        restarted = GameWorkflowManager(state_root)
        checkpoint = restarted.checkpoint(wid)
        self.assertEqual(checkpoint["state"]["pending_interrupts"][0]["value"]["kind"], "research")
        researched = restarted.apply_research(wid, "外部资料摘要")
        self.assertEqual(researched["graph_state"]["pending_interrupts"][0]["value"]["kind"], "choice")
        selected = restarted.choose(wid, "recommended")
        self.assertEqual(selected["graph_state"]["pending_interrupts"][0]["value"]["kind"], "plan")
        plan_restarted = GameWorkflowManager(state_root)
        planned = plan_restarted.plan(wid, [{"id": "write", "task": "写入原型"}])
        self.assertEqual(planned["graph_state"]["pending_interrupts"][0]["value"]["kind"], "approval")
        waiting = plan_restarted.execute(wid, lambda *_: {"status": "ok", "conclusion": "ok", "steps": 1})
        self.assertEqual(waiting["graph_state"]["pending_interrupts"][0]["value"]["kind"], "approval")
        restarted_again = GameWorkflowManager(state_root)
        approved = restarted_again.approve(wid, True)
        self.assertEqual(approved["status"], "planned")
        self.assertFalse(approved["graph_state"].get("pending_interrupts"))

    def test_mutating_tool_result_is_replayed_by_durable_idempotency_key(self):
        calls = []
        def mutating(argument):
            calls.append(argument)
            return "written"
        with tempfile.TemporaryDirectory() as storage:
            with tool_idempotency_scope("wf-1:task-1", storage):
                first = execute_tool(mutating, "file=a", lambda _: False,
                                     tool_name="create_file", side_effect=SideEffect.MUTATING)
                second = execute_tool(mutating, "file=a", lambda _: False,
                                      tool_name="create_file", side_effect=SideEffect.MUTATING)
        self.assertEqual(calls, ["file=a"])
        self.assertFalse(first.replayed)
        self.assertTrue(second.replayed)
        self.assertEqual(first.idempotency_key, second.idempotency_key)

    def test_task_context_compression_preserves_bounded_evidence(self):
        one = compress_text("头部证据" + ("x" * 500) + "尾部证据", 180)
        self.assertTrue(one.truncated)
        self.assertLessEqual(one.compressed_chars, 180)
        self.assertIn("头部证据", one.text)
        self.assertIn("尾部证据", one.text)
        many, meta = compress_context({"a": "a" * 1000, "b": "b" * 1000}, 300)
        self.assertLessEqual(sum(len(value) for value in many.values()), 300)
        self.assertTrue(meta["truncated"])

    def test_context_layer_budget_and_async_compression(self):
        budgets = allocate_layer_budgets(6000)
        self.assertEqual(set(budgets), {"route", "project", "task", "subagent", "output"})
        self.assertEqual(sum(budgets.values()), 6000)
        future = compress_context_async({"dependency": "x" * 2000}, 240)
        bounded, meta = future.result(timeout=2)
        self.assertLessEqual(sum(len(value) for value in bounded.values()), 240)
        self.assertTrue(meta["truncated"])

    def test_durable_compression_queue_persists_job_and_verifies_result(self):
        with tempfile.TemporaryDirectory() as storage:
            db = os.path.join(storage, "compression.sqlite3")
            snapshots, meta = compress_layers_durable(
                {"route": "route " * 1000, "project": {"root": "D:/game"}},
                500, db_path=db, timeout=2)
            self.assertEqual(set(snapshots), {"route", "project", "task", "subagent", "output"})
            self.assertEqual(meta["result_digest"], layer_snapshot_digest(snapshots))
            self.assertIn(meta["job_state"], {"completed", "running", "queued"})
            row = persistent_compression_worker(db).get(meta["job_id"])
            self.assertIsNotNone(row)
            self.assertIn(row["state"], {"completed", "running", "queued"})
            old_worker = persistent_compression_worker(db)
            old_worker.close()
            self.assertIsNot(persistent_compression_worker(db), old_worker)
            persistent_compression_worker(db).close()

    def test_recovery_rebuilds_missing_context_layers(self):
        state = self.manager.start("恢复上下文", project_id="p1", project_root="D:/game")
        wid = state["workflow_id"]
        path = self.manager._path(wid)
        raw = __import__("json").loads(path.read_text(encoding="utf-8"))
        raw["context_layers"] = {}
        path.write_text(__import__("json").dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.manager._states.pop(wid, None)
        recovered = self.manager.get(wid)
        self.assertEqual(recovered["context_layers"]["schema"], "five-layer-v1")
        self.assertEqual(sum(recovered["context_layers"]["budgets"].values()), 6000)
        self.assertEqual(recovered["context_layers"]["project"]["project_id"], "p1")

    def test_cross_manager_recovery_rebuilds_and_verifies_snapshot_body(self):
        """模拟窗口/进程重启：五层快照正文、摘要和 digest 必须一致。"""
        state_root = tempfile.mkdtemp()
        first = GameWorkflowManager(state_root)
        started = first.start("跨窗口恢复快照", project_id="p-recover", project_root="D:/game")
        wid = started["workflow_id"]
        path = first._path(wid)
        raw = __import__("json").loads(path.read_text(encoding="utf-8"))
        # 模拟旧版本/中断写入：逻辑层还在，但快照正文被截断且元数据缺失。
        raw["context_layers"].pop("snapshots", None)
        raw["context_layers"].pop("snapshot_meta", None)
        raw["context_layers"].pop("snapshot_digest", None)
        path.write_text(__import__("json").dumps(raw, ensure_ascii=False), encoding="utf-8")
        first.close()

        second = GameWorkflowManager(state_root)
        recovered = second.get(wid)
        layers = recovered["context_layers"]
        self.assertEqual(set(layers["snapshots"]), {"route", "project", "task", "subagent", "output"})
        self.assertTrue(layers["snapshot_meta"]["valid"])
        self.assertEqual(layers["snapshot_meta"]["digest"],
                         layer_snapshot_digest(layers["snapshots"]))
        self.assertEqual(layers["snapshot_digest"], layers["snapshot_meta"]["digest"])
        self.assertEqual(layers["snapshot_meta"]["job_state"], "recovery_sync")
        second.close()

    def test_five_layer_snapshot_is_bounded_and_recovery_verified(self):
        snapshots, meta = compress_layers({
            "route": "route " * 1000,
            "project": {"root": "D:/game", "files": ["player.gd"]},
            "task": "task " * 1000,
            "subagent": "result " * 1000,
            "output": "output " * 1000,
        }, 1200)
        self.assertEqual(set(snapshots), {"route", "project", "task", "subagent", "output"})
        self.assertLessEqual(sum(len(value) for value in snapshots.values()), 1200)
        self.assertEqual(meta["digest"], layer_snapshot_digest(snapshots))
        self.assertTrue(meta["truncated"])

        state = self.manager.start("快照恢复", project_id="snapshot", project_root="D:/game")
        saved = self.manager.get(state["workflow_id"])
        layer_meta = saved["context_layers"]["snapshot_meta"]
        self.assertTrue(layer_meta["valid"])
        self.assertEqual(layer_meta["digest"],
                         layer_snapshot_digest(saved["context_layers"]["snapshots"]))

    def test_dag_revision_is_staged_until_approval(self):
        state = self.manager.start("安全修改任务", policy=WorkflowPolicy(approval_mode="high"))
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        self.manager.plan(wid, [{"id": "old", "role": "coder", "task": "旧任务"}])
        staged = self.manager.request_dag_revision(wid, [
            {"id": "new", "role": "tester", "task": "新任务"},
        ])
        self.assertEqual(staged["status"], "interrupted")
        self.assertEqual(staged["tasks"][0]["id"], "old")
        self.assertEqual(staged["pending_tasks"][0]["id"], "new")
        approved = self.manager.approve_dag_revision(wid, True)
        self.assertEqual(approved["status"], "planned")
        self.assertEqual(approved["tasks"][0]["id"], "new")
        self.assertFalse(approved["pending_tasks"])

    def test_langgraph_execution_loop_replans_with_bounded_counters(self):
        graph = self.manager.build_langgraph()
        if graph is None:
            self.skipTest("LangGraph 未安装")
        state = graph.invoke(
            {"workflow_id": "loop-test", "selected_option": {"id": "recommended"},
             "approved": True, "failed_tasks": ["prototype"],
             "review": {"ok": False}, "max_replans": 1, "max_steps": 3},
            config={"configurable": {"thread_id": "loop-test"}},
        )
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["replan_count"], 1)
        self.assertEqual(state["step_count"], 2)
        self.assertEqual(state["event"], "fail")

    def test_execute_uses_langgraph_for_single_wave_replan_and_context(self):
        if self.manager.build_langgraph() is None:
            self.skipTest("LangGraph 未安装")
        state = self.manager.start(
            "验证失败后补救",
            policy=WorkflowPolicy(approval_mode="high", max_replans=1, max_steps=4),
        )
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        self.manager.plan(wid, [{"id": "broken", "role": "coder", "task": "制造一个可观察失败"}])
        calls = []

        def runner(task, context):
            calls.append((task["id"], dict(context)))
            if task["id"] == "broken":
                return {"status": "failed", "conclusion": "失败诊断", "error": "可恢复错误", "steps": 1}
            return {"status": "ok", "conclusion": "补救完成", "steps": 1}

        done = self.manager.execute(
            wid, runner,
            replanner=lambda failed, results, attempt: [
                {"id": "repair", "role": "coder", "task": "修复失败任务",
                 "depends_on": ["broken"]}
            ],
        )
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["replans"], 1)
        self.assertEqual([item[0] for item in calls], ["broken", "repair"])
        self.assertNotIn("broken", calls[0][1])
        self.assertIn("失败诊断", calls[1][1]["broken"])
        self.assertIn("状态：failed", calls[1][1]["broken"])
        self.assertEqual(done["results"]["revisions"][0]["status"], "applied")
        self.assertTrue(any(event["kind"] == "graph_replan_start" for event in done["events"]))
        self.assertEqual(len(done["results"]["task_threads"]), 2)
        self.assertNotEqual(done["results"]["task_threads"][0], done["results"]["task_threads"][1])
        repaired = next(item for item in done["subagents"] if item["id"] == "repair")
        self.assertEqual(repaired["status"], "ok")
        self.assertTrue(any(item.get("id") == "repair" for item in done["tasks"]))

    def test_subagent_summary_keeps_status_and_bounds_text(self):
        summary, meta = summarize_subagent_result(
            {"status": "failed", "conclusion": "诊断" + ("x" * 2000),
             "steps": 4, "trace": {"steps": [{"ok": False}]}}, 180)
        self.assertIn("状态：failed", summary)
        self.assertLessEqual(len(summary), 180)
        self.assertEqual(meta["failed_steps"], 1)

    def test_execute_langgraph_stops_when_replan_is_invalid(self):
        if self.manager.build_langgraph() is None:
            self.skipTest("LangGraph 未安装")
        state = self.manager.start(
            "无效补救不能无限循环",
            policy=WorkflowPolicy(approval_mode="high", max_replans=3, max_steps=10),
        )
        wid = state["workflow_id"]
        self.manager.choose(wid, "recommended")
        self.manager.plan(wid, [{"id": "broken", "role": "coder", "task": "失败任务"}])
        done = self.manager.execute(
            wid, lambda _task, _context: {"status": "failed", "error": "失败", "steps": 1},
            replanner=lambda *_args: [],
        )
        self.assertEqual(done["status"], "failed")
        self.assertEqual(done["replans"], 1)
        self.assertEqual(done["graph_state"]["event"], "fail")
        self.assertEqual(done["graph_state"]["replan_error"], "empty_or_invalid_proposal")

    def test_langgraph_send_fanout_runs_independent_tasks_in_one_wave(self):
        if self.manager.build_langgraph() is None:
            self.skipTest("LangGraph 未安装")
        calls = []
        report = self.manager._run_task_dag_langgraph(
            [
                {"id": "art", "role": "artist", "task": "准备素材"},
                {"id": "audio", "role": "audio", "task": "准备音频"},
            ],
            lambda task, _context: (calls.append(task["id"]) or {
                "status": "ok", "conclusion": task["id"], "steps": 1,
            }),
            max_steps=4,
            max_parallel=2,
        )
        self.assertEqual(report["backend"], "langgraph_send")
        self.assertEqual(len(report["waves"]), 1)
        self.assertCountEqual(calls, ["art", "audio"])
        self.assertEqual(report["n_ok"], 2)
        self.assertEqual(len(report["task_threads"]), 1)

    def test_langgraph_fanout_emits_live_subagent_lifecycle_events(self):
        if self.manager.build_langgraph() is None:
            self.skipTest("LangGraph 未安装")
        events = []
        report = self.manager._run_task_dag_langgraph(
            [{"id": "design", "role": "designer", "persona": "严谨设计师",
              "tools": ["read_file"], "mcp": "deny", "reflection": True,
              "task": "输出验收标准"}],
            lambda _task, _context: {"status": "ok", "conclusion": "标准已确认",
                                     "steps": 1, "reflection": {"ok": True}},
            max_steps=3, max_parallel=1, on_event=lambda kind, payload: events.append((kind, payload)),
        )
        kinds = [kind for kind, _payload in events]
        self.assertEqual(report["n_ok"], 1)
        self.assertIn("context_prepared", kinds)
        self.assertIn("wave_start", kinds)
        self.assertIn("subagent_start", kinds)
        self.assertIn("subagent_complete", kinds)
        self.assertIn("wave_complete", kinds)
        started = next(payload for kind, payload in events if kind == "subagent_start")
        self.assertEqual(started["task_id"], "design")
        self.assertEqual(started["mcp"], "deny")
        self.assertIn("context_chars", started)
        self.assertTrue(started["task_thread"].endswith(":design"))

    def test_dispatcher_expands_langgraph_wave_dynamically(self):
        if self.manager.build_langgraph() is None:
            self.skipTest("LangGraph 未安装")
        calls = []
        events = []

        def runner(task, context):
            calls.append((task["id"], dict(context)))
            if task["id"] == "dispatch":
                return {"status": "ok", "steps": 1, "conclusion":
                        '{"tasks":[{"id":"code","role":"coder","task":"修改文件"},'
                        '{"id":"verify","role":"tester","task":"验证文件",'
                        '"depends_on":["code"]}]}'}
            return {"status": "ok", "steps": 1, "conclusion": "完成 " + task["id"]}

        report = self.manager._run_task_dag_langgraph(
            [{"id": "dispatch", "role": "dispatcher", "task": "拆解文件并分派任务"}],
            runner, max_steps=6, max_parallel=2,
            on_event=lambda kind, payload: events.append((kind, payload)),
        )
        self.assertEqual(report["n_ok"], 3)
        self.assertEqual([item[0] for item in calls], ["dispatch", "code", "verify"])
        self.assertIn("dispatch", calls[1][1])
        self.assertEqual(report["dispatches"][0]["added"], ["code", "verify"])
        self.assertTrue(any(kind == "dispatch" for kind, _ in events))


if __name__ == "__main__":
    unittest.main()
