"""Built-in preview adapters for non-web project evidence.

These adapters do not pretend to capture a native window.  They normalize
evidence produced by an engine connector, game screenshot tool, or EDA MCP so
the workflow preview can display it and tell the agent which tool can refresh
the image.  A missing capture remains an explicit limitation in the artifact.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .core import register_preview_adapter


def _metadata(raw: Mapping[str, Any], *, domain: str, capture: str) -> dict[str, str]:
    existing = {str(k): str(v)[:300] for k, v in dict(raw.get("metadata") or {}).items()}
    existing.setdefault("domain", domain)
    existing.setdefault("capture_adapter", capture)
    return existing


def adapt_game(raw: Mapping[str, Any], _context: Mapping[str, Any]) -> dict[str, Any]:
    """Describe game screenshots and engine previews without claiming live capture."""
    kind = str(raw.get("kind") or "").lower()
    metadata = _metadata(raw, domain="game", capture="game_screenshot_or_engine_mcp")
    if kind in {"interactive", "live"} or raw.get("uri") or raw.get("preview_url"):
        metadata.setdefault("refresh_hint", "调用 game_screenshot 或已启用引擎 MCP 截图")
    else:
        metadata.setdefault("refresh_hint", "运行项目后调用 game_screenshot 获取最新画面")
    return {"adapter": "game", "metadata": metadata,
            "summary": raw.get("summary") or "游戏运行画面或引擎运行证据；截图需由游戏工具提供"}


def adapt_eda(raw: Mapping[str, Any], _context: Mapping[str, Any]) -> dict[str, Any]:
    """Describe schematic/PCB evidence returned by an EDA connector."""
    path = str(raw.get("path") or raw.get("uri") or "").lower()
    kind = str(raw.get("kind") or "").lower()
    metadata = _metadata(raw, domain="eda", capture="eda_mcp_or_editor_adapter")
    if kind not in {"image", "audio", "video", "interactive", "model"} and any(
        path.endswith(suffix) for suffix in (".kicad_sch", ".kicad_pcb", ".sch", ".pcb", ".brd")
    ):
        kind = "structured"
    metadata.setdefault("refresh_hint", "调用已审批的 EDA MCP 截图或状态工具")
    return {"adapter": "eda", "kind": kind or None, "metadata": metadata,
            "summary": raw.get("summary") or "EDA 原理图/PCB 证据；实际画面需由 EDA MCP 或编辑器适配器提供"}


def adapt_native(raw: Mapping[str, Any], _context: Mapping[str, Any]) -> dict[str, Any]:
    """Keep native desktop evidence visible while requiring a domain capture tool."""
    metadata = _metadata(raw, domain="native", capture="native_window_adapter")
    metadata.setdefault("refresh_hint", "需要对应桌面软件连接器或窗口截图适配器")
    return {"adapter": "native", "metadata": metadata,
            "summary": raw.get("summary") or "原生桌面软件证据；通用网页截图不适用"}


def register_builtin_adapters() -> None:
    register_preview_adapter("game", adapt_game, replace=True)
    register_preview_adapter("eda", adapt_eda, replace=True)
    register_preview_adapter("native", adapt_native, replace=True)


register_builtin_adapters()

__all__ = ["adapt_eda", "adapt_game", "adapt_native", "register_builtin_adapters"]
