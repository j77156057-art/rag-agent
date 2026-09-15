"""逐轮 trace + token 账本。

每个 Agent 回合（一次 /api/chat）落一条 JSONL 记录，字段：
  turn_id / session_id / provider / model / route / messages 哈希 / messages 条数 /
  工具调用序列 / tokens in-out / LLM 调用次数与耗时 / 各工具步延迟 /
  finish_reason / 是否被客户端断连中止 / 总耗时。

设计原则（重要）：
- **只记元数据，不记正文**：prompt 与回答只记字符数，正文永不落盘（避免把用户代码/密钥写进日志）。
- append-only JSONL，超过上限自动轮转为 `<file>.1`，不会无限增长。
- 默认开启；设 `DOCMIND_TRACE=0` 可整体关闭（写盘失败一律静默降级，不影响主流程）。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import datetime

from config import BASE_DIR

TRACE_FILE = os.getenv("DOCMIND_TRACE_FILE") or os.path.join(BASE_DIR, ".docmind_traces.jsonl")
TRACE_ENABLED = os.getenv("DOCMIND_TRACE", "1") != "0"
MAX_BYTES = int(os.getenv("DOCMIND_TRACE_MAX_BYTES", str(8 * 1024 * 1024)))

_lock = threading.Lock()

_USAGE_IN_KEYS = ("prompt_tokens", "in", "prompt_eval_count", "input_tokens")
_USAGE_OUT_KEYS = ("completion_tokens", "out", "eval_count", "output_tokens")


def _pick(d, keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)):
            return int(v)
    return 0


def messages_hash(messages) -> str:
    """对整段消息做稳定哈希（只用于判断"同样输入"与去重，不可逆）。"""
    h = hashlib.sha256()
    for m in messages or []:
        h.update(str(m.get("role", "")).encode("utf-8", "ignore"))
        h.update(b"\x00")
        content = m.get("content")
        if isinstance(content, str):
            h.update(content.encode("utf-8", "ignore"))
        elif content is not None:
            h.update(json.dumps(content, ensure_ascii=False, default=str).encode("utf-8", "ignore"))
        h.update(b"\x01")
    return h.hexdigest()[:16]


class Turn:
    """一个 Agent 回合的埋点累加器。由 agent.Agent.run 创建、on-finish 落盘。"""

    def __init__(self, session_id="default", provider="", model="", route=None, question=""):
        self.turn_id = uuid.uuid4().hex[:12]
        self.session_id = session_id or "default"
        self.provider = provider
        self.model = model
        self.route = route
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.question_chars = len(question or "")
        self.messages_hash = None
        self.messages_count = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.llm_calls = 0
        self.llm_ms = 0
        self.steps = []
        self.actions = []
        self.reflections = 0
        self.outcome = None      # 回合结局：completed/max_steps/truncated/evidence_fallback/...
        self.finish_reason = None
        self.final_chars = 0
        self.error = None
        self.aborted = False
        self._t0 = time.monotonic()
        self._done = False

    # ---- 采集 ----
    def snapshot_prompt(self, messages):
        self.messages_hash = messages_hash(messages)
        self.messages_count = len(messages or [])
        return self.messages_hash

    def add_usage(self, usage):
        """累加一次 LLM 调用的 token 用量（键名兼容 OpenAI 与 Ollama）。"""
        if not usage:
            return
        self.prompt_tokens += _pick(usage, _USAGE_IN_KEYS)
        self.completion_tokens += _pick(usage, _USAGE_OUT_KEYS)

    def llm_step(self, latency_ms, finish_reason=None):
        self.llm_calls += 1
        self.llm_ms += int(latency_ms or 0)
        if finish_reason:
            self.finish_reason = finish_reason

    def tool_step(self, action, arg="", latency_ms=0, obs="", ok=True):
        self.actions.append(action)
        self.steps.append({
            "i": len(self.steps),
            "action": action,
            "arg_chars": len(arg or ""),
            "latency_ms": int(latency_ms or 0),
            "obs_chars": len(obs or ""),
            "ok": bool(ok),
        })

    def note_reflection(self):
        self.reflections += 1

    # ---- 收尾 ----
    def finish(self, reason=None, final_text="", error=None, aborted=False):
        if self._done:
            return
        self._done = True
        self.finish_reason = reason or self.finish_reason or "completed"
        self.final_chars = len(final_text or "")
        self.error = error
        self.aborted = bool(aborted)

    def to_record(self):
        return {
            "turn_id": self.turn_id,
            "ts": self.started_at,
            "session_id": self.session_id,
            "provider": self.provider,
            "model": self.model,
            "route": self.route,
            "question_chars": self.question_chars,
            "messages_hash": self.messages_hash,
            "messages_count": self.messages_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "llm_calls": self.llm_calls,
            "llm_ms": self.llm_ms,
            "steps": self.steps,
            "actions": self.actions,
            "n_steps": len(self.steps),
            "reflections": self.reflections,
            "outcome": self.outcome or self.finish_reason or "completed",
            "finish_reason": self.finish_reason,
            "final_chars": self.final_chars,
            "aborted": self.aborted,
            "error": self.error,
            "elapsed_ms": int((time.monotonic() - self._t0) * 1000),
        }


# ---------------------------------------------------------------------------
# 落盘 / 读取
# ---------------------------------------------------------------------------
def _rotate_if_needed():
    try:
        if os.path.isfile(TRACE_FILE) and os.path.getsize(TRACE_FILE) > MAX_BYTES:
            bak = TRACE_FILE + ".1"
            if os.path.isfile(bak):
                os.remove(bak)
            os.replace(TRACE_FILE, bak)
    except OSError:
        pass


def record(rec) -> bool:
    """追加一条 trace。任何异常都静默降级（埋点绝不能影响主流程）。"""
    if not TRACE_ENABLED:
        return False
    try:
        with _lock:
            _rotate_if_needed()
            with open(TRACE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError):
        return False


def recent(limit=50):
    """返回最近 limit 条 trace（按时间正序）。损坏行跳过，不抛异常。"""
    if not os.path.isfile(TRACE_FILE):
        return []
    try:
        with _lock:
            with open(TRACE_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-max(1, int(limit)):]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def summary():
    """全账本聚合：回合数、token 总量、平均耗时、失败/中止计数、按 provider 分解。"""
    rows = recent(limit=100000)
    agg = {
        "turns": len(rows),
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "avg_elapsed_ms": 0,
        "aborted": 0,
        "errors": 0,
        "by_provider": {},
    }
    if not rows:
        return agg
    elapsed = 0
    for r in rows:
        p = int(r.get("prompt_tokens") or 0)
        c = int(r.get("completion_tokens") or 0)
        agg["prompt_tokens"] += p
        agg["completion_tokens"] += c
        agg["total_tokens"] += p + c
        elapsed += int(r.get("elapsed_ms") or 0)
        if r.get("aborted"):
            agg["aborted"] += 1
        if r.get("error"):
            agg["errors"] += 1
        key = r.get("provider") or "?"
        slot = agg["by_provider"].setdefault(key, {"turns": 0, "tokens": 0})
        slot["turns"] += 1
        slot["tokens"] += p + c
    agg["avg_elapsed_ms"] = int(elapsed / len(rows))
    return agg


def clear() -> bool:
    try:
        with _lock:
            if os.path.isfile(TRACE_FILE):
                os.remove(TRACE_FILE)
            bak = TRACE_FILE + ".1"
            if os.path.isfile(bak):
                os.remove(bak)
        return True
    except OSError:
        return False
