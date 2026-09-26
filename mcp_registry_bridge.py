"""官方 MCP Registry 检索结果到自动连接候选的边界。"""
from __future__ import annotations

from typing import Any, Callable, Optional

import mcp_registry
import project_state
from mcp_candidates import _candidate_view
from mcp_validation import validate_extracted_config


def registry_fn_for(root: str) -> Callable[[str], dict[str, Any]]:
    """返回绑定 `root` 缓存 + 真 HTTP 的 Registry 检索可调用（供 api/tools 注入）。

    缓存落 `project_state.path(root, "mcp_registry_cache.json")`（TTL 24h）；
    缓存路径解析失败退化为无缓存直连，绝不上抛（外层 search_registry 亦全兜底）。
    """
    def _fn(need: str) -> dict[str, Any]:
        try:
            cache: Optional[Any] = mcp_registry.RegistryCache(
                project_state.path(root, "mcp_registry_cache.json"))
        except Exception:
            cache = None
        return mcp_registry.search_registry(need, cache=cache)
    return _fn


_NET_ERROR_MARKERS = ("timeouterror", "timed out", "timeout", "urlerror", "connectionerror",
                      "connectionreseterror", "remotedisconnected", "incompleteread",
                      "sslerror", "socket", "oserror", "network")


def humanize_registry_error(err: str) -> str:
    """Registry/联网软降级错误 → 可行动中文；非网络类错误原样保留（截断 300）。

    用于把「裸 TimeoutError: The read operation timed out」之类的底层异常，转成
    前端可直读的中文提示（含重试仍失败说明 + 已回退离线索引的安抚），避免真机那样
    直接把 Python 异常名透给用户。非网络类错误（如 Registry 结构异常）则原样截断返回。
    """
    text = (err or "").strip()
    if not text:
        return ""
    low = text.lower()
    if any(m in low for m in _NET_ERROR_MARKERS):
        host = mcp_registry.REGISTRY_BASE.replace("https://", "")
        return (f"联网检索超时：无法访问 MCP 官方 Registry（{host}），已重试仍失败。"
                "请检查网络或配置代理后重试；本次已回退离线精选索引与联网抓取。")
    return text[:300]


def _registry_layer(registry_fn: Optional[Callable[[str], dict[str, Any]]],
                    need: str) -> tuple[list[dict[str, Any]], str, str]:
    """调用 registry_fn 并把结果**强制过 R1-R9**，返回 (候选视图, 错误说明, 来源)。

    来源 src ∈ registry/cache/cache-stale（沿用 search_registry 词表，供上层透传）。
    Registry 是 preview 服务、数据形态可能漂移；此处任何异常/异常结构一律软降级，
    交由调用方回落精选索引 —— 绝不打崩请求。
    """
    if not callable(registry_fn):
        return [], "", ""
    try:
        res = registry_fn(need)
    except Exception as exc:  # noqa: BLE001
        return [], humanize_registry_error(f"{type(exc).__name__}: {exc}"), ""
    if not isinstance(res, dict):
        return [], "", ""
    if not res.get("ok"):
        return [], humanize_registry_error(str(res.get("error") or "")), ""
    src = str(res.get("source") or "registry")
    views: list[dict[str, Any]] = []
    for cfg in res.get("candidates") or []:
        if not isinstance(cfg, dict):
            continue
        if mcp_registry.is_eda_query(need) and not mcp_registry.is_eda_candidate(cfg):
            continue
        ok, errs = validate_extracted_config(cfg)
        if not ok:                                        # registry 数据也绝不绕过信任闸门
            continue
        # 能过闸门的 registry 候选即视为可信（R6/R8 已对受信域/来源做了裁决）
        views.append(_candidate_view(cfg, "trusted", errs))
    return views, "", src


