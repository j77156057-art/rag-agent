"""MCP 候选的凭证元数据、信任显示和去重。"""
from __future__ import annotations

import os
import re

import mcp_client
from typing import Any, Optional

SECRET_INLINE_RE = re.compile(r"@secret:([A-Za-z0-9_.-]+)")


# 诊断图默认 6 节点（Phase 2 C5 集合，非 Phase 1 §6.1 旧集合）
DEFAULT_DIAG_STAGES = [
    ("search", "搜索"), ("fetch", "抓取"), ("extract", "抽取"),
    ("validate", "校验"), ("probe", "试连"), ("confirm", "确认"),
]

def render_diagnostic_svg(stages: Optional[list[dict[str, Any]]] = None,
                          failed_at: Optional[str] = None) -> str:
    """确定性生成连接诊断 SVG（无 emoji、stroke=currentColor，引用 Token 变量）。

    stages 为 [{'key','label','state'}]；state ∈ done/active/fail/warn/idle。
    failed_at 为卡住的 stage key（高亮 fail/warn）。无 stages 时用默认 6 节点。
    """
    stages = stages or [{"key": k, "label": lbl, "state": "idle"} for k, lbl in DEFAULT_DIAG_STAGES]
    parts = ['<svg viewBox="0 0 680 96" class="mcp-diag" role="img" aria-label="连接诊断" '
             'xmlns="http://www.w3.org/2000/svg">']
    parts.append('<style>'
                 '.mcp-seg-done{stroke:var(--diag-seg-done,#1c9e66);}'
                 '.mcp-seg-idle{stroke:var(--diag-seg-idle,#dde3ee);}'
                 '.mcp-ring{fill:var(--bg-raised,#fff);stroke-width:2;}'
                 '.n-done{stroke:var(--diag-done,#1c9e66);}'
                 '.n-fail{stroke:var(--diag-fail,#e0484f);}'
                 '.n-warn{stroke:var(--diag-warn,#c8811c);}'
                 '.n-active{stroke:var(--diag-active,#2f6fed);}'
                 '.n-idle{stroke:var(--diag-idle,#98a3b4);}'
                 '.mcp-lbl{font:510 12px var(--font-ui,system-ui);fill:var(--text,#222b38);}'
                 '</style>')
    n = len(stages)
    step = 600 / max(n, 1)
    prev_x = 40
    for i, st in enumerate(stages):
        x = 40 + i * step
        state = st.get("state", "idle")
        if failed_at and st.get("key") == failed_at:
            state = "fail" if state in ("fail", "idle") else state
        cls = {"done": "n-done", "fail": "n-fail", "warn": "n-warn",
               "active": "n-active", "idle": "n-idle"}.get(state, "n-idle")
        if i > 0:
            seg = "mcp-seg-done" if state in ("done", "fail", "warn", "active") else "mcp-seg-idle"
            parts.append(f'<line class="{seg}" x1="{prev_x}" y1="40" x2="{x}" y2="40"/>')
        parts.append(f'<g class="mcp-node" data-state="{state}" data-step="{i+1}" '
                     f'transform="translate({x},40)">')
        parts.append(f'<circle class="mcp-ring {cls}" r="16"/>')
        parts.append(f'<text class="mcp-lbl" y="36" text-anchor="middle">{st.get("label","")}</text>')
        parts.append('</g>')
        prev_x = x
    parts.append('</svg>')
    return "".join(parts)


def _is_official_namespace(ns: str) -> bool:
    """只有 MCP 项目自身的命名空间可单凭名字标为官方。

    `io.github.*` 表示 GitHub 发布者，不能证明是目标软件厂商发布。
    """
    return bool(ns) and ns.startswith("io.modelcontextprotocol")


def _trust_tier(prov: dict[str, Any], trust: str) -> str:
    """显示用信任分档（不改动 R8 自动填参闸门语义，R8 仍看 `trust`）。

    - unknown：不可信来源域（source_untrusted）
    - official：registry 官方命名空间，或精选索引收录的官方 server
    - community：受信来源域（如 github.com）但非官方命名空间的第三方 server
    """
    if trust == "source_untrusted":
        return "unknown"
    ns = str(prov.get("namespace") or "")
    if prov.get("curated"):
        return "official"
    if _is_official_namespace(ns):
        return "official"
    return "community"


def _candidate_view(cand: dict[str, Any], trust: str, errors: list[str]) -> dict[str, Any]:
    prov = cand.get("provenance") or {}
    specs = prov.get("secret_specs")
    if not isinstance(specs, list):
        specs = []
        for sec in ("env", "headers"):
            for value in (cand.get(sec) or {}).values():
                for match in SECRET_INLINE_RE.finditer(str(value)):
                    name = match.group(1)
                    if not any(s.get("name") == name for s in specs):
                        specs.append({"name": name, "required": True})
    normalized_specs: list[dict[str, Any]] = []
    for spec in specs:
        if not isinstance(spec, dict) or not spec.get("name"):
            continue
        name = str(spec["name"])
        existing = next((item for item in normalized_specs if item["name"] == name), None)
        if existing is None:
            normalized_specs.append({"name": name, "required": bool(spec.get("required"))})
        elif spec.get("required"):
            existing["required"] = True
    return {"config": cand, "trust": trust,
            "trust_tier": _trust_tier(prov, trust),
            "secrets": normalized_specs,
            "validation_errors": errors}


def _dedupe(cands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for c in cands:
        cfg = c["config"]
        key = (cfg.get("transport"), cfg.get("command") or cfg.get("url"))
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out



def build_candidate(parsed: list[str], *, provenance: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """把 parse_install_command 的 argv 经 resolve_command（mcp_client.py:72）解析为绝对路径
    写入 cand['command']；解析失败置 cand['command_unresolved']=True。组装候选。
    """
    launcher = parsed[0]
    resolved = mcp_client.resolve_command(launcher)
    abs_ok = (resolved != launcher) and os.path.isabs(resolved)
    return {
        "transport": "stdio",
        "command": resolved if abs_ok else launcher,
        "args": list(parsed[1:]),
        "url": "",
        "env": {},
        "headers": {},
        "provenance": dict(provenance or {}),
        "command_unresolved": not abs_ok,
    }


