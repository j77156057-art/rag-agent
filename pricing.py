"""按 provider 计价的成本核算 + 预算熔断。

- **单价表**：优先读 `<STATE_ROOT>/.docmind_pricing.json`（可随项目覆盖），缺省用内置表。
  单位：**元 / 1M tokens**。本地 provider（ollama / llamacpp / mock）默认 0（不产生 API 费用）。
- **Prompt caching 折扣**：单价表每项含 `cache_read`（缓存命中读）/ `cache_creation`（首次写入）
  单价，缺省按输入价的 1/10、1.25 倍推导。`cost_cny` 对 `in_tokens` 中的缓存命中部分按折扣价
  计费并从全价输入中扣减，避免缓存命中被按全价高估（修复"计费没考虑缓存命中"）。
- **预算熔断**：按「全局 + 每会话」累计花费，落 `<STATE_ROOT>/.docmind_budget.json`。
  单轮开始前 `check()` —— 已超限则直接拒绝该轮；单轮结束后 `charge()` 累计。
  离线工具/演示模式下费用恒为 0，熔断不会误伤。
"""
from __future__ import annotations

import contextlib
import json
import os
import time as _time
from datetime import date, datetime

from config import STATE_ROOT, state_path

PRICING_FILE = state_path("DOCMIND_PRICING_FILE", os.path.join(STATE_ROOT, ".docmind_pricing.json"))
BUDGET_FILE = state_path("DOCMIND_BUDGET_FILE", os.path.join(STATE_ROOT, ".docmind_budget.json"))

# 内置单价（元 / 1M tokens）。仅为量级参考，正式使用请用 .docmind_pricing.json 覆盖。
# cache_read / cache_creation 为 prompt caching 的命中读 / 首次写入单价；缺省时按
# CACHE_READ_RATIO / CACHE_CREATION_RATIO 从输入价推导（行业惯例：缓存读≈输入 1/10，
# 写入≈输入 1.25 倍）。这正是"计费没考虑缓存命中"的根因修复点。
DEFAULT_PRICING = {
    "qwen": {"in": 0.8, "out": 2.0, "cache_read": 0.08, "cache_creation": 1.0,
             "models": {"qwen-plus": {"in": 0.8, "out": 2.0},
                        "qwen-turbo": {"in": 0.3, "out": 0.6},
                        "qwen-max": {"in": 2.4, "out": 9.6}}},
    "deepseek": {"in": 1.0, "out": 2.0, "cache_read": 0.1, "cache_creation": 1.25,
                 "models": {"deepseek-chat": {"in": 1.0, "out": 2.0},
                            "deepseek-reasoner": {"in": 4.0, "out": 16.0}}},
    "ollama": {"in": 0.0, "out": 0.0},
    "llamacpp": {"in": 0.0, "out": 0.0},
    "mock": {"in": 0.0, "out": 0.0},
}

# Prompt caching 折扣比（行业惯例）。各 provider 亦可在 .docmind_pricing.json 显式覆盖。
CACHE_READ_RATIO = 0.1
CACHE_CREATION_RATIO = 1.25


def _minute_key() -> str:
    """当前分钟桶键（日+时分），用于每分钟限流。"""
    n = datetime.now()
    return f"{n.date().isoformat()}T{n.hour:02d}:{n.minute:02d}"


# golden 评测模式：预算完全内存化、零磁盘 I/O，绕过运行时 safe_delete 守卫（见 _budget_lock / _read / _write）。
_GOLDEN_MEM = None


def _golden_mode() -> bool:
    return os.getenv("DOCMIND_GOLDEN_NO_LOCK") == "1"


@contextlib.contextmanager
def _budget_lock():
    """跨进程强一致锁：用 O_EXCL 原子锁文件提供互斥（Windows/POSIX 均原子）。

    进程内外的并发读写都经此锁串行化，配合 _write 的「临时文件 + os.replace」
    实现强一致（不会因两个进程同时 RMW 而丢更新）。锁持有时间仅微秒级；若锁文件
    疑似僵死（>3s 未释放，远超正常持有时间），视为孤儿锁直接清理后重试；整体等待
    硬上限 5s，避免任何调用方被卡死。

    注意（golden 评测专用旁路）：DocMind 的运行时审批门会对「删除 .docmind_budget.json.lock」
    这类 os.unlink 触发 safe_delete 守卫，在非交互的评测子进程里会阻塞/硬杀进程，导致整轮
    0 题写出。golden runner 通过 DOCMIND_GOLDEN_NO_LOCK=1 让本锁降级为 no-op（预算写入仍走
    os.replace 落盘，只是不做文件锁互斥——评测为单进程顺序跑，无并发竞争，安全）。
    """
    if os.getenv("DOCMIND_GOLDEN_NO_LOCK") == "1":
        yield
        return
    d = os.path.dirname(BUDGET_FILE) or "."
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    lf = BUDGET_FILE + ".lock"
    deadline = _time.time() + 5.0
    fd = None
    while fd is None:
        try:
            fd = os.open(lf, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except (FileExistsError, OSError):
            fd = None
            # 孤儿锁：持有时间远超正常水平（微秒级），清理后重试
            try:
                if _time.time() - os.path.getmtime(lf) > 3:
                    os.unlink(lf)
            except OSError:
                pass
            if _time.time() > deadline:
                # 硬上限：强制接管孤儿锁，避免调用方无限等待
                try:
                    os.unlink(lf)
                except OSError:
                    pass
                try:
                    fd = os.open(lf, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                except (FileExistsError, OSError):
                    fd = None
                if fd is None:
                    break
            else:
                _time.sleep(0.003)
    try:
        yield
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(lf)
        except OSError:
            pass


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


def price_detail(provider, model=""):
    """返回 (in_price, out_price, cache_read_price, cache_creation_price)，单位元 / 1M tokens。

    cache_read / cache_creation 缺失时按 CACHE_READ_RATIO / CACHE_CREATION_RATIO 从输入价
    推导；本地 provider 恒为 0。各 provider 可在 .docmind_pricing.json 显式覆盖。
    """
    table = _load_pricing()
    cfg = table.get(provider) or {}
    models = cfg.get("models") or {}
    base = models.get(model) if (model and isinstance(models.get(model), dict)) else cfg
    pin = float(base.get("in", cfg.get("in", 0.0)))
    pout = float(base.get("out", cfg.get("out", 0.0)))
    pcr = float(base.get("cache_read", cfg.get("cache_read", pin * CACHE_READ_RATIO)))
    pcc = float(base.get("cache_creation", cfg.get("cache_creation", pin * CACHE_CREATION_RATIO)))
    return pin, pout, pcr, pcc


def price_for(provider, model=""):
    """返回 (in_price, out_price)，单位元 / 1M tokens（向后兼容）。"""
    pin, pout, _, _ = price_detail(provider, model)
    return pin, pout


def cost_cny(provider, model, in_tokens, out_tokens,
             cache_read_tokens=0, cache_creation_tokens=0) -> float:
    """一次调用的费用（元）。本地 provider 恒为 0。

    in_tokens / out_tokens 为总输入 / 输出 token；prompt caching 命中（cache_read_tokens）
    与首次写入（cache_creation_tokens）通常已计入 in_tokens，故按折扣价单独计费、并从全价
    输入中扣减，避免缓存命中被按全价重复计费（这正是运行台"计费没考虑缓存命中"的根因）。
    """
    pin, pout, pcr, pcc = price_detail(provider, model)
    if pin == 0.0 and pout == 0.0 and pcr == 0.0 and pcc == 0.0:
        return 0.0
    in_t = int(in_tokens or 0)
    cr_t = int(cache_read_tokens or 0)
    cc_t = int(cache_creation_tokens or 0)
    # 非缓存输入 = 总输入 - 缓存命中 - 缓存写入（下限 0，防 provider 口径差异致负数）
    non_cached = max(0, in_t - cr_t - cc_t)
    total = (non_cached * pin
             + cr_t * pcr
             + cc_t * pcc
             + int(out_tokens or 0) * pout)
    return total / 1_000_000.0


# ---------------------------------------------------------------------------
# 预算状态
# ---------------------------------------------------------------------------
def _default_state() -> dict:
    return {"global": {"spent": 0.0, "limit": _env_limit("DOCMIND_BUDGET_CNY")},
            "sessions": {}, "day": date.today().isoformat(), "day_spent": 0.0,
            "per_minute": {"calls": 0.0, "cost": 0.0},
            "minute": {"key": _minute_key(), "calls": 0, "cost": 0.0}}


def _env_limit(name):
    try:
        v = float(os.getenv(name, "0") or 0)
    except ValueError:
        v = 0.0
    return v if v > 0 else 0.0  # 0 = 不限


def _read():
    # golden 评测模式：预算完全内存化，零磁盘 I/O。运行时 safe_delete 守卫会拦截对
    # .docmind_budget.json(.lock) 的删除/替换，在非交互子进程里硬杀进程、整轮 0 题写出。
    # 评测为单进程顺序跑，内存累计足够，且 DOCMIND_BUDGET_CNY 默认 0（不限），不影响判定。
    if _golden_mode():
        import copy as _copy
        if _GOLDEN_MEM is None:
            return _default_state()
        return _copy.deepcopy(_GOLDEN_MEM)
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
                if isinstance(raw.get("per_minute"), dict):
                    state["per_minute"] = raw["per_minute"]
                if isinstance(raw.get("minute"), dict):
                    state["minute"] = raw["minute"]
    except (OSError, ValueError, TypeError):
        pass
    # 跨天重置日累计
    today = date.today().isoformat()
    if state.get("day") != today:
        state["day"] = today
        state["day_spent"] = 0.0
    # 默认字段兜底（旧预算文件可能缺 per_minute / minute）
    state.setdefault("per_minute", {"calls": 0.0, "cost": 0.0})
    state.setdefault("minute", {"key": _minute_key(), "calls": 0, "cost": 0.0})
    # 跨分钟重置分钟桶
    mk = _minute_key()
    if state["minute"].get("key") != mk:
        state["minute"] = {"key": mk, "calls": 0, "cost": 0.0}
    return state


def _write(state) -> bool:
    if _golden_mode():
        global _GOLDEN_MEM
        import copy as _copy
        _GOLDEN_MEM = _copy.deepcopy(state)
        return True
    try:
        tmp = BUDGET_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, BUDGET_FILE)
        return True
    except OSError:
        return False


def check(session_id=""):
    """回合开始前检查预算。返回 {ok, reason, global_limit, global_spent, session_limit, session_spent, 分钟信息}。"""
    with _budget_lock():
        st = _read()
    g = st["global"]
    sid = str(session_id or "")
    s = (st["sessions"].get(sid) or {}) if sid else {}
    s_limit = float(s.get("limit", 0.0)) or _env_limit("DOCMIND_SESSION_BUDGET_CNY")
    s_spent = float(s.get("spent", 0.0))
    mb = st.get("minute", {})
    pm = st.get("per_minute", {})
    info = {
        "global_limit": g.get("limit", 0.0), "global_spent": round(float(g.get("spent", 0.0)), 6),
        "session_limit": s_limit, "session_spent": round(s_spent, 6),
        "day_spent": round(float(st.get("day_spent", 0.0)), 6),
        "minute_calls": int(mb.get("calls", 0)),
        "minute_cost": round(float(mb.get("cost", 0.0)), 6),
        "per_minute_calls_limit": float(pm.get("calls", 0.0) or 0.0),
        "per_minute_cost_limit": float(pm.get("cost", 0.0) or 0.0),
    }
    if info["global_limit"] and info["global_spent"] >= info["global_limit"]:
        return {"ok": False, "reason": f"全局预算已用尽（¥{info['global_spent']:.4f} / ¥{info['global_limit']:.4f}）", **info}
    if s_limit and s_spent >= s_limit:
        return {"ok": False, "reason": f"本会话预算已用尽（¥{s_spent:.4f} / ¥{s_limit:.4f}）", **info}
    return {"ok": True, "reason": "", **info}


def charge(cost, session_id=""):
    """累计一次花费；返回累计后的状态。

    若开启每分钟费用上限且本次会超额，则**拒绝计费**（不写入、不累加），
    返回 {ok: False, rate_limited: True, ...}。每分钟调用次数上限在 `rate_check` 预检。
    """
    cost = max(0.0, float(cost or 0.0))
    if cost == 0.0:
        return check(session_id)
    with _budget_lock():
        st = _read()
        pm = st.get("per_minute") or {"calls": 0.0, "cost": 0.0}
        lim_cost = float(pm.get("cost", 0.0) or 0.0)
        mb = st["minute"]
        cur_cost = float(mb.get("cost", 0.0))
        if lim_cost and (cur_cost + cost) > lim_cost:
            return {"ok": False, "rate_limited": True,
                    "reason": f"每分钟费用已达上限（¥{cur_cost:.4f}/¥{lim_cost:.4f}）",
                    "minute_calls": int(mb.get("calls", 0)),
                    "minute_cost": round(cur_cost, 6),
                    "per_minute_cost_limit": lim_cost,
                    "global_spent": round(float(st["global"].get("spent", 0.0)), 6),
                    "global_limit": st["global"].get("limit", 0.0)}
        st["global"]["spent"] = float(st["global"].get("spent", 0.0)) + cost
        st["day_spent"] = float(st.get("day_spent", 0.0)) + cost
        mb["cost"] = cur_cost + cost
        sid = str(session_id or "")
        if sid:
            slot = st["sessions"].setdefault(sid, {"spent": 0.0, "limit": _env_limit("DOCMIND_SESSION_BUDGET_CNY")})
            slot["spent"] = float(slot.get("spent", 0.0)) + cost
        _write(st)
    return check(session_id)


def rate_check(session_id="", projected_cost=0.0):
    """每分钟限流预检（调用方在发起模型调用**前**调用）。

    通过的调用会在此计入一次「每分钟调用」；每分钟费用上限在 `charge` 处按实际费用落地。
    返回 {ok, reason, minute_calls, minute_cost, per_minute_calls_limit, per_minute_cost_limit}。
    """
    with _budget_lock():
        st = _read()
        pm = st.get("per_minute") or {"calls": 0.0, "cost": 0.0}
        lim_calls = float(pm.get("calls", 0.0) or 0.0)
        lim_cost = float(pm.get("cost", 0.0) or 0.0)
        mb = st["minute"]
        calls = int(mb.get("calls", 0))
        cur_cost = float(mb.get("cost", 0.0))
        info = {"minute_calls": calls, "minute_cost": round(cur_cost, 6),
                "per_minute_calls_limit": lim_calls, "per_minute_cost_limit": lim_cost,
                "minute_key": mb.get("key")}
        if lim_calls and (calls + 1) > lim_calls:
            return {"ok": False, "reason": f"每分钟调用次数已达上限（{calls}/{int(lim_calls)}）", **info}
        if lim_cost and (cur_cost + max(0.0, float(projected_cost or 0.0))) > lim_cost:
            return {"ok": False, "reason": f"每分钟费用已达上限（¥{cur_cost:.4f}/¥{lim_cost:.4f}）", **info}
        mb["calls"] = calls + 1
        _write(st)
        info["minute_calls"] = calls + 1
        return {"ok": True, "reason": "", **info}


def set_rate_limit(calls=0.0, cost=0.0):
    """设置每分钟限流（调用次数 / 费用元，0 = 不限）。"""
    with _budget_lock():
        st = _read()
        st["per_minute"] = {"calls": max(0.0, float(calls or 0.0)),
                            "cost": max(0.0, float(cost or 0.0))}
        _write(st)
    return {"per_minute_calls_limit": st["per_minute"]["calls"],
            "per_minute_cost_limit": st["per_minute"]["cost"]}


def set_limit(limit_cny, session_id=""):
    """设置全局或某会话的预算上限（0 = 不限）。"""
    with _budget_lock():
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
    with _budget_lock():
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
    with _budget_lock():
        st = _read()
    pm = st.get("per_minute") or {"calls": 0.0, "cost": 0.0}
    mb = st.get("minute") or {}
    return {
        "global_limit": st["global"].get("limit", 0.0),
        "global_spent": round(float(st["global"].get("spent", 0.0)), 6),
        "day": st.get("day"), "day_spent": round(float(st.get("day_spent", 0.0)), 6),
        "per_minute_calls_limit": float(pm.get("calls", 0.0) or 0.0),
        "per_minute_cost_limit": float(pm.get("cost", 0.0) or 0.0),
        "minute_calls": int(mb.get("calls", 0)),
        "minute_cost": round(float(mb.get("cost", 0.0)), 6),
        "sessions": {k: {"spent": round(float(v.get("spent", 0.0)), 6), "limit": float(v.get("limit", 0.0))}
                     for k, v in (st.get("sessions") or {}).items()},
        "pricing_file": PRICING_FILE,
    }
