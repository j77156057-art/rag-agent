"""跨会话经验记忆：把 Agent 的「操作→决策→结果」沉淀成可检索的 episodic 记忆。

设计（方案 docs/agent-self-verify-memory.md §4）：
- 存储复用 vectorstore 单集合抽象，独立集合 docmind_experience（与文档/代码集合并列，不污染）。
- 脱敏：只存元数据 + 教训文本，绝不存代码片段 / 凭据 / 正文（与 trace 隐私原则一致）。
- 召回：按 project_id 隔离，作为**建议性** RAG 上下文（advisory，永不压过真实证据）。
- 有界：容量上限 + 去重 + valid_until 置信衰减；低置信一律不参与决策。

所有外部依赖（vectorstore / embeddings / llm）的故障都静默降级——经验记忆绝不能拖垮主流程。
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timedelta

# ---- 容量与时效 ----
EXPERIENCE_MAX = int(os.getenv("DOCMIND_EXPERIENCE_MAX", "200"))

# 失效 TTL（天）：失败经验更易过时、给更短时效；成功经验给更长（控制噪声 + 防漂移）。
_TTL_DAYS = {"fail-then-fixed": 30, "repeated-fail": 14, "success": 90}
_DEFAULT_TTL_DAYS = 30

# 合法的结局值（与方案 §4.1 一致）
_OUTCOMES = ("success", "fail-then-fixed", "repeated-fail")

# 脱敏：绝不落盘的内容模式（凭据类）。自由文本入库前先过一遍。
_SECRET_RE = re.compile(
    r"(?i)(api[_-]?key|token|secret|password|passwd|sk-[a-z0-9]{10,}|AKIA[0-9A-Z]{16})"
)


def _scrub(text, limit=800):
    """脱敏 + 截断：过滤明显凭据，截断超长文本。只用于 lesson / action_summary 等自由文本。"""
    if not text:
        return ""
    text = str(text).strip()
    text = _SECRET_RE.sub("[REDACTED]", text)
    return text[:limit]


# ---------------------------------------------------------------------------
# 外部依赖惰性获取（全部 try/except 降级，且可被测试 monkeypatch）
# ---------------------------------------------------------------------------
_embed_client_cache = None


def _embed_client():
    """返回 EmbeddingClient 单例；任何故障返回 None（上层降级为无向量）。

    注意：这里绝不探测维度 / 不触发网络（local 哈希兜底永远可用），
    因此即使离线也能安全创建——向量维度在首次 upsert 时由 Chroma 按集合固定。
    """
    global _embed_client_cache
    if _embed_client_cache is None:
        try:
            from embeddings import EmbeddingClient
            _embed_client_cache = EmbeddingClient()
        except Exception:  # noqa: BLE001
            _embed_client_cache = None
    return _embed_client_cache


def _experience_collection():
    """返回经验集合（可被测试 monkeypatch，绕过真实 Chroma）。"""
    from vectorstore import get_collection
    from config import EXPERIENCE_COLLECTION_NAME
    return get_collection(EXPERIENCE_COLLECTION_NAME)


# ---------------------------------------------------------------------------
# 核心 API
# ---------------------------------------------------------------------------
def record_episode(project_id, action_summary, decision, outcome,
                   lesson=None, *, ts=None, embed_client=None, llm=None,
                   extract_lesson=False):
    """记录一条经验。任何异常静默降级返回 False。

    project_id:      项目隔离键（Agent 的 self.project_id）。
    action_summary:  做了什么（自然语言，脱敏后落盘）。
    decision:        关键选择 / 失败点（脱敏后落盘）。
    outcome:         success / fail-then-fixed / repeated-fail。
    lesson:          显式教训文本；为空且 extract_lesson=True 且提供 llm 时尝试 LLM 提炼。
    embed_client:    可选；缺省用惰性 _embed_client()（测试可注入假客户端）。
    llm:             可选；仅 extract_lesson=True 时使用。
    extract_lesson:  是否用 LLM 提炼 lesson（受成本熔断约束）。
    返回:            True=已写入, False=未写入（非法结局/空摘要/降级）。
    """
    try:
        if outcome not in _OUTCOMES:
            return False
        project_id = str(project_id or "default")
        action_summary = _scrub(action_summary)
        decision = _scrub(decision)
        lesson = _scrub(lesson) if lesson else ""
        if not action_summary:
            return False
        if not lesson and extract_lesson and llm is not None:
            lesson = _extract_lesson(llm, action_summary, decision, outcome) or ""

        now = datetime.now()
        ts = ts or now.isoformat(timespec="seconds")
        ttl = _TTL_DAYS.get(outcome, _DEFAULT_TTL_DAYS)
        valid_until = (now + timedelta(days=ttl)).isoformat(timespec="seconds")

        # 去重 id：同项目 + 同结局 + 同动作摘要 → 更新而非新增（防堆叠噪声）。
        ep_id = "exp-" + hashlib.sha1(
            (project_id + "|" + outcome + "|" + action_summary).encode("utf-8")
        ).hexdigest()[:16]

        text = _embed_text(action_summary, decision, lesson)
        try:
            emb = embed_client or _embed_client()
            vec = emb.embed([text])[0] if emb is not None else None
        except Exception:  # noqa: BLE001
            vec = None

        col = _experience_collection()
        meta = {
            "action_summary": action_summary,
            "decision": decision,
            "outcome": outcome,
            "lesson": lesson,
            "project_id": project_id,
            "ts": ts,
            "valid_until": valid_until,
        }
        if vec is not None:
            col.upsert(ids=[ep_id], documents=[text],
                       embeddings=[vec], metadatas=[meta])
        else:
            # 无嵌入能力时仍记录（仅靠元数据可检索/展示），但不进向量索引。
            col.upsert(ids=[ep_id], documents=[text], metadatas=[meta])
        _enforce_capacity(col)
        return True
    except Exception:  # noqa: BLE001 —— 经验记忆绝不影响主流程
        return False


def recall_similar(project_id, query, k=5, *, embed_client=None):
    """按 project_id 隔离召回 top-k 相似经验。任何异常静默降级返回 []。

    返回: [{id, metadata, distance, stale, confidence}]（已排序：新鲜在前、陈旧在后）。
    """
    try:
        col = _experience_collection()
        emb = embed_client or _embed_client()
        if emb is None:
            return []
        vec = emb.embed([query])[0]
        res = col.query(query_embeddings=[vec], n_results=min(int(k), 50),
                        where={"project_id": str(project_id or "default")})
        return _format_recall(res)
    except Exception:  # noqa: BLE001
        return []


def count_experiences(project_id=None):
    """当前经验条数（按项目隔离；project_id=None 时不筛选，返回全集大小）。"""
    try:
        col = _experience_collection()
        if project_id is None:
            return col.count()
        res = col.get(where={"project_id": str(project_id)}, include=[])
        return len(res.get("ids") or [])
    except Exception:  # noqa: BLE001
        return 0


def is_stale(meta):
    """单条经验的 valid_until 是否已过期（陈旧 → 置信打折）。"""
    vu = (meta or {}).get("valid_until")
    if not vu:
        return False
    try:
        return datetime.now() > datetime.fromisoformat(vu)
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------
def _embed_text(action_summary, decision, lesson):
    return " | ".join(x for x in (action_summary, decision, lesson) if x)


def _format_recall(res):
    """把 Chroma query 结果整理成带 staleness / confidence 的列表。"""
    out = []
    if not res:
        return out
    ids = (res.get("ids") or [[]])[0]
    dists = (res.get("distances") or [[]])[0]
    metas = (res.get("metadatas") or [[]])[0]
    for i, meta in enumerate(metas or []):
        dist = float(dists[i]) if i < len(dists) else 1.0
        vu = (meta or {}).get("valid_until")
        stale = False
        if vu:
            try:
                stale = datetime.now() > datetime.fromisoformat(vu)
            except Exception:
                stale = False
        confidence = max(0.0, 1.0 - dist)
        if stale:
            confidence *= 0.3
        out.append({
            "id": ids[i] if i < len(ids) else None,
            "metadata": meta or {},
            "distance": dist,
            "stale": stale,
            "confidence": round(confidence, 3),
        })
    # 新鲜在前、陈旧在后；同档按距离升序。
    out.sort(key=lambda x: (x["stale"], x["distance"]))
    return out


def _enforce_capacity(col):
    """容量上限：超限删最旧（valid_until 最早）的若干条，保持集合有界。"""
    try:
        n = col.count()
        if n <= EXPERIENCE_MAX:
            return
        res = col.get(include=["metadatas"], limit=n)
        rows = []
        for _id, meta in zip(res.get("ids") or [], res.get("metadatas") or []):
            vu = (meta or {}).get("valid_until") or ""
            rows.append((vu, _id))
        rows.sort(key=lambda x: x[0])
        to_del = [r[1] for r in rows[: n - EXPERIENCE_MAX]]
        if to_del:
            col.delete(ids=to_del)
    except Exception:  # noqa: BLE001
        pass


_LESSON_PROMPT = (
    "你是一个软件工程经验提炼器。下面是一段 Agent 操作记录，请提炼一条不超过 60 字的中文"
    "「教训」：下次遇到类似改动，应注意什么、避开什么坑。只输出教训本身，不要解释、不要前缀。\n\n"
    "操作：{action}\n决策/失败点：{decision}\n结局：{outcome}\n"
)


def _extract_lesson(llm, action_summary, decision, outcome):
    """用 LLM 从一次操作记录提炼教训（受成本熔断约束）。任何异常返回空字符串。"""
    try:
        out = llm.chat([{
            "role": "user",
            "content": _LESSON_PROMPT.format(
                action=action_summary, decision=decision, outcome=outcome),
        }], stream=False, temperature=0.0)
        line = (out or "").strip().splitlines()
        line = line[0].strip() if line else ""
        return _scrub(line, limit=200)
    except Exception:  # noqa: BLE001
        return ""
