# -*- coding: utf-8 -*-
"""冻结发布流水线里的「黄金题回归门」一键执行器。

串起：健康检查 → run_golden.py（真实 SSE）→ agent_eval.py --baseline（回归判定），
并输出一行可直接贴进 DocMind_BUILD.md 的摘要。**缺本地模型/题库时优雅跳过（退出码 0）**，
这样没有 Ollama 的机器也能跑完整套冻结构建流程。

环境变量：
  GOLDEN_QUESTIONS  题库 JSON（未设置则跳过）
  GOLDEN_BASELINE   baseline results.jsonl（未设置则只打分、不做回归判定）
  GOLDEN_BASE       服务地址（默认 http://127.0.0.1:8000）
  GOLDEN_MODEL      ollama 模型（默认 qwen3.6:35b-a3b）
  GOLDEN_OUT        本轮结果落盘路径（默认 <题库目录>/results_gate_<时间戳>.jsonl）
  GOLDEN_TOKEN      DOCMIND_API_TOKEN 启用时的令牌

退出码：0 = 通过或跳过；1 = 检出回归；2 = 执行失败（异常/无结果）。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

ASSETS = os.path.dirname(os.path.abspath(__file__))


def _repo_root():
    """自 assets 目录向上找到含 agent_eval.py 的仓库根。"""
    d = ASSETS
    for _ in range(6):
        d = os.path.dirname(d)
        if os.path.isfile(os.path.join(d, "agent_eval.py")):
            return d
    return os.getcwd()


ROOT = _repo_root()
BASE = os.environ.get("GOLDEN_BASE", "http://127.0.0.1:8000").rstrip("/")
QUESTIONS = os.environ.get("GOLDEN_QUESTIONS", "")
BASELINE = os.environ.get("GOLDEN_BASELINE", "")
TOKEN = os.environ.get("GOLDEN_TOKEN", "").strip()


def say(msg):
    print(msg, flush=True)


def skip(reason):
    say(f"[golden-gate] SKIPPED: {reason}")
    say(f"[golden-gate] 摘要（贴 BUILD.md）：黄金题回归门 = 跳过（{reason}）")
    sys.exit(0)


def healthy():
    req = urllib.request.Request(BASE + "/api/health")
    if TOKEN:
        req.add_header("x-docmind-token", TOKEN)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=8) as r:
            return r.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        return False


def main():
    if not QUESTIONS:
        skip("未设置 GOLDEN_QUESTIONS")
    if not os.path.isfile(QUESTIONS):
        skip(f"题库不存在：{QUESTIONS}")
    if not healthy():
        skip(f"服务不可达：{BASE}（先起 dev 服务再跑门）")

    out = os.environ.get("GOLDEN_OUT") or os.path.join(
        os.path.dirname(os.path.abspath(QUESTIONS)),
        f"results_gate_{time.strftime('%Y%m%d-%H%M%S')}.jsonl",
    )
    env = dict(os.environ, GOLDEN_BASE=BASE, GOLDEN_OUT=out)
    say(f"[golden-gate] 跑黄金题 → {out}")
    rc = subprocess.run([sys.executable, "-B", os.path.join(ASSETS, "run_golden.py")],
                        env=env, cwd=ROOT).returncode
    if rc != 0 or not os.path.isfile(out):
        say(f"[golden-gate] FAILED: run_golden 退出码 {rc}")
        sys.exit(2)

    # 全是 error 行的结果文件视为执行失败（不是评测得 0 分）
    rows = []
    with open(out, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if rows and all(r.get("error") for r in rows):
        say("[golden-gate] FAILED: 所有题都报错（模型/服务异常）")
        sys.exit(2)

    cmd = [sys.executable, "-B", os.path.join(ROOT, "agent_eval.py"),
           "--results", out, "--json"]
    if BASELINE:
        if not os.path.isfile(BASELINE):
            skip(f"baseline 不存在：{BASELINE}")
        cmd += ["--baseline", BASELINE]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        say(proc.stdout[-2000:])
        say(f"[golden-gate] FAILED: 无法解析 agent_eval 输出（退出码 {proc.returncode}）")
        sys.exit(2)

    rep = payload.get("report") or {}
    gate = payload.get("gate")
    line = (f"通过 {rep.get('passed')}/{rep.get('scored')}"
            f"（通过率 {rep.get('pass_rate')}）")
    if gate:
        line += f"，baseline {gate['baseline_pass_rate']} → 本 {gate['current_pass_rate']}"
        if gate["regressed"]:
            line += f"，回归题：{','.join(gate['regressions']) or '通过率下滑'}"
    say(f"[golden-gate] {line}")
    say(f"[golden-gate] 结果：{out}")
    say(f"[golden-gate] 摘要（贴 BUILD.md）：黄金题回归门 = "
        f"{'REGRESSED' if (gate and gate['regressed']) else 'PASS'}（{line}）")

    if gate and gate["regressed"]:
        say("[golden-gate] 检出回归 —— 冻结发布应中止。")
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        say(f"[golden-gate] FAILED: {type(e).__name__}: {e}")
        sys.exit(2)
