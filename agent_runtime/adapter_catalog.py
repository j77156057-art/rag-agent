"""Safe, project-scoped catalog for preview and MCP domain adapters."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .preview_adapters import preview_adapters
from .project_profile import load_profile, save_profile

_GENERATED_DIR = ".docmind/preview-adapters"


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
    for manifest in generated(project_root):
        rows.append({
            "id": manifest["id"], "label": manifest["label"],
            "kind": manifest["domain"], "evidence": manifest["evidence"],
            "capture_adapter": manifest["capture_adapter"],
            "available": manifest["status"] == "active",
            "requires_connector": bool(manifest.get("connector_hint")),
            "project_scoped": True, "status": manifest["status"],
            "generated": True,
            "refresh_tool": manifest.get("refresh_tool") or "",
            "connector_hint": manifest.get("connector_hint") or "",
            "validation": manifest.get("validation") or [],
        })
    return {"project_root": root, "adapters": rows, "count": len(rows)}


def _clean_id(value: Any) -> str:
    import re
    return re.sub(r"[^a-z0-9_.-]+", "-", str(value or "").strip().lower())[:64].strip(".-")


def generated(project_root: str | Path) -> list[dict[str, Any]]:
    root = Path(project_root).expanduser().resolve()
    folder = root / _GENERATED_DIR
    rows: list[dict[str, Any]] = []
    if not folder.is_dir():
        return rows
    import json
    for path in sorted(folder.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if not isinstance(raw, dict) or _clean_id(raw.get("id")) != path.stem:
            continue
        raw["id"] = path.stem
        raw["label"] = str(raw.get("label") or path.stem)[:160]
        raw["domain"] = str(raw.get("domain") or "generic")[:40]
        raw["status"] = str(raw.get("status") or "pending") if raw.get("status") in {"pending", "active", "rejected"} else "pending"
        raw["evidence"] = str(raw.get("evidence") or "模型生成的领域预览适配器")[:300]
        raw["capture_adapter"] = str(raw.get("capture_adapter") or "agent_generated")[:100]
        rows.append(raw)
    return rows[:64]


def create_generated(project_root: str | Path, spec: dict[str, Any]) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("项目目录不存在")
    adapter_id = _clean_id(spec.get("id") or spec.get("name"))
    if not adapter_id:
        raise ValueError("适配器 id 不能为空")
    folder = root / _GENERATED_DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (adapter_id + ".json")
    if path.exists():
        raise ValueError("适配器草稿已存在")
    import json
    manifest = {
        "schema": "docmind.preview-adapter.v1", "id": adapter_id,
        "label": str(spec.get("label") or adapter_id)[:160],
        "domain": str(spec.get("domain") or "generic")[:40],
        "artifact_kinds": [str(item)[:32] for item in list(spec.get("artifact_kinds") or [])[:16]],
        "capture_adapter": str(spec.get("capture_adapter") or "agent_generated")[:100],
        "refresh_tool": str(spec.get("refresh_tool") or "")[:120],
        "connector_hint": str(spec.get("connector_hint") or "")[:120],
        "validation": [str(item)[:240] for item in list(spec.get("validation") or [])[:16]],
        "evidence": str(spec.get("evidence") or "模型生成的领域预览适配器")[:300],
        "status": "pending",
    }
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def approve_generated(project_root: str | Path, adapter_id: str, approved: bool) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    clean = _clean_id(adapter_id)
    rows = {item["id"]: item for item in generated(root)}
    if clean not in rows:
        raise ValueError("没有找到该适配器草稿")
    import json
    path = root / _GENERATED_DIR / (clean + ".json")
    rows[clean]["status"] = "active" if approved else "rejected"
    path.write_text(json.dumps(rows[clean], ensure_ascii=False, indent=2), encoding="utf-8")
    return rows[clean]


def configure(project_root: str | Path, adapter_ids: list[str]) -> dict[str, Any]:
    """Persist only known non-secret preview adapter IDs for this project."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("项目目录不存在")
    allowed = set(preview_adapters()) | {"visual"} | {item["id"] for item in generated(root) if item["status"] == "active"}
    selected = list(dict.fromkeys(str(item or "").strip().lower() for item in adapter_ids))
    unknown = [item for item in selected if item and item not in allowed]
    if unknown:
        raise ValueError("未知预览适配器：" + ",".join(unknown[:8]))
    profile = load_profile(root)
    profile["preview_adapters"] = [item for item in selected if item]
    return save_profile(root, profile)


__all__ = ["catalog", "configure", "generated", "create_generated", "approve_generated"]
