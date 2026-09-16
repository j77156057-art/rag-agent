"""会话隔离 + 持久化 + 滚动摘要。

把 Agent 的对话记忆从"进程内单例的一个 list"提升为**按 session_id 落盘**的会话：
每个会话一个 JSON 文件，保存最近若干轮问答 + 一段"早期对话摘要"。

- 隔离：不同 session_id 互不可见，解决 api.py 复用同一 Agent 导致历史串台的问题。
- 持久化：重启服务后同一 session_id 仍能续聊。
- 摘要压缩：轮数/字符超阈值时，把最早的若干轮交给 LLM 压缩成一段摘要，
  只保留最近 KEEP 轮原文 —— 长会话不会把上下文预算吃光。

存储：<STATE_ROOT>/.docmind_sessions/<slug>.json（只存用户问题与模型回答，非敏感配置另存）。
写盘失败一律静默降级，不影响主流程。
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime

from config import STATE_ROOT, state_path

SESSIONS_DIR = state_path("DOCMIND_SESSIONS_DIR", os.path.join(STATE_ROOT, ".docmind_sessions"))
# 压缩以 token 占用为准（阈值由 Agent 按模型真实窗口换算后传入），轮数只留两道
# 硬保险：轮数上限防失控；保留轮数下限保证最近的上下文不被摘要掉。
MAX_TURNS = int(os.getenv("DOCMIND_SESSION_MAX_TURNS", "200"))       # 轮数硬上限（超过必压缩）
KEEP_TURNS_MIN = int(os.getenv("DOCMIND_SESSION_KEEP_TURNS_MIN", "2"))  # 压缩至少保留的原文轮数
# 直接调用 maybe_compact 且未传 token 阈值时的兜底（约等于旧 8000 字符的保守口径）
DEFAULT_TRIGGER_TOKENS = int(os.getenv("DOCMIND_COMPACT_TRIGGER_TOKENS", "7000"))
DEFAULT_KEEP_TOKENS = int(os.getenv("DOCMIND_COMPACT_KEEP_TOKENS", "3500"))

_lock = threading.Lock()


def _slug(session_id) -> str:
    """把任意 session_id 归一成安全文件名（防目录穿越 / 非法字符）。"""
    s = re.sub(r"[^0-9A-Za-z_.-]", "_", str(session_id or "default"))
    s = s.strip("._") or "default"
    return s[:64]


def _path(session_id) -> str:
    return os.path.join(SESSIONS_DIR, _slug(session_id) + ".json")


def _default() -> dict:
    return {"session_id": "", "updated_at": "", "summary": "", "turns": []}


def load(session_id) -> dict:
    """读取一个会话记录；缺失/损坏返回空结构（调用方无需处理异常）。"""
    data = _default()
    data["session_id"] = str(session_id or "default")
    p = _path(session_id)
    if not os.path.isfile(p):
        return data
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            turns = raw.get("turns")
            data["summary"] = raw.get("summary") or ""
            data["turns"] = turns if isinstance(turns, list) else []
            data["updated_at"] = raw.get("updated_at") or ""
    except (OSError, ValueError):
        pass
    return data


def _write(session_id, data) -> bool:
    try:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        tmp = _path(session_id) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, _path(session_id))
        return True
    except OSError:
        return False


def history(session_id):
    """返回给 Agent 回放的 turns 列表（list[{user, assistant}]）。"""
    return [{"user": t.get("user", ""), "assistant": t.get("assistant", "")}
            for t in load(session_id).get("turns", []) if isinstance(t, dict)]


def summary_text(session_id) -> str:
    return load(session_id).get("summary", "") or ""


def save(session_id, turns, summary=None) -> bool:
    """整体落盘一个会话（turns = [{user, assistant}]）。"""
    with _lock:
        data = load(session_id)
        data["session_id"] = str(session_id or "default")
        if summary is not None:
            data["summary"] = summary
        data["turns"] = [
            {"user": t.get("user", ""), "assistant": t.get("assistant", ""),
             "ts": t.get("ts") or datetime.now().isoformat(timespec="seconds")}
            for t in (turns or []) if isinstance(t, dict)
        ]
        return _write(session_id, data)


def _est_tokens(text: str) -> int:
    """无 LLM 客户端时的保守 token 估算：CJK 约 1.1 token/字，其余约 3.2 字符/token。

    与 llm.LLMClient._heuristic_tokens 同口径，但不含每条 +40 的固定底数
    （这里是整段历史一次计数，加底数会严重高估短历史）。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return int(cjk * 1.1 + other / 3.2) + 1


def _turns_tokens(turns, counter) -> int:
    text = "\n".join(
        (t.get("user", "") or "") + "\n" + (t.get("assistant", "") or "")
        for t in (turns or []) if isinstance(t, dict)
    )
    return int(counter(text) or 0)


_SUMMARY_PROMPT = (
    "你是对话压缩器。把下面这段【早期对话】压缩成不超过 300 字的中文要点，"
    "务必保留：已确认的结论、涉及的文件路径与行号、用户的偏好与约束、尚未解决的问题。"
    "不要复述寒暄，不要编造未出现的信息。只输出要点本身。\n\n早期对话：\n"
)


def _naive_summary(turns) -> str:
    """LLM 不可用时的确定性兜底：抽取每轮问题首句拼成要点。"""
    bits = []
    for t in turns:
        q = (t.get("user") or "").strip().replace("\n", " ")[:60]
        if q:
            bits.append("· " + q)
    return "（早期对话摘要·本地兜底）\n" + "\n".join(bits)


def _llm_summary(turns, llm) -> str:
    if llm is None:
        return _naive_summary(turns)
    convo = "\n".join(
        f"用户：{(t.get('user') or '')[:400]}\n助手：{(t.get('assistant') or '')[:400]}"
        for t in turns
    )
    try:
        out = llm.chat(
            [{"role": "user", "content": _SUMMARY_PROMPT + convo}],
            stream=False, temperature=0.2,
        )
        out = (out or "").strip()
        return out or _naive_summary(turns)
    except Exception:  # noqa: BLE001 —— 压缩失败绝不能打断正常问答
        return _naive_summary(turns)


def maybe_compact(session_id, turns, llm=None, trigger_tokens=None, keep_tokens=None):
    """历史 token 占用超过模型预算阈值时，把最早的若干轮压缩进 summary。

    - trigger_tokens：历史总 token 达到该值即压缩（由 Agent 按模型真实窗口 ×
      COMPACT_TRIGGER_RATIO 换算；1M 窗口模型与 16k 本地模型用各自的阈值）；
    - keep_tokens：压缩后最近原文轮次的 token 目标体积（× COMPACT_KEEP_RATIO），
      至少保留 KEEP_TURNS_MIN 轮；
    - 轮数超过 MAX_TURNS 硬上限也必压缩（与 token 阈值是「或」关系）。
    返回 (turns, summary)；未触发压缩时原样返回。
    """
    turns = list(turns or [])
    trigger = int(trigger_tokens or DEFAULT_TRIGGER_TOKENS)
    keep = int(keep_tokens or DEFAULT_KEEP_TOKENS)
    counter = getattr(llm, "count_tokens", None) or _est_tokens

    over_tokens = _turns_tokens(turns, counter) > trigger
    over_turns = len(turns) > MAX_TURNS
    if not over_tokens and not over_turns:
        return turns, summary_text(session_id)

    # 从最旧轮次开始弹出，直到最近轮次装进 keep_tokens（轮数硬上限同时收敛），
    # 但给最近 KEEP_TURNS_MIN 轮免死金牌——再超也由每轮 _fit_budget 裁剪兜底。
    kept = list(turns)
    while (
        len(kept) > KEEP_TURNS_MIN
        and (len(kept) > MAX_TURNS or _turns_tokens(kept, counter) > keep)
    ):
        kept.pop(0)
    if len(kept) >= len(turns):
        return turns, summary_text(session_id)

    old = turns[: len(turns) - len(kept)]
    prev = summary_text(session_id)
    new_sum = _llm_summary(old, llm)
    summary = (prev + "\n" + new_sum).strip() if prev else new_sum
    return kept, summary


def list_sessions(limit=50):
    """列出会话摘要（用于 /api/sessions 概览）。"""
    out = []
    if not os.path.isdir(SESSIONS_DIR):
        return out
    try:
        for name in os.listdir(SESSIONS_DIR):
            if not name.endswith(".json"):
                continue
            p = os.path.join(SESSIONS_DIR, name)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except (OSError, ValueError):
                continue
            out.append({
                "session_id": raw.get("session_id") or name[:-5],
                "turns": len(raw.get("turns") or []),
                "has_summary": bool(raw.get("summary")),
                "updated_at": raw.get("updated_at") or "",
            })
    except OSError:
        return out
    out.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    return out[:max(1, int(limit))]


def delete(session_id) -> bool:
    try:
        p = _path(session_id)
        if os.path.isfile(p):
            os.remove(p)
        return True
    except OSError:
        return False
