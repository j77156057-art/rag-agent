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


def parse_plan(raw, known_ids=None):
    """校验并规范化任务列表。返回 [{id, role, task, depends_on, optional}]。

    `known_ids` 用于**动态重规划**：补救任务的 `depends_on` 允许指向本轮之前
    已存在的任务（那些 id 不在新任务列表里）。
    """
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

    ids = {t["id"] for t in out} | {str(k) for k in (known_ids or ())}
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


def _deps_state(tid, by_id, results):
    """返回 (是否可判定, 阻断原因)。

    可判定 = 所有依赖都已执行完；此时若某个非可选依赖不是 ok，就给出阻断原因。
    """
    for d in by_id[tid]["depends_on"]:
        r = results.get(d)
        if r is None:
            return False, None                     # 依赖还没跑完
        if r.get("status") != "ok" and not by_id[d].get("optional"):
            return True, f"上游 {d} {r.get('status')}"
    return True, None


def _accept_new_tasks(raw, by_id, tasks, max_tasks):
    """校验并接收 replanner 产出的补救任务（幂等过滤：坏项跳过，不抛异常）。"""
    if not raw:
        return []
    items = raw.get("tasks") if isinstance(raw, dict) else raw
    if not isinstance(items, list) or not items:
        return []
    try:
        norm = parse_plan(items, known_ids=set(by_id))
    except PlanError:
        return []
    room = max(0, max_tasks - len(tasks))
    added = []
    for t in norm:
        if t["id"] in by_id:
            continue                                # id 冲突：丢弃
        if any(d not in by_id for d in t["depends_on"]):
            continue                                # 依赖指向同批里被丢弃的项：丢弃
        by_id[t["id"]] = t
        tasks.append(t)
        added.append(t)
        if len(added) >= room:
            break
    return added


def run_plan(tasks, runner, synth_runner=None, max_parallel=4, on_event=None,
             replanner=None, max_replans=0, max_tasks=None):
    """迭代调度任务图（支持**动态重规划**）。

    runner(task, context) -> {"status": "ok"|"failed", "conclusion": str,
                              "steps": int, "error": str}
      context = {上游任务id: 上游结论}（仅含非空结论）
    synth_runner(tasks, results) -> str（可选，结果合成/冲突标注）
    replanner(failed_tasks, results, attempt) -> list[task] | None（可选）
      某轮有任务失败时调用，返回**补救任务**（可依赖已完成的任务）。
      返回空/非法/抛异常都视为"不再重规划"。最多调用 max_replans 次。

    调度语义：每轮挑出「依赖已全部落定且未被阻断」的任务并行执行；
    上游失败且不再重规划时，其下游标记 blocked 而不空跑（optional 上游除外）。
    单个任务异常只影响它自己。
    """
    t0 = time.monotonic()
    # 接受"原始任务"或"已 parse_plan 过的任务"两种入参（幂等规范化，避免调用方踩坑）
    tasks = parse_plan(list(tasks))
    by_id = {t["id"]: t for t in tasks}
    topological_waves(tasks)          # 静态校验：先炸出循环依赖
    limit_tasks = int(max_tasks or MAX_TASKS)
    results = {}
    blocked = {}
    rounds = []                       # 每轮实际执行的 id 列表（≈ 拓扑波）
    replans = 0

    def _emit(kind, payload):
        if on_event:
            try:
                on_event(kind, payload)
            except Exception:  # noqa: BLE001 —— 埋点/通知失败不影响调度
                pass

    while True:
        runnable = []
        for t in tasks:
            tid = t["id"]
            if tid in results:
                continue
            settled, reason = _deps_state(tid, by_id, results)
            if not settled:
                continue
            if reason:
                blocked[tid] = reason
                results[tid] = {"status": "blocked", "conclusion": "", "steps": 0,
                                "error": reason, "elapsed_ms": 0}
            else:
                runnable.append(t)
        if not runnable:
            break

        _emit("wave", {"wave": len(rounds), "tasks": [t["id"] for t in runnable]})

        def _run_one(t):
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
            return t["id"], out

        with ThreadPoolExecutor(max_workers=min(max_parallel, max(1, len(runnable)))) as ex:
            futures = [ex.submit(_run_one, t) for t in runnable]
            for f in futures:
                tid, out = f.result()
                results[tid] = out
                _emit("task", {"id": tid, "status": out.get("status")})
        rounds.append([t["id"] for t in runnable])

        failed = [t for t in runnable if (results.get(t["id"]) or {}).get("status") == "failed"]
        if failed and replanner is not None and replans < int(max_replans) and len(tasks) < limit_tasks:
            try:
                proposal = replanner(failed, dict(results), replans + 1)
            except Exception:  # noqa: BLE001 —— 重规划失败按"不再重规划"处理
                proposal = None
            added = _accept_new_tasks(proposal, by_id, tasks, limit_tasks)
            if added:
                replans += 1
                _emit("replan", {"attempt": replans, "added": [t["id"] for t in added]})
                continue
        # 不再重规划：失败者的下游由下一轮 _deps_state 标记为 blocked

    merged = ""
    if synth_runner and any(r.get("status") == "ok" for r in results.values()):
        try:
            merged = synth_runner(tasks, results) or ""
        except Exception as e:  # noqa: BLE001 —— 合成失败退回只给各任务结论
            merged = f"（结果合成失败：{type(e).__name__}: {e}）"

    return {
        "ok": all((r.get("status") == "ok") or by_id[tid].get("optional")
                  for tid, r in results.items()),
        "waves": rounds,
        "order": list(results.keys()),
        "results": results,
        "blocked": sorted(blocked),
        "merged": merged,
        "replans": replans,
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
        + (f"重规划 {report.get('replans', 0)} 次，" if report.get("replans") else "")
        + f"耗时 {report['elapsed_ms']}ms。"
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
