# -*- coding: utf-8 -*-
"""黄金题评测 runner（SSE 全量落盘，每题独立会话）。

每题使用独立 `session_id`（golden-<run>-<qid>）走服务端会话隔离，
因此**不再需要**重设 /api/config 来清空历史（旧的 clear_history hack 已移除）。

环境变量：
  GOLDEN_QUESTIONS  必填，题库 JSON：[{"id":"Q1","question":"...","expect":{...}}, ...]
  GOLDEN_OUT        输出 jsonl 路径（默认题库同目录 results.jsonl；勿覆盖历史轮次）
  GOLDEN_BASE       服务地址（默认 http://127.0.0.1:8000）
  GOLDEN_MODEL      ollama 模型（默认 qwen3.6:35b-a3b）
  GOLDEN_QIDS       可选，逗号分隔只跑部分题，如 "Q2,Q5"
  GOLDEN_RUN_TAG    可选，本轮命名空间（默认时间戳），用于隔离不同轮次的会话
  GOLDEN_TOKEN      可选，DOCMIND_API_TOKEN 启用时填令牌
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
TOKEN = os.environ.get("GOLDEN_TOKEN", "").strip()
RUN_TAG = os.environ.get("GOLDEN_RUN_TAG") or time.strftime("%Y%m%d-%H%M%S")
_QIDS = {q.strip() for q in os.environ.get("GOLDEN_QIDS", "").split(",") if q.strip()}


def _headers(content_type):
    h = {"Content-Type": content_type}
    if TOKEN:
        h["x-docmind-token"] = TOKEN
    return h


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
        headers=_headers(f"multipart/form-data; boundary={boundary}"),
    )
    return urllib.request.urlopen(req, timeout=600)


def ask(qid, question, session_id, expect):
    t0 = time.time()
    thoughts, actions, observations, reflections = [], [], [], []
    final_parts = []
    with post_multipart("/api/chat", {"question": question, "session_id": session_id}) as r:
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
        "session_id": session_id,
        "expect": expect or {},
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
    print(f"run tag: {RUN_TAG}  →  {OUT}", flush=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for item in questions:
            qid, q = item["id"], item["question"]
            if _QIDS and qid not in _QIDS:
                continue
            session_id = f"golden-{RUN_TAG}-{qid}"
            print(f"[{time.strftime('%H:%M:%S')}] {qid} session={session_id} asking: {q}", flush=True)
            try:
                rec = ask(qid, q, session_id, item.get("expect") or item.get("scoring") or {})
            except Exception as e:  # noqa: BLE001
                rec = {"id": qid, "question": q, "model": MODEL, "session_id": session_id,
                       "expect": item.get("expect") or {}, "error": f"{type(e).__name__}: {e}"}
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            print(f"  done in {rec.get('elapsed_s')}s, actions={rec.get('n_actions')}, "
                  f"final_len={len(rec.get('final', ''))}", flush=True)
    print(f"written: {OUT}")


if __name__ == "__main__":
    main()
