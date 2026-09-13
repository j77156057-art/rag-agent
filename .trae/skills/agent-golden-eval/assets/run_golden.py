# -*- coding: utf-8 -*-
"""黄金题评测 runner（SSE 全量落盘，每题独立会话）。

环境变量：
  GOLDEN_QUESTIONS  必填，题库 JSON 路径：[{"id": "Q1", "question": "..."}, ...]
  GOLDEN_OUT        输出 jsonl 路径（默认题库同目录 results.jsonl；勿覆盖历史轮次）
  GOLDEN_BASE       服务地址（默认 http://127.0.0.1:8000）
  GOLDEN_MODEL      ollama 模型（默认 qwen3.6:35b-a3b）
  GOLDEN_QIDS       可选，逗号分隔只跑部分题，如 "Q2,Q5"
"""
import json
import os
import time
import urllib.request

BASE = os.environ.get("GOLDEN_BASE", "http://127.0.0.1:8000")
MODEL = os.environ.get("GOLDEN_MODEL", "qwen3.6:35b-a3b")
QP = os.environ.get("GOLDEN_QUESTIONS")
OUT = os.environ.get(
    "GOLDEN_OUT",
    os.path.join(os.path.dirname(os.path.abspath(QP or ".")), "results.jsonl"),
)
_QIDS = {q.strip() for q in os.environ.get("GOLDEN_QIDS", "").split(",") if q.strip()}


def post_multipart(path, fields):
    boundary = "----goldenboundary"
    body = b""
    for k, v in fields.items():
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode()
        body += f"{v}\r\n".encode("utf-8")
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        BASE + path, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    return urllib.request.urlopen(req, timeout=600)


def clear_history():
    # /api/config 是 JSON body（multipart 会 422）；重设同 provider/model 清空
    # agent.history 但不重建向量集合，从而保证每题独立会话。
    data = json.dumps({"provider": "ollama", "model": MODEL}).encode("utf-8")
    req = urllib.request.Request(
        BASE + "/api/config", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        json.loads(r.read().decode("utf-8"))


def ask(qid, question):
    t0 = time.time()
    thoughts, actions, observations, reflections = [], [], [], []
    final_parts = []
    with post_multipart("/api/chat", {"question": question}) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload:
                continue
            ev = json.loads(payload)
            typ = ev.get("type")
            if typ == "done":
                break
            if typ == "thought":
                thoughts.append(ev.get("text", ""))
            elif typ == "action":
                actions.append(ev.get("text", ""))
            elif typ == "observation":
                observations.append(ev.get("text", "")[:200])
            elif typ == "reflection":
                reflections.append(ev.get("text", ""))
            elif typ == "final":
                final_parts.append(ev.get("text", ""))
    return {
        "id": qid,
        "question": question,
        "model": MODEL,
        "elapsed_s": round(time.time() - t0, 1),
        "n_actions": len(actions),
        "actions": actions,
        "thoughts": thoughts,
        "observations": observations,
        "reflections": reflections,
        "final": "".join(final_parts),
    }


def main():
    if not QP or not os.path.isfile(QP):
        raise SystemExit(f"GOLDEN_QUESTIONS 无效或未设置: {QP!r}")
    with open(QP, "r", encoding="utf-8") as f:
        questions = json.load(f)
    with open(OUT, "w", encoding="utf-8") as f:
        for item in questions:
            qid, q = item["id"], item["question"]
            if _QIDS and qid not in _QIDS:
                continue
            print(f"[{time.strftime('%H:%M:%S')}] {qid} clearing history...", flush=True)
            try:
                clear_history()
            except Exception as e:  # noqa: BLE001
                print(f"  clear failed: {e}", flush=True)
            print(f"[{time.strftime('%H:%M:%S')}] {qid} asking: {q}", flush=True)
            try:
                rec = ask(qid, q)
            except Exception as e:  # noqa: BLE001
                rec = {"id": qid, "question": q, "model": MODEL,
                       "error": f"{type(e).__name__}: {e}"}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  done in {rec.get('elapsed_s')}s, actions={rec.get('n_actions')}, "
                  f"final_len={len(rec.get('final', ''))}", flush=True)
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
