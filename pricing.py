"""按 provider 计价的成本核算 + 预算熔断。

- **单价表**：优先读 `<BASE_DIR>/.docmind_pricing.json`（可随项目覆盖），缺省用内置表。
  单位：**元 / 1M tokens**。本地 provider（ollama / llamacpp / mock）默认 0（不产生 API 费用）。
- **预算熔断**：按「全局 + 每会话」累计花费，落 `<BASE_DIR>/.docmind_budget.json`。
  单轮开始前 `check()` —— 已超限则直接拒绝该轮；单轮结束后 `charge()` 累计。
  离线工具/演示模式下费用恒为 0，熔断不会误伤。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date

from config import BASE_DIR

PRICING_FILE = os.getenv("DOCMIND_PRICING_FILE") or os.path.join(BASE_DIR, ".docmind_pricing.json")
BUDGET_FILE = os.getenv("DOCMIND_BUDGET_FILE") or os.path.join(BASE_DIR, ".docmind_budget.json")

# 内置单价（元 / 1M tokens）。仅为量级参考，正式使用请用 .docmind_pricing.json 覆盖。
DEFAULT_PRICING = {
    "qwen": {"in": 0.8, "out": 2.0,
             "models": {"qwen-plus": {"in": 0.8, "out": 2.0},
                        "qwen-turbo": {"in": 0.3, "out": 0.6},
                        "qwen-max": {"in": 2.4, "out": 9.6}}},
    "deepseek": {"in": 1.0, "out": 2.0,
                 "models": {"deepseek-chat": {"in": 1.0, "out": 2.0},
                            "deepseek-reasoner": {"in": 4.0, "out": 16.0}}},
    "ollama": {"in": 0.0, "out": 0.0},
    "llamacpp": {"in": 0.0, "out": 0.0},
    "mock": {"in": 0.0, "out": 0.0},
}

_lock = threading.Lock()


def _load_pricing() -> dict:
    data = {k: dict(v) for k, v in DEFAULT_PRICING.items()}
    try:
        if os.path.isfile(PRICING_FILE):
            with open(PRICING_FILE, "r", encoding="utf-8") as f:
                override = json.load(f)
            if isinstance(override, dict):
                for prov, cfg in override.items():
                    if isinstance(cfg, dict):
                        data.setdefault(prov, {}).update(cfg)
    except (OSError, ValueError):
        pass
    return data


def price_for(provider, model=""):
    """返回 (in_price, out_price)，单位元 / 1M tokens。"""
    table = _load_pricing()
    cfg = table.get(provider) or {}
    models = cfg.get("models") or {}
    if model and isinstance(models.get(model), dict):
        m = models[model]
        return float(m.get("in", cfg.get("in", 0.0))), float(m.get("out", cfg.get("out", 0.0)))
    return float(cfg.get("in", 0.0)), float(cfg.get("out", 0.0))


def cost_cny(provider, model, in_tokens, out_tokens) -> float:
    """一次调用的费用（元）。本地 provider 恒为 0。"""
    pin, pout = price_for(provider, model)
    if pin == 0.0 and pout == 0.0:
        return 0.0
    return (int(in_tokens or 0) * pin + int(out_tokens or 0) * pout) / 1_000_000.0


# ---------------------------------------------------------------------------
# 预算状态
# ---------------------------------------------------------------------------
def _default_state() -> dict:
    return {"global": {"spent": 0.0, "limit": _env_limit("DOCMIND_BUDGET_CNY")},
            "sessions": {}, "day": date.today().isoformat(), "day_spent": 0.0}


def _env_limit(name):
    try:
        v = float(os.getenv(name, "0") or 0)
    except ValueError:
        v = 0.0
    return v if v > 0 else 0.0  # 0 = 不限


def _read():
    state = _default_state()
    try:
        if os.path.isfile(BUDGET_FILE):
            with open(BUDGET_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                g = raw.get("global") or {}
                state["global"]["spent"] = float(g.get("spent", 0.0))
                lim = float(g.get("limit", 0.0))
                state["global"]["limit"] = lim if lim > 0 else _env_limit("DOCMIND_BUDGET_CNY")
                state["sessions"] = raw.get("sessions") or {}
                state["day"] = raw.get("day") or state["day"]
                state["day_spent"] = float(raw.get("day_spent", 0.0))
    except (OSError, ValueError, TypeError):
        pass
    # 跨天重置日累计
    today = date.today().isoformat()
    if state.get("day") != today:
        state["day"] = today
        state["day_spent"] = 0.0
    return state


def _write(state) -> bool:
    try:
        tmp = BUDGET_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, BUDGET_FILE)
        return True
    except OSError:
        return False


def check(session_id=""):
    """回合开始前检查预算。返回 {ok, reason, global_limit, global_spent, session_limit, session_spent}。"""
    with _lock:
        st = _read()
    g = st["global"]
    sid = str(session_id or "")
    s = (st["sessions"].get(sid) or {}) if sid else {}
    s_limit = float(s.get("limit", 0.0)) or _env_limit("DOCMIND_SESSION_BUDGET_CNY")
    s_spent = float(s.get("spent", 0.0))
    info = {
        "global_limit": g.get("limit", 0.0), "global_spent": round(float(g.get("spent", 0.0)), 6),
        "session_limit": s_limit, "session_spent": round(s_spent, 6),
        "day_spent": round(float(st.get("day_spent", 0.0)), 6),
    }
    if info["global_limit"] and info["global_spent"] >= info["global_limit"]:
        return {"ok": False, "reason": f"全局预算已用尽（¥{info['global_spent']:.4f} / ¥{info['global_limit']:.4f}）", **info}
    if s_limit and s_spent >= s_limit:
        return {"ok": False, "reason": f"本会话预算已用尽（¥{s_spent:.4f} / ¥{s_limit:.4f}）", **info}
    return {"ok": True, "reason": "", **info}


def charge(cost, session_id=""):
    """累计一次花费；返回累计后的状态。"""
    cost = max(0.0, float(cost or 0.0))
    if cost == 0.0:
        return check(session_id)
    with _lock:
        st = _read()
        st["global"]["spent"] = float(st["global"].get("spent", 0.0)) + cost
        st["day_spent"] = float(st.get("day_spent", 0.0)) + cost
        sid = str(session_id or "")
        if sid:
            slot = st["sessions"].setdefault(sid, {"spent": 0.0, "limit": _env_limit("DOCMIND_SESSION_BUDGET_CNY")})
            slot["spent"] = float(slot.get("spent", 0.0)) + cost
        _write(st)
    return check(session_id)


def set_limit(limit_cny, session_id=""):
    """设置全局或某会话的预算上限（0 = 不限）。"""
    with _lock:
        st = _read()
        lim = max(0.0, float(limit_cny or 0.0))
        if session_id:
            slot = st["sessions"].setdefault(str(session_id), {"spent": 0.0, "limit": 0.0})
            slot["limit"] = lim
        else:
            st["global"]["limit"] = lim
        _write(st)
    return check(session_id)


def reset(session_id=""):
    """清零花费（全局或指定会话）。"""
    with _lock:
        st = _read()
        if session_id:
            slot = st["sessions"].setdefault(str(session_id), {"spent": 0.0, "limit": 0.0})
            slot["spent"] = 0.0
        else:
            st["global"]["spent"] = 0.0
            st["day_spent"] = 0.0
            st["sessions"] = {}
        _write(st)
    return check(session_id)


def status():
    with _lock:
        st = _read()
    return {
        "global_limit": st["global"].get("limit", 0.0),
        "global_spent": round(float(st["global"].get("spent", 0.0)), 6),
        "day": st.get("day"), "day_spent": round(float(st.get("day_spent", 0.0)), 6),
        "sessions": {k: {"spent": round(float(v.get("spent", 0.0)), 6), "limit": float(v.get("limit", 0.0))}
                     for k, v in (st.get("sessions") or {}).items()},
        "pricing_file": PRICING_FILE,
    }
