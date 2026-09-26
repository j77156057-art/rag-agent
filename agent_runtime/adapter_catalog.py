"""Safe, project-scoped catalog for preview and MCP domain adapters."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .preview_adapters import preview_adapters


_DESCRIPTIONS = {
    "visual": ("网页预览", "同源网页截图", "browser"),
    "game": ("游戏引擎", "运行画面和引擎状态", "game_screenshot_or_engine_mcp"),
    "eda": ("EDA 设计", "原理图、PCB 和 ERC/DRC 证据", "eda_mcp_or_editor_adapter"),
    "native": ("桌面软件", "原生窗口和专用软件状态", "native_window_adapter"),
}


def catalog(project_root: str | Path = "", *, connectors: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    root = str(Path(project_root).expanduser().resolve()) if project_root else ""
    rows: list[dict[str, Any]] = []
    for name in sorted(set(preview_adapters()) | {"visual"}):
        label, evidence, capture = _DESCRIPTIONS.get(name, (name, "领域产物和状态", name))
        rows.append({
            "id": name, "label": label, "kind": name,
            "evidence": evidence, "capture_adapter": capture,
            "available": True, "requires_connector": name not in {"visual"},
            "project_scoped": bool(root),
        })
    for connector in connectors or []:
        if not isinstance(connector, dict):
            continue
        key = str(connector.get("key") or "").strip()
        if not key:
            continue
        rows.append({
            "id": "mcp:" + key,
            "label": str(connector.get("label") or connector.get("name") or key)[:160],
            "kind": "mcp", "evidence": "MCP 工具返回的状态或截图",
            "capture_adapter": "mcp", "available": bool(connector.get("enabled")),
            "requires_connector": True, "connector_key": key,
            "transport": str(connector.get("transport") or "")[:40],
        })
    return {"project_root": root, "adapters": rows, "count": len(rows)}


__all__ = ["catalog"]
