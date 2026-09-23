"""受控的 MCP 能力发现与项目级路由清单。

能力发现只产生候选，不修改 Python 源码，也不会因为一次联网结果直接启用
路由。候选保存在项目文件中，前端/用户批准后才会进入路由器。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Callable

import project_state

CAPABILITIES_FILENAME = ".docmind_mcp_capabilities.json"
SCHEMA_VERSION = 1

_EDA_HINTS = {
    "pcb": "pcb", "印制板": "pcb", "电路板": "pcb", "layout": "pcb",
    "原理图": "schematic", "schematic": "schematic", "电路图": "schematic",
    "erc": "erc", "drc": "drc", "规则检查": "drc", "bom": "bom",
    "物料清单": "bom", "spice": "simulation", "仿真": "simulation",
    "netlist": "netlist", "网表": "netlist", "gerber": "gerber",
    "kicad": "eda", "altium": "eda", "easyeda": "eda", "eda": "eda",
}
_GENERAL_HINTS = {
    "read": "read", "读取": "read", "get": "read", "list": "read", "查询": "read",
    "write": "write", "create": "write", "update": "write", "修改": "write",
    "export": "export", "导出": "export", "import": "import", "导入": "import",
    "check": "validate", "verify": "validate", "检查": "validate", "校验": "validate",
    "run": "run", "simulate": "simulation", "仿真": "simulation",
}

_DIRECTORY = [
    {
        "id": "eda",
        "label": "EDA / PCB / 原理图",
        "keywords": ["eda", "pcb", "kicad", "altium", "easyeda", "原理图", "电路板", "仿真"],
        "summary": "连接 KiCad、Altium、EasyEDA 或仿真工具所提供的标准 MCP Server。",
        "capabilities": ["schematic", "pcb", "erc", "drc", "bom", "netlist", "gerber", "simulation"],
        "connection_options": [
            {"transport": "stdio", "when": "MCP Server 提供本地启动命令时", "value": "填写官方文档给出的 command 与 args"},
            {"transport": "http", "when": "EDA 插件在本机或局域网暴露 MCP 地址时", "value": "填写官方 Streamable HTTP /mcp 地址"},
        ],
        "setup_steps": [
            "先在目标 EDA 软件中安装并启用对应 MCP 插件或 Server。",
            "从项目主页复制官方启动命令或 /mcp 地址，不使用搜索摘要里的未验证命令。",
            "在下方添加连接器并点测试连接，成功读取 tools/list 后再点发现能力。",
            "检查生成的能力与表单，批准后才允许 Agent 自动路由到该连接器。",
        ],
        "template": {"key": "eda", "label": "EDA MCP", "transport": "stdio", "command": "", "args": [], "url": ""},
    },
]


def _path(root: str) -> str:
    return project_state.path(root, CAPABILITIES_FILENAME, legacy=CAPABILITIES_FILENAME)


def _load(root: str) -> dict[str, Any]:
    try:
        with open(_path(root), encoding="utf-8") as fh:
            value = json.load(fh)
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(root: str, value: dict[str, Any]) -> None:
    path = _path(root)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _words(text: str) -> list[str]:
    raw = re.findall(r"[a-zA-Z][a-zA-Z0-9_.-]{1,}|[\u4e00-\u9fff]{2,}", text.lower())
    return list(dict.fromkeys(raw))


def _tool_text(tool: dict[str, Any]) -> str:
    schema = tool.get("input_schema") or {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    return " ".join([
        str(tool.get("name") or ""), str(tool.get("description") or ""),
        " ".join(str(k) for k in (props or {})),
    ])


def _infer_tool_caps(tool: dict[str, Any]) -> list[str]:
    text = _tool_text(tool)
    caps = set()
    for hint, cap in {**_EDA_HINTS, **_GENERAL_HINTS}.items():
        if hint in text.lower():
            caps.add(cap)
    return sorted(caps or {"mcp_tool"})


def _ui_requirements(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for tool in tools:
        schema = tool.get("input_schema") or {"type": "object"}
        props = schema.get("properties") if isinstance(schema, dict) else {}
        result.append({"tool": tool.get("name"), "kind": "form", "fields": list((props or {}).keys()),
                       "renderer": "generic_schema_form"})
    return result


def _sources_from_search(raw: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r"https?://[^\s)]+", raw or "")))[:8]


def search_directory(query: str, *, web_enabled: bool = False,
                     search_fn: Callable[[str], str] | None = None) -> dict[str, Any]:
    """搜索连接指引。联网只补充来源，绝不从摘要自动生成可执行命令。"""
    q = (query or "").strip().lower()
    if len(q) < 2:
        return {"ok": False, "error": "请输入至少 2 个字符。", "results": []}
    results = []
    for entry in _DIRECTORY:
        haystack = " ".join([entry["label"], entry["summary"], *entry["keywords"]]).lower()
        if q in haystack or any(word in haystack for word in _words(q)):
            item = dict(entry)
            item["sources"] = []
            item["source_status"] = "offline_guide"
            results.append(item)
    sources: list[str] = []
    search_error = ""
    if web_enabled and search_fn:
        try:
            raw = search_fn(f"{query} MCP server official GitHub installation connection usage")
            sources = _sources_from_search(raw)
            if not sources and raw:
                search_error = str(raw)[:300]
        except Exception as exc:
            search_error = str(exc)[:300]
    for item in results:
        item["sources"] = sources
        item["source_status"] = "web_sources" if sources else "offline_guide"
    if not results:
        results.append({
            "id": "generic", "label": f"{query} MCP 连接指引",
            "keywords": _words(q), "summary": "未命中内置领域模板，可按标准 MCP 连接流程配置。",
            "capabilities": [],
            "connection_options": [
                {"transport": "stdio", "when": "服务端提供本地命令", "value": "填写官方 command 与 args"},
                {"transport": "http", "when": "服务端提供 Streamable HTTP", "value": "填写官方 /mcp URL"},
            ],
            "setup_steps": [
                "确认来源是项目官网或可信代码仓库。",
                "复制官方连接参数到下方表单并测试连接。",
                "读取工具清单、发现能力并人工批准路由。",
            ],
            "template": {"key": re.sub(r"[^a-z0-9_-]+", "-", q).strip("-")[:32] or "custom-mcp",
                         "label": f"{query} MCP", "transport": "stdio", "command": "", "args": [], "url": ""},
            "sources": sources, "source_status": "web_sources" if sources else "offline_guide",
        })
    return {"ok": True, "query": query, "results": results, "search_error": search_error}


def discover(root: str, key: str, *, web_enabled: bool = False,
             search_fn: Callable[[str], str] | None = None) -> dict[str, Any]:
    """读取 MCP 工具并生成待审批候选。联网只补充来源，不直接决定启用。"""
    import mcp_client

    listed = mcp_client.list_tools(root, key)
    tools = listed.get("tools") or []
    blob = " ".join(_tool_text(t) for t in tools)
    domain = "eda" if any(h in blob.lower() for h in _EDA_HINTS) else "general"
    capabilities = set()
    mappings = []
    for tool in tools:
        caps = _infer_tool_caps(tool)
        capabilities.update(caps)
        mappings.append({"tool": tool.get("name"), "capabilities": caps})
    keywords = sorted(set(_words(blob)) | {c for c in capabilities if c != "mcp_tool"})[:80]
    sources: list[str] = []
    search_query = ""
    if web_enabled and search_fn:
        search_query = f"MCP {key} {'EDA PCB schematic ERC DRC SPICE' if domain == 'eda' else 'tools capabilities'}"
        try:
            sources = _sources_from_search(search_fn(search_query))
        except Exception:
            sources = []
    candidate = {
        "id": f"{key}-{int(time.time())}", "server": key, "domain": domain,
        "capabilities": sorted(capabilities), "keywords": keywords,
        "best_for": f"{domain.upper()} MCP 工具（由 tools/list 自动推断）" if domain == "eda" else "该 MCP 暴露的通用工具",
        "tool_mappings": mappings, "ui_requirements": _ui_requirements(tools),
        "sources": sources, "search_query": search_query,
        "confidence": round(min(0.98, 0.35 + 0.08 * len(capabilities)), 2),
        "tool_count": len(tools), "created_at": time.time(), "status": "pending",
    }
    data = _load(root)
    pending = data.setdefault("pending", {})
    pending[key] = candidate
    data["schema_version"] = SCHEMA_VERSION
    _write(root, data)
    return {"ok": True, "candidate": candidate}


def list_capabilities(root: str) -> dict[str, Any]:
    data = _load(root)
    return {"ok": True, "schema_version": data.get("schema_version", SCHEMA_VERSION),
            "active": data.get("active", {}), "pending": data.get("pending", {})}


def approve(root: str, key: str, approved: bool = True) -> dict[str, Any]:
    data = _load(root)
    candidate = (data.get("pending") or {}).get(key)
    if not candidate:
        return {"ok": False, "error": "没有待审批的能力候选。"}
    if approved:
        candidate = dict(candidate)
        candidate["status"] = "active"
        data.setdefault("active", {})[key] = candidate
    else:
        data.setdefault("rejected", {})[key] = {**candidate, "status": "rejected"}
    data.get("pending", {}).pop(key, None)
    _write(root, data)
    return {"ok": True, "approved": approved, "capability": candidate}


def active_for(root: str, key: str) -> dict[str, Any]:
    return (_load(root).get("active") or {}).get(key) or {}
