"""Project-local queue of operations stopped by the existing approval gate.

This module records requests, never grants permissions. The original
``require_approval`` check remains authoritative for every operation.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

import project_state


_LOCK = threading.RLock()
_REPEAT_WINDOW_SECONDS = 30
_MAX_ROWS = 500
RISK_LEVELS = {
    "mcp_server": "L2", "mcp_capability": "L2", "install_tool": "L2",
    "apply_regions": "L2", "commit_region": "L3", "commit_all": "L3",
    "rollback_changeset": "L3", "rollback_skill": "L3", "update_skill": "L2",
}
SETTINGS_ONLY = frozenset({"mcp_server", "mcp_capability"})
QUEUE_APPROVABLE = frozenset(RISK_LEVELS) - SETTINGS_ONLY


def _path(root: str) -> Path:
    return Path(project_state.path(root, "approval_requests.jsonl"))


def _rows(root: str) -> list[dict[str, Any]]:
    try:
        with _path(root).open(encoding="utf-8") as handle:
            return [row for line in handle if line.strip()
                    if isinstance((row := json.loads(line)), dict)][-_MAX_ROWS:]
    except (OSError, ValueError):
        return []


def record_gate_request(root: str, action: str, target: str) -> None:
    """Deduplicate repeated blocked calls without storing arguments or secrets."""
    action, target = str(action or "").strip(), str(target or "").strip()
    if not root or not action or action not in RISK_LEVELS:
        return
    with _LOCK:
        now = time.time()
        rows = _rows(root)
        last = next((row for row in reversed(rows)
                     if row.get("action") == action and row.get("target") == target), None)
        if last and now - float(last.get("created_at_epoch") or 0) < _REPEAT_WINDOW_SECONDS:
            return
        row = {"id": "gate-" + uuid.uuid4().hex[:12], "action": action,
               "target": target[:300], "risk": RISK_LEVELS[action],
               "created_at": datetime.now().isoformat(timespec="seconds"),
               "created_at_epoch": now}
        path = _path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def pending_gate_requests(root: str, decisions: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return latest unresolved request per action/target within the gate TTL."""
    now = time.time()
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _rows(root):
        key = (str(row.get("action") or ""), str(row.get("target") or ""))
        if key[0] and key[1]:
            latest[key] = row
    decided: dict[tuple[str, str], float] = {}
    for row in decisions:
        key = (str(row.get("action") or ""), str(row.get("target") or ""))
        try:
            timestamp = datetime.fromisoformat(str(row.get("time") or "")).timestamp()
        except (TypeError, ValueError):
            continue
        decided[key] = max(timestamp, decided.get(key, 0))
    pending = []
    for key, row in latest.items():
        timestamp = float(row.get("created_at_epoch") or 0)
        if now - timestamp > 1800 or decided.get(key, 0) >= timestamp - 1:
            continue
        pending.append({k: row[k] for k in ("id", "action", "target", "risk", "created_at")})
    return sorted(pending, key=lambda row: (row["risk"] != "L3", row["created_at"]),
                  reverse=False)


__all__ = ["QUEUE_APPROVABLE", "SETTINGS_ONLY", "record_gate_request", "pending_gate_requests"]
