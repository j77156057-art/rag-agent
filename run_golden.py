"""黄金题库运行器：跑一遍题目，产出 results.jsonl 供 agent_eval.py 打分。

为什么需要它：agent_eval.py 只负责「打分与回归判定」，不负责跑题。
没有 runner，题库就只是躺在磁盘上的 JSON，跑不出任何数字。

用法：
  python run_golden.py                                   # 跑默认题库，输出到 golden/results.jsonl
  python run_golden.py --questions golden/questions_v2.json --out golden/results_v2.jsonl
  python run_golden.py --provider ollama --model qwen3.6:35b-a3b
  python run_golden.py --ids G34 --disable-tools search_code   # L3 工具失效降级档
  python run_golden.py --limit 5                         # 先跑 5 题试水

跑完打分：
  python agent_eval.py --results golden/results_v2.jsonl
  python agent_eval.py --results golden/results_v2.jsonl --baseline golden/results_baseline.jsonl

设计约定：
- 输出字段与既有 results_baseline.jsonl 保持一致（id/question/model/session_id/expect/
  elapsed_s/n_actions/actions/thoughts/observations/reflections/final/error），
  保证 agent_eval.py 无需改动即可消费。
- 默认关闭 trace 落盘（DOCMIND_TRACE=0），避免污染真实使用账本。
- 软超时：单题超过 --max-seconds 就中断并记录，不会让整轮挂死。
- 每题写完立即 flush，中途 Ctrl-C 也保留已完成部分。
"""
from __future__ import annotations

import os

# 必须在 import agent 之前设置：agent_trace 在模块导入时读取该变量决定是否落盘
os.environ.setdefault("DOCMIND_TRACE", "0")

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from agent import Agent  # noqa: E402
from llm import LLMClient  # noqa: E402

REFLECT_MARKS = ("未返回有效结果", "换思路", "重试", "没有找到")


def build_allowlist(disabled: list[str], only: list[str] | None = None):
    """把「禁用的工具」/「只启用的工具」翻译成 Agent 需要的「允许列表」。

    `--only-tools` 优先：47 个工具的 schema 会撑爆系统提示（实测触发
    "system prompt has been truncated" 并让模型输出格式崩溃），
    压降工具集是缓解该问题最直接的手段。
    """
    if only:
        return list(only)
    if not disabled:
        return None
    try:
        from tools import TOOLS
        return [name for name in TOOLS if name not in set(disabled)]
    except ImportError:
        print(f"警告：无法导入 TOOLS，--disable-tools 已忽略", file=sys.stderr)
        return None


def run_one(question: str, *, llm, session_id: str, allowlist, max_seconds: float, project_id=None):
    """跑单题，返回一个结果 dict（不抛异常，错误写进 error 字段）。"""
    thoughts: list[str] = []
    actions: list[str] = []
    observations: list[str] = []
    reflections: list[str] = []
    final = ""
    error = None
    timed_out = False

    agent = Agent(llm=llm, session_id=session_id, tool_allowlist=allowlist, project_id=project_id)
    t0 = time.monotonic()

    try:
        for ev in agent.run(question, stream=True):
            # 软超时：不让单题拖垮整轮
            if max_seconds > 0 and (time.monotonic() - t0) > max_seconds:
                timed_out = True
                break
            typ = ev.get("type")
            if typ == "thought":
                thoughts.append(ev.get("text", ""))
            elif typ == "action":
                text = ev.get("text") or f"{ev.get('tool', '')}({ev.get('arg', '')})"
                actions.append(text)
            elif typ == "observation":
                text = ev.get("text", "")
                observations.append(text)
                if any(mark in text for mark in REFLECT_MARKS):
                    reflections.append(text[:200])
            elif typ == "final":
                final = ev.get("text", "")
            elif typ == "error":
                error = ev.get("text") or ev.get("message")
    except Exception as e:  # noqa: BLE001 —— 单题崩溃不能拖垮整轮
        error = f"{type(e).__name__}: {e}"

    if timed_out and not error:
        error = f"timeout after {max_seconds}s"

    return {
        "thoughts": thoughts,
        "actions": actions,
        "observations": observations,
        "reflections": reflections,
        "final": final,
        "error": error,
        "elapsed_s": round(time.monotonic() - t0, 1),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="跑黄金题库，产出 results.jsonl")
    ap.add_argument("--questions", default="golden/questions_v2.json", help="题库 JSON")
    ap.add_argument("--out", default="golden/results.jsonl", help="结果输出（JSONL，追加）")
    ap.add_argument("--provider", default="", help="provider，如 ollama / qwen")
    ap.add_argument("--model", default="", help="模型名")
    ap.add_argument("--ids", default="", help="只跑指定题，逗号分隔，如 G01,G34")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 题（0=全部）")
    ap.add_argument("--disable-tools", default="", help="禁用的工具，逗号分隔（L3 降级档用）")
    ap.add_argument("--only-tools", default="",
                    help="只启用这些工具（逗号分隔），如 --only-tools "
                         "search_code,grep,read_file,list_dir,search_knowledge,calculate。"
                         "47 个工具的 schema 会撑爆系统提示，压降工具集可显著稳定输出")
    ap.add_argument("--project-id", default="",
                    help="项目 id（如 prj-xxxx）。代码问答按项目隔离，跑题前务必确认它指向被问的代码库；"
                         "留空则用「当前项目」，很容易指错")
    ap.add_argument("--max-seconds", type=float, default=300.0, help="单题软超时秒数")
    args = ap.parse_args(argv)

    with open(args.questions, "r", encoding="utf-8") as f:
        questions = json.load(f)

    if args.ids:
        wanted = {s.strip() for s in args.ids.split(",") if s.strip()}
        questions = [q for q in questions if q.get("id") in wanted]
    if args.limit > 0:
        questions = questions[: args.limit]
    if not questions:
        print("没有要跑的题目")
        return 1

    llm = LLMClient(provider=args.provider or None, model=args.model or None)
    model_name = getattr(llm, "model", "") or args.model or os.getenv("LLM_MODEL", "")
    allowlist = build_allowlist(
        [s.strip() for s in args.disable_tools.split(",") if s.strip()],
        [s.strip() for s in args.only_tools.split(",") if s.strip()] or None,
    )

    os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    print(f"题库 {args.questions} 共 {len(questions)} 题 · model={model_name or '(默认)'} "
          f"· 禁用工具={args.disable_tools or '无'} · project={args.project_id or '(当前项目)'}")
    # --- 绑定项目上下文 ---------------------------------------------------
    # search_code / grep / read_file 是按「当前项目」取代码集合的，
    # 只给 Agent(project_id=) 不够——必须真正把当前项目指针切过去，
    # 否则会检索到别的工程（实测：答出 godot_sample 的 level.gd 符号）。
    _restore = None
    if args.project_id:
        try:
            import projects as _projects
            from config import set_runtime as _set_runtime, get_runtime as _get_runtime
            if _projects.enabled():
                _restore = (_projects.current_project_id(), _get_runtime("code_root"))
                if _projects.set_current(args.project_id):
                    root = (_projects.get_project(args.project_id) or {}).get("root") or os.getcwd()
                    _set_runtime("code_root", root)
                    print(f"已切换当前项目 → {args.project_id}（code_root={root}）")
                else:
                    print(f"警告：项目 {args.project_id} 切换失败，将按当前项目检索")
        except Exception as e:  # noqa: BLE001 —— 绑定失败不应阻断跑题
            print(f"警告：绑定项目上下文失败：{type(e).__name__}: {e}")

    print(f"输出 → {args.out}\n")

    with open(args.out, "a", encoding="utf-8") as f:
        for i, q in enumerate(questions, 1):
            qid = q.get("id", f"Q{i}")
            session_id = f"golden-{stamp}-{qid}"
            res = run_one(
                q.get("question", ""),
                llm=llm,
                session_id=session_id,
                allowlist=allowlist,
                max_seconds=args.max_seconds,
                project_id=args.project_id or None,
            )
            rec = {
                "id": qid,
                "question": q.get("question", ""),
                "expect": q.get("expect", {}),
                "model": model_name,
                "session_id": session_id,
                "elapsed_s": res["elapsed_s"],
                "n_actions": len(res["actions"]),
                "actions": res["actions"],
                "thoughts": res["thoughts"],
                "observations": res["observations"],
                "reflections": res["reflections"],
                "final": res["final"],
                "error": res["error"],
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()

            flag = "err " if res["error"] else "ok  "
            print(f"  {flag}{qid}  {res['elapsed_s']:>6.1f}s  "
                  f"actions={len(res['actions'])}  final_chars={len(res['final'])}")

    if _restore:
        try:
            _projects.set_current(_restore[0])
            _set_runtime("code_root", _restore[1])
            print(f"已恢复当前项目 → {_restore[0]}")
        except Exception:
            pass

    print(f"\n完成，结果在 {args.out}")
    print(f"打分：python agent_eval.py --results {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
