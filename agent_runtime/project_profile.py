"""项目级能力画像。

画像只保存当前项目可复用的能力摘要，供工作流重启和开发舱展示。它不
保存命令参数、环境变量、请求头或任何凭据；详细配置仍由项目自己的
MCP/工具状态文件负责，并继续受现有审批边界保护。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

PROFILE_VERSION = 1
PROFILE_FILENAME = "project-profile.json"
_SECRET = re.compile(r"(?i)(api[_-]?key|token|password|passwd|secret|private[_-]?key|authorization)\s*[:=]\s*[^\s,;]+")
_MAX_ITEMS = 64


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _text(value: Any, limit: int = 240) -> str:
    text = str(value or "").replace("\x00", "").strip()
    text = _SECRET.sub(lambda match: match.group(1) + "=[REDACTED]", text)
    return text[:limit]


def _items(value: Any, limit: int = _MAX_ITEMS) -> list[str]:
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw:
        text = _text(item)
        if text and text not in seen:
            seen.add(text)
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _default(root: str = "") -> dict[str, Any]:
    return {
        "version": PROFILE_VERSION,
        "project_id": "",
        "project_root": _text(root, 1000),
        "kind": "generic",
        "tools": [],
        "mcp": [],
        "run_commands": [],
        "acceptance_methods": [],
        "acceptance_scripts": [],
        "preview_adapters": [],
        "sources": [],
        "updated_at": "",
    }


def profile_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve() / ".docmind" / PROFILE_FILENAME


def _normalize_mcp(value: Any) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows[:_MAX_ITEMS]:
        if isinstance(raw, str):
            raw = {"key": raw}
        if not isinstance(raw, Mapping):
            continue
        key = _text(raw.get("key") or raw.get("name") or raw.get("server_name"), 120)
        if not key or key in seen:
            continue
        seen.add(key)
        item: dict[str, Any] = {
            "key": key,
            "name": _text(raw.get("name") or raw.get("label") or key, 160),
            "enabled": bool(raw.get("enabled", True)),
        }
        transport = _text(raw.get("transport"), 30)
        if transport in {"stdio", "http", "sse", "streamable-http"}:
            item["transport"] = transport
        caps = _items(raw.get("capabilities"), 32)
        if caps:
            item["capabilities"] = caps
        summary = _text(raw.get("summary") or raw.get("config_summary"), 240)
        if summary:
            item["summary"] = summary
        out.append(item)
    return out


def _normalize(raw: Mapping[str, Any], root: str = "") -> dict[str, Any]:
    out = _default(root)
    out["version"] = PROFILE_VERSION
    for key in ("project_id", "project_root", "kind", "updated_at"):
        if key in raw:
            out[key] = _text(raw.get(key), 1000 if key == "project_root" else 160)
    for key in ("tools", "run_commands", "acceptance_methods", "acceptance_scripts", "preview_adapters", "sources"):
        out[key] = _items(raw.get(key))
    out["mcp"] = _normalize_mcp(raw.get("mcp"))
    if root:
        out["project_root"] = _text(root, 1000)
    return out


def load_profile(root: str | Path) -> dict[str, Any]:
    path = profile_path(root)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return _normalize(raw if isinstance(raw, Mapping) else {}, str(Path(root).expanduser().resolve()))
    except (OSError, ValueError, TypeError):
        return _default(str(Path(root).expanduser().resolve()))


def save_profile(root: str | Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    path = profile_path(root)
    normalized = _normalize(profile, str(Path(root).expanduser().resolve()))
    normalized["updated_at"] = _now()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    return normalized


def _append(values: list[str], value: Any) -> None:
    for item in _items(value, 16):
        if item not in values and len(values) < _MAX_ITEMS:
            values.append(item)


def _collect_task(task: Mapping[str, Any], profile: dict[str, Any]) -> None:
    for key in ("tools", "tool_allowlist", "tool_groups"):
        _append(profile["tools"], task.get(key))
    mcp = task.get("mcp")
    if isinstance(mcp, Mapping):
        profile["mcp"] = _normalize_mcp(profile["mcp"] + [mcp])
    elif isinstance(mcp, (list, tuple)):
        profile["mcp"] = _normalize_mcp(profile["mcp"] + list(mcp))
    elif isinstance(mcp, str) and mcp.strip() and mcp.strip().lower() not in {"auto", "none", "off"}:
        profile["mcp"] = _normalize_mcp(profile["mcp"] + [{"key": mcp}])
    for key in ("run_command", "run_commands", "command", "commands", "test_command", "verify_command"):
        _append(profile["run_commands"], task.get(key))
    for key in ("acceptance_script", "acceptance_scripts", "verify_script", "verification_script"):
        _append(profile["acceptance_scripts"], task.get(key))


def merge_profile(root: str | Path, state: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a workflow projection into the project's durable profile."""
    profile = load_profile(root)
    # 已连接的 MCP 只写连接器摘要；server_configs 返回的 command、args、env、
    # headers 可能包含本机路径或凭据，绝不直接落盘到画像。
    try:
        import mcp_client
        configured: list[dict[str, Any]] = []
        for server in mcp_client.server_configs(str(root)):
            if not isinstance(server, Mapping):
                continue
            configured.append({
                "key": server.get("key"),
                "name": server.get("label") or server.get("name") or server.get("key"),
                "enabled": server.get("enabled", True),
                "transport": server.get("transport"),
                "capabilities": server.get("capabilities") or [],
            })
        profile["mcp"] = _normalize_mcp(configured + profile["mcp"])
    except Exception:
        pass
    if state.get("project_id"):
        profile["project_id"] = _text(state.get("project_id"), 160)
    if state.get("kind"):
        profile["kind"] = _text(state.get("kind"), 40) or "generic"
    _append(profile["sources"], state.get("sources"))
    for task in list(state.get("tasks") or [])[:64]:
        if isinstance(task, Mapping):
            _collect_task(task, profile)
    contract = state.get("acceptance_contract")
    if isinstance(contract, Mapping):
        for item in list(contract.get("items") or [])[:32]:
            if isinstance(item, Mapping):
                _append(profile["acceptance_methods"], item.get("method"))
                evidence = item.get("evidence")
                for value in _items(evidence, 16):
                    if re.search(r"(?:^|[./\\])[^/\\]+\.(?:py|js|ts|ps1|sh|bat)$", value, re.I):
                        _append(profile["acceptance_scripts"], value)
    preview = state.get("preview")
    if isinstance(preview, Mapping):
        _append(profile["preview_adapters"], preview.get("adapter"))
        for artifact in list(preview.get("artifacts") or [])[:64]:
            if isinstance(artifact, Mapping):
                _append(profile["preview_adapters"], artifact.get("adapter"))
    for result in list((state.get("results") or {}).get("results", {}).values())[:64] if isinstance(state.get("results"), Mapping) else []:
        if not isinstance(result, Mapping):
            continue
        for key in ("run_command", "run_commands", "command", "commands"):
            _append(profile["run_commands"], result.get(key))
        for artifact in list(result.get("artifacts") or [])[:32]:
            if isinstance(artifact, Mapping):
                _append(profile["preview_adapters"], artifact.get("adapter") or artifact.get("domain"))
    profile = _normalize(profile, str(Path(root).expanduser().resolve()))
    return save_profile(root, profile)


__all__ = ["PROFILE_FILENAME", "PROFILE_VERSION", "load_profile", "merge_profile", "profile_path", "save_profile"]
