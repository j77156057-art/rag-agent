"""Agent policy, approvals and connector discovery endpoints."""
import asyncio
import json
import queue
import threading
from dataclasses import replace
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from agent_runtime import langsmith
from agent_runtime.game_workflow import (WORKFLOWS, StateGraph, WorkflowError,
                                          WorkflowPolicy, review_output)
from agent_runtime.workflow_profiles import get_profile, normalize_kind
from agent_runtime.retrieval import retrieve_context, status as retrieval_status
from agent_runtime.retrieval_eval import compare_reports, evaluate_modes
from agent_runtime.workflow_eval import DEFAULT_DATASET_NAME, dataset_cases
from agent_runtime.tool_install import ToolInstallError, ToolInstallManager
from agent_runtime.local_runtime import effective_subagent_limit, resource_profile
from config import COLLECTION_NAME, LLM_MODEL, LLM_PROVIDER, PROVIDERS, get_runtime
from game_workbench import approval as record_user_approval
from game_workbench import list_approval_records, require_approval
from agent_runtime.cockpit_policy import QUEUE_APPROVABLE, pending_gate_requests
import projects
import tools
from starlette.concurrency import run_in_threadpool
from tools import web_research


class AgentRouteReq(BaseModel):
    prompt: str = ""
    files: list = []
    requested: str = "auto"


class WorkflowStartReq(BaseModel):
    prompt: str = ""
    project_id: str = ""
    web_enabled: bool = False
    use_llm: bool = True
    experience_enabled: bool = True
    policy: dict = {}
    # 领域画像：generic（默认）/ game；未知值由管理器归一为 generic，不报错。
    kind: str = "generic"


class WorkflowChoiceReq(BaseModel):
    choice: str
    custom_request: str = ""


class WorkflowResearchReq(BaseModel):
    findings: str
    source: str = "web"


class WorkflowResearchRunReq(BaseModel):
    query: str = ""
    max_chars: int = 8000


class WorkflowPlanReq(BaseModel):
    tasks: list[dict] | None = None


class WorkflowDagRevisionReq(BaseModel):
    tasks: list[dict]
    approved: bool = False


class WorkflowApprovalReq(BaseModel):
    approved: bool = False
    auto_execute: bool = False


class WorkflowAcceptanceReq(BaseModel):
    items: list[dict]


class WorkflowFinalAcceptanceReq(BaseModel):
    approved: bool = False
    note: str = ""


class WorkflowVisualFeedbackReq(BaseModel):
    id: str = ""
    label: str = "实时画面"
    note: str = ""
    region: dict | None = None
    screenshot: bool = False
    sent_at: str = ""
    artifact_id: str = ""


class WorkflowVisualSnapshotReq(BaseModel):
    image_base64: str = Field(max_length=11184900)


class WorkflowVisualFeedbackStatusReq(BaseModel):
    status: str
    detail: str = ""


class WorkflowCheckpointReq(BaseModel):
    approved: bool | None = None


class WorkflowProjectRollbackReq(BaseModel):
    approved: bool = False


class WorkflowInterruptReq(BaseModel):
    reason: str = "用户请求中断"


class WorkflowExecuteReq(BaseModel):
    session_id: str = "workflow"


class WorkflowSubagentRetryReq(BaseModel):
    session_id: str = "workflow"


class WorkflowSkillApprovalReq(BaseModel):
    approved: bool = False


class WorkflowDatasetSyncReq(BaseModel):
    dataset_name: str = ""


class RetrievalEvaluateReq(BaseModel):
    cases: list[dict] = []
    collection: str = COLLECTION_NAME
    top_k: int = 5
    modes: list[str] = ["dense", "hybrid"]
    metric: str = "mrr"
    ks: list[int] = [1, 3, 5]


class ToolInstallReq(BaseModel):
    manager: str
    package: str
    version: str = ""
    fallback_tools: list[str] = []
    approved: bool = False


class CockpitApprovalDecisionReq(BaseModel):
    id: str
    approved: bool


def build_router(ctx) -> APIRouter:
    router = APIRouter(prefix="/api/agent", tags=["agent"])

    def workflow_evidence(prompt: str, project_id: str = "", project_root: str = "") -> str:
        """Build bounded local evidence for planning prompts only."""
        collections = {"knowledge": COLLECTION_NAME}
        if project_root:
            pid = project_id or projects.project_id(project_root)
            collections["code"] = projects.code_collection(pid)
        try:
            return retrieve_context(prompt, collections=collections, top_k=4, max_chars=2600)
        except Exception:
            return ""

    def workflow_callbacks(state, session_id=""):
        """Rebuild execution callbacks from durable workflow/session IDs."""
        sid = session_id or state.get("execution_session_id") or "workflow"
        agent = ctx._agent_for(sid, state.get("project_id") or None)

        wid = str((state or {}).get("workflow_id") or "")

        def _step_sink(task_id, role):
            if not wid:
                return None
            return lambda item: WORKFLOWS.record_agent_step(wid, task_id, role, item)

        def runner(task, context):
            prompt = str(task.get("task") or "")
            # 租约在执行开始时才创建；回调可能早于执行阶段构造，
            # 因此每个子任务都从持久工作流状态同步最新租约。
            try:
                agent.capability_lease = dict(
                    (WORKFLOWS.get(wid).get("capability_lease") or {}) if wid else {})
            except Exception:
                agent.capability_lease = dict(state.get("capability_lease") or {})
            out = agent._run_child(
                task.get("role", "coder"), prompt, context=context,
                persona=task.get("persona", ""),
                tool_allowlist=task.get("tools"),
                mcp_policy=task.get("mcp", "auto"),
                reflect=bool(task.get("reflection", True)),
                max_steps=task.get("max_steps"),
                step_sink=_step_sink(str(task.get("id") or ""),
                                     str(task.get("role") or "coder")))
            check = review_output(out, max_tool_failures=int(
                (state.get("policy") or {}).get("max_tool_failures", 3)))
            out = dict(out or {})
            out["review"] = check
            if not check.get("ok"):
                out["status"] = "failed"
                out["error"] = out.get("error") or "子代理输出未通过 Harness 复核"
            return out

        return {
            "runner": runner,
            "synth_runner": lambda tasks, results: agent._synth(tasks, results),
            "replanner": lambda failed, results, attempt: agent._replanner(
                failed, results, attempt),
        }

    # Approval may arrive after a process restart.  The manager persists only
    # the session identifier and asks this factory to recreate the callbacks.
    WORKFLOWS.set_execution_resolver(lambda state: workflow_callbacks(state))

    def launch_workflow(*, prompt, kind, web_enabled, use_llm, experience_enabled,
                        project_id, policy, schedule_options):
        """HTTP 端点与对话内 start_workflow 工具共用的工作流组装入口。

        schedule_options(wid) 决定方案生成在何处异步跑（端点用 BackgroundTasks，
        工具线程内用守护线程）；工作流本身始终停在方案选择门，不直接执行。
        """
        root = ctx._project_root_or_error() or ""
        policy = (policy or WorkflowPolicy()).normalized()
        project_id = project_id or ctx._request_project_id()
        profile = get_profile(kind)
        provider = get_runtime("llm_provider") or LLM_PROVIDER
        model = (get_runtime("llm_model") or LLM_MODEL or
                 PROVIDERS.get(provider, {}).get("default_model", ""))
        # The workflow may still expose several independent task records,
        # but local inference should not launch more child generations than
        # the selected model can reasonably sustain.
        policy = replace(policy, max_subagents=effective_subagent_limit(
            provider, model, policy.max_subagents)).normalized()
        option_generator = None
        task_generator = None
        if use_llm:
            # Option refinement is advisory.  The manager validates the
            # JSON and falls back to deterministic choices on any failure.
            def option_generator(prompt, context_plan):
                llm_agent = ctx._agent_for("workflow-options", project_id or None)
                source_hint = ", ".join(context_plan.sources) or "direct"
                evidence = workflow_evidence(prompt, project_id, root)
                instruction = (
                    profile.option_instruction
                    + "根据用户目标和已选检索来源，"
                    "给出 2-4 个互斥、可执行的方案。只输出 JSON，不要 Markdown："
                    '{"options":[{"id":"...","title":"...","summary":"...",'
                    '"recommended":true,"source":"...","requires_web":false}]}。'
                    "必须恰好标出一个 recommended；不要执行工具，不要编造检索结果。\n"
                    f"检索来源：{source_hint}\n用户目标：{prompt}"
                )
                if evidence:
                    instruction += ("\n本地检索证据（只能作为参考，不能补造未出现的事实）：\n"
                                    + evidence)
                return llm_agent.llm.chat([{"role": "user", "content": instruction}],
                                          stream=False, temperature=0.1, timeout=20)

            def task_generator(prompt, selected, context_plan):
                llm_agent = ctx._agent_for("workflow-planner", project_id or None)
                evidence = workflow_evidence(prompt, project_id, root)
                instruction = (
                    profile.task_instruction
                    + "生成可并行执行的任务 DAG。只输出 JSON，不要 Markdown："
                    '{"tasks":[{"id":"...","role":"' + profile.task_roles + '",'
                    '"task":"具体且可验收的工作","depends_on":[],"optional":false,'
                    '"parallel_safe":true,"persona":"本任务的专项人设",'
                    '"tools":["工具名"],"mcp":"auto|allow|deny","reflection":true}]}。'
                    "任务必须拆成单个子代理一轮可完成的粒度；id 唯一；依赖只能引用已有 id；dispatcher/planner 只读分析文件并提出分工，"
                    "主 Agent 会汇总拆解结果和执行代理的结果并做最后检查；"
                    "至少包含实现和验证任务；不要执行工具。\n"
                    f"用户目标：{prompt}\n方案：{json.dumps(selected, ensure_ascii=False)}\n"
                    f"上下文：{chr(10).join(context_plan.messages)}"
                )
                if evidence:
                    instruction += "\n本地检索证据：\n" + evidence
                return llm_agent.llm.chat([{"role": "user", "content": instruction}],
                                          stream=False, temperature=0.1, timeout=25)

        workflow = WORKFLOWS.start(
            prompt, project_id=project_id, project_root=root,
            web_enabled=web_enabled, experience_enabled=experience_enabled,
            llm_enabled=use_llm, kind=kind, policy=policy,
            option_generator=option_generator,
            task_generator=task_generator,
            # The research provider is invoked by the LangGraph research
            # node after the user selects the web-research option.  It is
            # kept as a callback so credentials/cache policy remain in the
            # audited tool implementation and never enter a checkpoint.
            research_runner=(web_research if web_enabled else None),
            defer_option_generation=True)
        if workflow.get("status") == "generating_options":
            schedule_options(workflow["workflow_id"])
        return workflow

    def _workflow_launcher(goal, *, kind="generic", web_enabled=False):
        """start_workflow 工具的注入实现：复用同一套组装，方案生成放守护线程。"""
        def _spawn_options(wid):
            threading.Thread(
                target=WORKFLOWS.generate_options, args=(wid,),
                name="workflow-options", daemon=True).start()

        return launch_workflow(
            prompt=goal, kind=normalize_kind(kind), web_enabled=bool(web_enabled),
            use_llm=True, experience_enabled=True, project_id="", policy=None,
            schedule_options=_spawn_options)

    tools.set_workflow_launcher(_workflow_launcher)

    @router.post("/route")
    async def route(req: AgentRouteReq):
        return ctx.route_for(req.prompt, req.files, req.requested)

    @router.post("/permission")
    async def permission(payload: dict):
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "allowed": False, "reason": "未配置代码库"}
        approved = bool(payload.get("approved", False))
        if payload.get("approval_id"):
            approved = ctx.approval_allows(root, payload.get("approval_id"), payload.get("path", ""))
        return ctx.record_permission(payload.get("path", ""), root,
                                     bool(payload.get("allow_external", False)), approved)

    @router.get("/routing")
    async def routing_status():
        return {"ok": True, **ctx.routing_status()}

    @router.get("/langsmith")
    async def langsmith_status():
        return {"ok": True, **langsmith.status()}

    @router.post("/tools/install/plan")
    async def tool_install_plan(req: ToolInstallReq):
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "error": "未配置代码库"}
        try:
            return {"ok": True, "plan": ToolInstallManager(root).plan(req.dict())}
        except (ToolInstallError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/tools/install")
    async def tool_install(req: ToolInstallReq):
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "error": "未配置代码库"}
        try:
            manager = ToolInstallManager(root)
            request = req.dict()
            plan = manager.plan(request)
            gate = require_approval(root, "install_tool", plan["approval_target"])
            if gate:
                return {"ok": False, **gate, "plan": plan}
            return manager.install(request, approved=True)
        except (ToolInstallError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/tools/install/audit")
    async def tool_install_audit(limit: int = 50):
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "error": "未配置代码库"}
        return {"ok": True, "items": ToolInstallManager(root).audit(limit)}

    @router.get("/workflow/backend")
    async def workflow_backend():
        provider = get_runtime("llm_provider") or LLM_PROVIDER
        model = (get_runtime("llm_model") or LLM_MODEL or
                 PROVIDERS.get(provider, {}).get("default_model", ""))
        return {"ok": True, "backend": "langgraph" if StateGraph is not None else "native",
                "langgraph_installed": StateGraph is not None,
                "checkpoint_backend": WORKFLOWS.checkpoint_backend,
                "checkpoint_error": WORKFLOWS.checkpoint_error,
                "persistent_checkpoint": WORKFLOWS.persistent_checkpoint,
                "distributed_leases": WORKFLOWS.distributed_leases,
                "llm_resource": resource_profile(provider, model).as_dict(),
                "checkpoint_health": WORKFLOWS.checkpoint_health(),
                "langsmith": langsmith.status()}

    @router.post("/workflow/start")
    async def workflow_start(req: WorkflowStartReq, background_tasks: BackgroundTasks):
        try:
            workflow = await run_in_threadpool(
                launch_workflow,
                prompt=req.prompt, kind=normalize_kind(req.kind),
                web_enabled=req.web_enabled, use_llm=req.use_llm,
                experience_enabled=req.experience_enabled,
                project_id=req.project_id,
                policy=WorkflowPolicy(**(req.policy or {})),
                schedule_options=lambda wid:
                    background_tasks.add_task(WORKFLOWS.generate_options, wid))
            return {"ok": True, "workflow": workflow}
        except (WorkflowError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/workflow/active")
    async def workflow_active():
        """当前项目进程内未终结的工作流（无则 workflow=null）。

        页面刷新后工作流卡片随内存消息一起消失，但后端互斥仍然存活，
        前端据此发现孤儿并提供「挂回卡片 / 中断」入口。必须注册在
        /workflow/{workflow_id} 之前，否则会被路径参数吞掉。
        """
        root = ctx._project_root_or_error() or ""
        project_id = ctx._request_project_id()
        workflow = await run_in_threadpool(WORKFLOWS.active_for_project, project_id, root)
        return {"ok": True, "workflow": workflow}

    @router.get("/workflows")
    async def workflow_list(limit: int = 50):
        """当前项目最近的工作流摘要（含磁盘上的终态历史）。

        复数路径刻意区别于 /workflow/{workflow_id}，供工作台「工作流历史」
        弹层展示、中断运行中项、删除终态项。
        """
        root = ctx._project_root_or_error() or ""
        project_id = ctx._request_project_id()
        items = await run_in_threadpool(WORKFLOWS.list_workflows, project_id, root, limit)
        return {"ok": True, "items": items}

    @router.get("/workflow/{workflow_id}")
    async def workflow_get(workflow_id: str):
        try:
            return {"ok": True, "workflow": WORKFLOWS.get(workflow_id)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/workflow/{workflow_id}/events")
    async def workflow_events(workflow_id: str, request: Request, after: int = 0):
        """工作流实时事件 SSE。

        - 连接先重放 seq>after 的持久事件（含各 subagent_complete 的有界 trace）；
        - 随后实时推送持久事件与内存 subagent_step；
        - 15s 心跳；终态（completed/failed/interrupted）推完发 __stream_done__ 自关；
        - 客户端断开即注销队列。注册先于重放，按 seq 去重，无丢事件/无裂缝。
        """
        try:
            WORKFLOWS.get(workflow_id)
        except WorkflowError:
            return JSONResponse({"ok": False, "error": "工作流不存在"}, status_code=404)
        q = WORKFLOWS.subscribe(workflow_id)
        loop = asyncio.get_event_loop()

        def _drain(timeout):
            return q.get(timeout=timeout)

        async def stream():
            cursor = max(0, int(after or 0))
            try:
                replay, _latest = await run_in_threadpool(
                    WORKFLOWS.events_since, workflow_id, cursor)
                if replay:
                    chunks = []
                    for event in replay:
                        cursor = max(cursor, int(event.get("seq") or 0))
                        chunks.append("data: " + json.dumps(event, ensure_ascii=False) + "\n\n")
                    yield "".join(chunks)
                if await run_in_threadpool(WORKFLOWS.is_terminal, workflow_id):
                    yield "data: " + json.dumps({"kind": "__stream_done__"}) + "\n\n"
                    return
                while True:
                    if await request.is_disconnected():
                        break
                    try:
                        event = await loop.run_in_executor(None, _drain, 15)
                    except queue.Empty:
                        yield ": ping\n\n"
                        continue
                    seq = int(event.get("seq") or 0)
                    if seq <= cursor:
                        continue  # 重放/订阅交叉窗口的重复事件
                    cursor = max(cursor, seq)
                    yield "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                    if await run_in_threadpool(WORKFLOWS.is_terminal, workflow_id):
                        yield "data: " + json.dumps({"kind": "__stream_done__"}) + "\n\n"
                        break
            finally:
                await run_in_threadpool(WORKFLOWS.unsubscribe, workflow_id, q)

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @router.get("/workflow/{workflow_id}/skill-candidate")
    async def workflow_skill_candidate(workflow_id: str):
        try:
            state = WORKFLOWS.get(workflow_id)
            return {"ok": True, "candidate": state.get("skill_candidate") or None}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/workflow/{workflow_id}/evaluation")
    async def workflow_evaluation(workflow_id: str):
        try:
            return {"ok": True, "evaluation": WORKFLOWS.evaluate(workflow_id)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/workflow/evaluation/dataset")
    async def workflow_evaluation_dataset():
        return {"ok": True, "dataset": DEFAULT_DATASET_NAME,
                "cases": dataset_cases(), **langsmith.status()}

    @router.post("/retrieval/evaluate")
    async def retrieval_evaluate(req: RetrievalEvaluateReq):
        """Evaluate dense/hybrid retrieval against a small labelled set."""
        cases = [dict(item or {}) for item in (req.cases or [])]
        if not cases:
            return {"ok": False, "error": "cases 不能为空"}
        modes = [str(item or "").strip().lower() for item in (req.modes or [])]
        modes = [item for item in modes if item]
        if not modes:
            return {"ok": False, "error": "modes 不能为空"}
        metric = str(req.metric or "mrr").strip().lower()
        try:
            report = await run_in_threadpool(
                evaluate_modes, cases, collection=req.collection or COLLECTION_NAME,
                top_k=max(1, int(req.top_k)), modes=modes,
                ks=tuple(max(1, int(item)) for item in (req.ks or [1, 3, 5])))
            comparison = compare_reports(report["modes"], metric=metric)
            return {"ok": True, **report, "metric": metric, "comparison": comparison}
        except (TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/retrieval/status")
    async def retrieval_runtime_status(collection: str = COLLECTION_NAME, top_k: int = 5):
        return {"ok": True, "retrieval": retrieval_status(
            collection=collection or COLLECTION_NAME, top_k=max(1, int(top_k)))}

    @router.post("/workflow/evaluation/dataset/sync")
    async def workflow_evaluation_dataset_sync(req: WorkflowDatasetSyncReq):
        return {"ok": True, **langsmith.sync_workflow_dataset(req.dataset_name or None)}

    @router.post("/workflow/{workflow_id}/skill-candidate/approve")
    async def workflow_skill_candidate_approve(workflow_id: str, req: WorkflowSkillApprovalReq):
        try:
            return {"ok": True, "workflow": WORKFLOWS.approve_skill_candidate(
                workflow_id, req.approved)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/choice")
    async def workflow_choice(workflow_id: str, req: WorkflowChoiceReq):
        try:
            # Choosing an option may resume the graph and invoke a planner/LLM.
            # Keep that synchronous work off the event loop so a slow model does
            # not freeze unrelated UI requests (polling, chat, or file views).
            workflow = await run_in_threadpool(
                WORKFLOWS.choose,
                workflow_id,
                req.choice,
                custom_request=req.custom_request,
            )
            return {"ok": True, "workflow": workflow}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/research")
    async def workflow_research(workflow_id: str, req: WorkflowResearchReq):
        try:
            state = WORKFLOWS.get(workflow_id)
            project_id = state.get("project_id") or ctx._request_project_id()
            def option_generator(prompt, context_plan):
                llm_agent = ctx._agent_for("workflow-options-research", project_id or None)
                evidence = workflow_evidence(prompt, project_id, state.get("project_root") or "")
                instruction = (
                    "你是游戏开发 Harness 的研究后方案设计器。根据用户目标和外部检索摘要，"
                    "重新生成 2-4 个互斥、可执行的方案。只输出 JSON，不要 Markdown："
                    '{"options":[{"id":"...","title":"...","summary":"...",'
                    '"recommended":true,"source":"web","requires_web":false}]}。'
                    "必须恰好一个 recommended；不要编造摘要中没有的事实。\n"
                    f"上下文：{chr(10).join(context_plan.messages)}\n用户目标与检索结果：{prompt}"
                )
                if evidence:
                    instruction += "\n补充的本地检索证据：\n" + evidence
                return llm_agent.llm.chat([{"role": "user", "content": instruction}],
                                          stream=False, temperature=0.1, timeout=20)
            return {"ok": True, "workflow": await run_in_threadpool(
                WORKFLOWS.apply_research, workflow_id, req.findings, source=req.source,
                option_generator=option_generator)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/research/run")
    async def workflow_research_run(workflow_id: str, req: WorkflowResearchRunReq):
        """Execute the existing audited web_research tool, then gate its output.

        The endpoint never writes files; provider/key policy and cache/fallback
        behavior remain centralized in tools.web_research.
        """
        try:
            state = WORKFLOWS.get(workflow_id)
            if state.get("status") != "awaiting_research":
                raise WorkflowError("当前工作流不在等待联网检索")
            query = (req.query or state.get("request") or "").strip()
            if not query:
                raise WorkflowError("检索问题不能为空")
            findings = await run_in_threadpool(web_research, query)
            findings = str(findings or "")[:max(800, min(12000, int(req.max_chars)))]
            if not findings:
                raise WorkflowError("联网检索没有返回结果")
            project_id = state.get("project_id") or ctx._request_project_id()
            def option_generator(prompt, context_plan):
                llm_agent = ctx._agent_for("workflow-options-research", project_id or None)
                evidence = workflow_evidence(prompt, project_id, state.get("project_root") or "")
                instruction = (
                    "你是游戏开发 Harness 的研究后方案设计器。根据用户目标和外部检索摘要，"
                    "重新生成 2-4 个互斥、可执行的方案。只输出 JSON，不要 Markdown："
                    '{"options":[{"id":"...","title":"...","summary":"...",'
                    '"recommended":true,"source":"web","requires_web":false}]}。'
                    "必须恰好一个 recommended；不要编造摘要中没有的事实。\n"
                    f"上下文：{chr(10).join(context_plan.messages)}\n用户目标与检索结果：{prompt}"
                )
                if evidence:
                    instruction += "\n补充的本地检索证据：\n" + evidence
                return llm_agent.llm.chat([{"role": "user", "content": instruction}],
                                          stream=False, temperature=0.1, timeout=20)
            result = await run_in_threadpool(
                WORKFLOWS.apply_research, workflow_id, findings, source="web",
                option_generator=option_generator)
            return {"ok": True, "workflow": result, "findings_chars": len(findings)}
        except (WorkflowError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/plan")
    async def workflow_plan(workflow_id: str, req: WorkflowPlanReq):
        try:
            task_generator = None
            if req.tasks is None:
                state = WORKFLOWS.get(workflow_id)
                project_id = state.get("project_id") or ctx._request_project_id()
                if not state.get("llm_enabled", True):
                    task_generator = None
                else:
                    def task_generator(prompt, selected, context_plan):
                        llm_agent = ctx._agent_for("workflow-planner", project_id or None)
                        evidence = workflow_evidence(prompt, project_id, state.get("project_root") or "")
                        instruction = (
                            "你是游戏开发主 Agent，主要负责汇总规划、审核结果和最终决策。根据用户目标、用户选择的方案和上下文，"
                            "自行判断任务复杂度：简单任务直接执行，复杂文件任务先派一个只读 dispatcher/planner 拆解文件与依赖；"
                            "由它提出后续分工，主 Agent 校验后再动态决定执行型 Subagent 的数量；不要固定生成两个成员。"
                            "生成可并行执行的任务 DAG。只输出 JSON，不要 Markdown："
                            '{"tasks":[{"id":"...","role":"dispatcher|planner|designer|coder|artist|tester|researcher|reviewer",'
                            '"task":"具体且可验收的工作","depends_on":[],"optional":false,'
                            '"parallel_safe":true,"persona":"本任务的专项人设",'
                            '"tools":["工具名"],"mcp":"auto|allow|deny","reflection":true}]}。'
                            "任务必须拆成单个子代理一轮可完成的粒度；id 唯一；依赖只能引用已有 id；dispatcher/planner 只读分析文件并提出分工，"
                            "主 Agent 会汇总拆解结果和执行代理的结果并做最后检查；"
                            "至少包含实现和验证任务；不要执行工具。\n"
                            f"用户目标：{prompt}\n方案：{json.dumps(selected, ensure_ascii=False)}\n"
                            f"上下文：{chr(10).join(context_plan.messages)}"
                        )
                        if evidence:
                            instruction += "\n本地检索证据：\n" + evidence
                        return llm_agent.llm.chat([{"role": "user", "content": instruction}],
                                                  stream=False, temperature=0.1, timeout=25)
            return {"ok": True, "workflow": await run_in_threadpool(
                WORKFLOWS.plan, workflow_id, req.tasks, task_generator=task_generator)}
        except (WorkflowError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/approve")
    async def workflow_approve(workflow_id: str, req: WorkflowApprovalReq):
        try:
            return {"ok": True, "workflow": WORKFLOWS.approve(
                workflow_id, req.approved, auto_execute=req.auto_execute)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/workflow/{workflow_id}/preview")
    async def workflow_preview(workflow_id: str):
        try:
            state = WORKFLOWS.get(workflow_id)
            return {"ok": True, "preview": state.get("preview") or {}}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/workflow/{workflow_id}/preview/artifact/{artifact_id}")
    async def workflow_preview_artifact(workflow_id: str, artifact_id: str):
        """Serve one preview file from the current project, after two checks.

        The artifact must be present in the persisted preview bundle and its
        resolved path must stay below that workflow's project root.  This
        keeps media previews useful for any domain while preventing an
        arbitrary path from becoming a file server.
        """
        try:
            state = WORKFLOWS.get(workflow_id)
        except WorkflowError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        preview = state.get("preview") or {}
        artifact = next((item for item in preview.get("artifacts", [])
                         if str(item.get("id") or "") == artifact_id), None)
        root_text = str(state.get("project_root") or "").strip()
        raw_path = str((artifact or {}).get("path") or "").strip()
        if not artifact or not root_text or not raw_path:
            return JSONResponse({"ok": False, "error": "预览资源不存在"}, status_code=404)
        try:
            root = Path(root_text).resolve()
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                candidate = root / candidate
            candidate = candidate.resolve()
            if candidate != root and root not in candidate.parents:
                return JSONResponse({"ok": False, "error": "预览资源不在当前项目内"}, status_code=403)
            if not candidate.is_file():
                return JSONResponse({"ok": False, "error": "预览资源尚未生成"}, status_code=404)
        except (OSError, ValueError):
            return JSONResponse({"ok": False, "error": "预览资源路径无效"}, status_code=404)
        return FileResponse(candidate, media_type=str(artifact.get("mime") or "application/octet-stream"))

    @router.post("/workflow/{workflow_id}/acceptance")
    async def workflow_acceptance(workflow_id: str, req: WorkflowAcceptanceReq):
        try:
            return {"ok": True, "workflow": WORKFLOWS.set_acceptance_contract(
                workflow_id, req.items)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/acceptance/decide")
    async def workflow_acceptance_decide(workflow_id: str, req: WorkflowFinalAcceptanceReq):
        try:
            return {"ok": True, "workflow": WORKFLOWS.decide_acceptance(
                workflow_id, req.approved, req.note)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/visual-feedback")
    async def workflow_visual_feedback(workflow_id: str, req: WorkflowVisualFeedbackReq):
        try:
            feedback_project(workflow_id)
            saved = WORKFLOWS.record_visual_feedback(workflow_id, req.model_dump())
            return {"ok": True, **saved}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    def feedback_project(workflow_id: str):
        state = WORKFLOWS.get(workflow_id)
        current_root = ctx._project_root_or_error()
        workflow_root = str(state.get("project_root") or "")
        if not current_root or not workflow_root or Path(current_root).resolve() != Path(workflow_root).resolve():
            raise WorkflowError("反馈不属于当前项目")
        return state

    @router.post("/workflow/{workflow_id}/visual-feedback/{feedback_id}/status")
    async def workflow_visual_feedback_status(workflow_id: str, feedback_id: str, req: WorkflowVisualFeedbackStatusReq):
        try:
            feedback_project(workflow_id)
            return {"ok": True, **WORKFLOWS.update_visual_feedback(workflow_id, feedback_id, req.status, req.detail)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/visual-feedback/{feedback_id}/snapshot/{phase}")
    async def workflow_visual_snapshot_save(workflow_id: str, feedback_id: str, phase: str, req: WorkflowVisualSnapshotReq):
        try:
            feedback_project(workflow_id)
            return {"ok": True, **await run_in_threadpool(
                WORKFLOWS.save_visual_snapshot, workflow_id, feedback_id, phase, req.image_base64)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/workflow/{workflow_id}/visual-feedback/{feedback_id}/snapshot/{phase}")
    async def workflow_visual_snapshot_file(workflow_id: str, feedback_id: str, phase: str):
        try:
            feedback_project(workflow_id)
            path = WORKFLOWS.visual_snapshot_path(workflow_id, feedback_id, phase)
            return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
        except WorkflowError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)

    @router.post("/workflow/{workflow_id}/checkpoint")
    async def workflow_checkpoint(workflow_id: str, req: WorkflowCheckpointReq):
        try:
            return {"ok": True, "checkpoint": WORKFLOWS.checkpoint(
                workflow_id, approved=req.approved)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/project-checkpoint")
    async def workflow_project_checkpoint(workflow_id: str):
        try:
            return {"ok": True, "checkpoint": WORKFLOWS.create_project_checkpoint(workflow_id)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/project-rollback")
    async def workflow_project_rollback(workflow_id: str, req: WorkflowProjectRollbackReq):
        try:
            return {"ok": True, "rollback": WORKFLOWS.rollback_project_checkpoint(
                workflow_id, approved=req.approved)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/interrupt")
    async def workflow_interrupt(workflow_id: str, req: WorkflowInterruptReq):
        try:
            return {"ok": True, "workflow": WORKFLOWS.interrupt(workflow_id, req.reason)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.delete("/workflow/{workflow_id}")
    async def workflow_delete(workflow_id: str):
        """删除终态工作流并清理磁盘状态文件；非终态返回 ok:false（请先中断）。"""
        try:
            return await run_in_threadpool(WORKFLOWS.delete_workflow, workflow_id)
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/resume")
    async def workflow_resume(workflow_id: str):
        try:
            return {"ok": True, "workflow": WORKFLOWS.resume(workflow_id)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/revise")
    async def workflow_revise(workflow_id: str, req: WorkflowDagRevisionReq):
        try:
            if req.approved:
                return {"ok": True, "workflow": WORKFLOWS.approve_dag_revision(workflow_id, True)}
            return {"ok": True, "workflow": WORKFLOWS.request_dag_revision(workflow_id, req.tasks)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/revise/approve")
    async def workflow_revise_approve(workflow_id: str, req: WorkflowApprovalReq):
        try:
            return {"ok": True, "workflow": WORKFLOWS.approve_dag_revision(workflow_id, req.approved)}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/execute")
    async def workflow_execute(workflow_id: str, req: WorkflowExecuteReq):
        """Run planned tasks through existing Agent subagent personalities."""
        try:
            state = WORKFLOWS.get(workflow_id)
            callbacks = workflow_callbacks(state, req.session_id)

            result = await run_in_threadpool(
                WORKFLOWS.execute, workflow_id, **callbacks,
                session_id=req.session_id,
            )
            return {"ok": True, "workflow": result}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.post("/workflow/{workflow_id}/subagents/{task_id}/retry")
    async def workflow_subagent_retry(workflow_id: str, task_id: str,
                                      req: WorkflowSubagentRetryReq):
        """Retry one failed/blocked Subagent while preserving other results."""
        try:
            state = WORKFLOWS.get(workflow_id)
            callbacks = workflow_callbacks(state, req.session_id)
            result = await run_in_threadpool(
                WORKFLOWS.retry_subagent, workflow_id, task_id,
                **callbacks, session_id=req.session_id,
            )
            return {"ok": True, "workflow": result}
        except WorkflowError as exc:
            return {"ok": False, "error": str(exc)}

    @router.get("/secrets")
    async def secrets():
        root = ctx._project_root_or_error()
        return {"ok": bool(root),
                "providers": ctx.secrets_store.providers(root) if root else []}

    @router.delete("/secrets/{provider}")
    async def remove_secret(provider: str):
        root = ctx._project_root_or_error()
        return (ctx.secrets_store.remove(root, provider) if root
                else {"ok": False, "error": "未配置代码库"})

    @router.get("/approvals")
    async def approvals():
        root = ctx._project_root_or_error()
        return {"ok": bool(root),
                "approvals": ctx.list_approvals(root) if root else []}

    @router.get("/approval-requests")
    async def approval_requests():
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "error": "未配置代码库", "items": []}
        return {"ok": True, "items": pending_gate_requests(
            root, list_approval_records(root))}

    @router.post("/approval-requests/decide")
    async def approval_request_decide(req: CockpitApprovalDecisionReq):
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "error": "未配置代码库"}
        item = next((row for row in pending_gate_requests(
            root, list_approval_records(root)) if row["id"] == req.id), None)
        if not item:
            return {"ok": False, "error": "待审核请求不存在或已处理"}
        if item["action"] not in QUEUE_APPROVABLE:
            return {"ok": False, "error": "MCP 连接与能力只能在设置页确认"}
        decision = record_user_approval(root, item["action"], "workbench-user",
                                        approved=req.approved, target=item["target"])
        return {"ok": True, "decision": decision}

    @router.post("/approvals")
    async def create_approval(payload: dict):
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "error": "未配置代码库"}
        approval = ctx.create_external_approval(
            root, payload.get("paths", []), payload.get("summary", ""),
            payload.get("diff", ""), payload.get("before", ""), payload.get("after", ""))
        return {"ok": True, "approval": approval}

    @router.post("/approvals/decide")
    async def decide(payload: dict):
        root = ctx._project_root_or_error()
        status = payload.get("status", "")
        if status not in ("approved", "rejected"):
            return {"ok": False, "error": "status 必须是 approved 或 rejected"}
        row = ctx.decide_approval(root, payload.get("id", ""), status) if root else None
        return {"ok": bool(row), "approval": row}

    @router.get("/approvals/{approval_id}")
    async def get_approval(approval_id: str):
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "error": "未配置代码库"}
        row = next((item for item in ctx.list_approvals(root)
                    if item.get("id") == approval_id), None)
        return {"ok": bool(row), "approval": row}

    @router.post("/external-write")
    async def external_write(payload: dict):
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "error": "未配置代码库"}
        return ctx.apply_approved_external(
            root, payload.get("approval_id", ""), payload.get("path", ""),
            payload.get("content"))

    @router.get("/connectors")
    async def connectors():
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "error": "未配置代码库", "connectors": []}
        try:
            rows = ctx.mcp_client.connector_directory(root)
            return {"ok": True, "connectors": [
                {**item, "requires_approval": True} for item in rows]}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "connectors": []}

    @router.get("/connector-route")
    async def connector_route(hint: str = ""):
        root = ctx._project_root_or_error()
        if not root:
            return {"ok": False, "error": "未配置代码库", "matches": []}
        try:
            return {"ok": True, "hint": hint,
                    "matches": ctx.mcp_client.select_connector(root, hint)}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "matches": []}

    return router


__all__ = ["AgentRouteReq", "build_router", "WorkflowStartReq", "WorkflowChoiceReq",
           "RetrievalEvaluateReq"]
