"""多代理编排器：任务图（DAG）调度 + 上游结论注入 + 结果合成。

补齐 `delegate` 的短板：`delegate` 只能一次委派一个子任务（或同层并行扇出），
没有依赖顺序、没有上下游上下文传递、也没有结果仲裁。本模块提供：

- **任务图**：每个任务可声明 `depends_on`（上游任务 id）；
- **分波并行**：`topological_waves()` 把 DAG 分成若干波，同波任务可并行；
- **上下文传递**：执行下游任务时，把它各上游的结论注入子任务提示（解决"子代理不共享父上下文"）；
- **失败阻断**：上游失败/被阻断时，下游任务直接标记 `blocked` 而不空跑（`optional` 上游除外）；
- **结果合成**：可选的 `synth_runner` 把所有结论合并成一段最终答复（并标注冲突）。

**纯调度逻辑，不依赖 Agent**：调用方通过 `runner(task, context)` 回调提供"怎么跑一个子任务"，
因此可完全离线单测（注入假 runner）。循环依赖、id 重复、依赖不存在都会在解析阶段报错。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

MAX_TASKS = int(__import__("os").environ.get("DOCMIND_ORCH_MAX_TASKS", "12"))


class PlanError(ValueError):
    """任务图不合法（空任务、id 重复、依赖缺失、循环依赖、超量）。"""


def _as_deps(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [d.strip() for d in value.replace("，", ",").split(",") if d.strip()]
    if isinstance(value, (list, tuple)):
        return [str(d).strip() for d in value if str(d).strip()]
    raise PlanError(f"depends_on 类型不支持：{type(value).__name__}")


def parse_plan(raw):
    """校验并规范化任务列表。返回 [{id, role, task, depends_on, optional}]。"""
    if isinstance(raw, dict):
        tasks = raw.get("tasks")
    elif isinstance(raw, list):
        tasks = raw
    else:
        raise PlanError("plan 必须是任务数组，或含 tasks 字段的对象")
    if not isinstance(tasks, list) or not tasks:
        raise PlanError("任务列表为空")
    if len(tasks) > MAX_TASKS:
        raise PlanError(f"任务过多（{len(tasks)} > {MAX_TASKS}）")

    out, seen = [], set()
    for i, t in enumerate(tasks):
        if not isinstance(t, dict):
            raise PlanError(f"第 {i + 1} 个任务不是对象")
        tid = str(t.get("id") or f"t{i + 1}").strip()
        if not tid:
            raise PlanError(f"第 {i + 1} 个任务缺少 id")
        if tid in seen:
            raise PlanError(f"任务 id 重复：{tid}")
        seen.add(tid)
        desc = str(t.get("task") or t.get("desc") or "").strip()
        if not desc:
            raise PlanError(f"任务 {tid} 缺少 task 描述")
        out.append({
            "id": tid,
            "role": str(t.get("role") or "researcher").strip().lower(),
            "task": desc,
            "depends_on": _as_deps(t.get("depends_on", t.get("after"))),
            "optional": bool(t.get("optional")),
        })

    ids = {t["id"] for t in out}
    for t in out:
        for d in t["depends_on"]:
            if d == t["id"]:
                raise PlanError(f"任务 {t['id']} 不能依赖自身")
            if d not in ids:
                raise PlanError(f"任务 {t['id']} 依赖不存在的任务 {d}")
    return out


def topological_waves(tasks):
    """把任务分成若干波（同波内无相互依赖，可并行）。循环依赖抛 PlanError。"""
    remaining = {t["id"]: set(t["depends_on"]) for t in tasks}
    waves = []
    while remaining:
        ready = sorted(i for i, deps in remaining.items() if not deps)
        if not ready:
            raise PlanError("存在循环依赖，无法调度：" + "、".join(sorted(remaining)))
        waves.append(ready)
        for i in ready:
            remaining.pop(i)
        for deps in remaining.values():
            deps.difference_update(ready)
    return waves


def run_plan(tasks, runner, synth_runner=None, max_parallel=4, on_event=None):
    """按波执行任务图。

    runner(task, context) -> {"status": "ok"|"failed", "conclusion": str,
                              "steps": int, "error": str}
      context = {上游任务id: 上游结论}（仅含非空结论）
    synth_runner(tasks, results) -> str（可选，做结果合成/冲突标注）

    返回结构化结果；单个任务异常只影响它自己。
    """
    t0 = time.monotonic()
    tasks = list(tasks)
    by_id = {t["id"]: t for t in tasks}
    waves = topological_waves(tasks)
    results = {}
    blocked = {}

    def _emit(kind, payload):
        if on_event:
            try:
                on_event(kind, payload)
            except Exception:  # noqa: BLE001 —— 埋点/通知失败不影响调度
                pass

    def _blocked_reason(tid):
        for d in by_id[tid]["depends_on"]:
            if d in blocked:
                return f"上游 {d} 被阻断"
            r = results.get(d)
            if r and r.get("status") not in ("ok",) and not by_id[d].get("optional"):
                return f"上游 {d} {r.get('status')}"
        return None

    for wi, wave in enumerate(waves):
        runnable = []
        for tid in wave:
            reason = _blocked_reason(tid)
            if reason:
                blocked[tid] = reason
                results[tid] = {"status": "blocked", "conclusion": "", "steps": 0,
                                "error": reason, "elapsed_ms": 0}
            else:
                runnable.append(tid)
        if not runnable:
            continue
        _emit("wave", {"wave": wi, "tasks": list(runnable)})

        def _run_one(tid):
            t = by_id[tid]
            ctx = {d: (results.get(d) or {}).get("conclusion", "") for d in t["depends_on"]}
            ctx = {k: v for k, v in ctx.items() if v}
            start = time.monotonic()
            try:
                out = runner(t, ctx) or {}
            except Exception as e:  # noqa: BLE001 —— 单个子任务失败不拖垮整图
                out = {"status": "failed", "conclusion": "",
                       "error": f"{type(e).__name__}: {e}"}
            out.setdefault("status", "ok")
            out.setdefault("conclusion", "")
            out.setdefault("steps", 0)
            out["elapsed_ms"] = int((time.monotonic() - start) * 1000)
            return tid, out

        with ThreadPoolExecutor(max_workers=min(max_parallel, max(1, len(runnable)))) as ex:
            futures = [ex.submit(_run_one, tid) for tid in runnable]
            for f in futures:
                tid, out = f.result()
                results[tid] = out
                _emit("task", {"id": tid, "status": out.get("status")})

    merged = ""
    if synth_runner and any(r.get("status") == "ok" for r in results.values()):
        try:
            merged = synth_runner(tasks, results) or ""
        except Exception as e:  # noqa: BLE001 —— 合成失败退回只给各任务结论
            merged = f"（结果合成失败：{type(e).__name__}: {e}）"

    order = [tid for w in waves for tid in w]
    return {
        "ok": all((r.get("status") == "ok") or by_id[tid].get("optional")
                  for tid, r in results.items()),
        "waves": waves,
        "order": order,
        "results": results,
        "blocked": sorted(blocked),
        "merged": merged,
        "n_tasks": len(tasks),
        "n_ok": sum(1 for r in results.values() if r.get("status") == "ok"),
        "n_failed": sum(1 for r in results.values() if r.get("status") == "failed"),
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
    }


def format_report(report, max_conclusion=400):
    """把 run_plan 的结果渲染成给模型阅读的文本（工具 Observation 用）。"""
    lines = [
        f"编排完成：{report['n_tasks']} 个任务 / {len(report['waves'])} 波，"
        f"成功 {report['n_ok']}、失败 {report['n_failed']}、阻断 {len(report['blocked'])}，"
        f"耗时 {report['elapsed_ms']}ms。"
    ]
    for tid in report["order"]:
        r = report["results"].get(tid, {})
        status = r.get("status")
        mark = {"ok": "ok", "failed": "FAIL", "blocked": "BLOCKED"}.get(status, status)
        if status == "ok" and r.get("degraded"):
            mark = "ok(部分)"   # 子代理未在步数内收尾，结论是过程要点兜底
        body = (r.get("conclusion") or "").strip()
        if status != "ok":
            body = (r.get("error") or body or "").strip()
        if len(body) > max_conclusion:
            body = body[:max_conclusion] + "…（截断）"
        lines.append(f"- [{mark}] {tid}（{r.get('elapsed_ms', 0)}ms）：{body or '（无内容）'}")
    if report.get("merged"):
        lines.append("\n【合成结论】\n" + report["merged"])
    return "\n".join(lines)
