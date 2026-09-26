"""Project-level checkpoints for autonomous workflow recovery.

The workflow checkpoint stored by LangGraph describes orchestration state.  This
module stores a bounded copy of project files before an execution wave so a
user can restore the last known baseline after reviewing the result.  The
checkpoint never follows symlinks, leaves the project root, or deletes files
created after the checkpoint.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


_EXCLUDED_DIRS = {
    ".git", ".docmind", ".venv", "node_modules", "dist", "build",
    "Library", "Temp", "Intermediate", "DerivedDataCache", "__pycache__",
}
_EXCLUDED_NAMES = {
    ".env", ".env.local", ".env.production", ".env.development",
    ".docmind_secrets.json", ".docmind_secret.key",
}
_EXCLUDED_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".sqlite", ".sqlite3", ".db")
_MAX_FILES = 800
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _root(path: str | os.PathLike[str]) -> Path:
    value = Path(path).expanduser().resolve()
    if not value.is_dir():
        raise ValueError("项目目录不存在")
    return value


def _inside(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root)
        return True
    except ValueError:
        return False


def _digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _iter_files(root: Path, requested: Iterable[str] = ()):
    seen: set[str] = set()
    explicit = [str(item or "").replace("\\", "/").lstrip("/") for item in requested]
    for rel in explicit:
        if not rel or ".." in rel.split("/"):
            continue
        target = (root / rel).resolve()
        if _inside(root, target) and target.is_file() and not target.is_symlink():
            key = target.relative_to(root).as_posix()
            if not _sensitive_path(target) and key not in seen:
                seen.add(key)
                yield key, target
    for base, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if name not in _EXCLUDED_DIRS]
        base_path = Path(base)
        for name in sorted(names):
            target = base_path / name
            if target.is_symlink() or not target.is_file() or _sensitive_path(target):
                continue
            rel = target.relative_to(root).as_posix()
            if rel not in seen:
                seen.add(rel)
                yield rel, target


def _sensitive_path(path: Path) -> bool:
    name = path.name.lower()
    return (name in _EXCLUDED_NAMES or name.startswith(".env.") or
            name.endswith(_EXCLUDED_SUFFIXES))


def create_checkpoint(project_root: str, storage_root: str, workflow_id: str,
                      files: Iterable[str] = ()) -> dict[str, Any]:
    root = _root(project_root)
    checkpoint_id = "cp-" + uuid.uuid4().hex[:12]
    destination = Path(storage_root).resolve() / "project_checkpoints" / workflow_id / checkpoint_id
    files_dir = destination / "files"
    files_dir.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    total = 0
    for rel, source in _iter_files(root, files):
        try:
            size = source.stat().st_size
            if size > _MAX_FILE_BYTES or total + size > _MAX_TOTAL_BYTES or len(entries) >= _MAX_FILES:
                skipped.append({"path": rel, "reason": "size_or_file_limit"})
                continue
            target = files_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            entries.append({"path": rel, "bytes": size, "sha256": _digest(source)})
            total += size
        except (OSError, ValueError) as exc:
            skipped.append({"path": rel, "reason": type(exc).__name__})
    manifest = {
        "schema": "docmind.project-checkpoint.v1",
        "id": checkpoint_id,
        "workflow_id": str(workflow_id),
        "project_root": str(root),
        "created_at": _now(),
        "file_count": len(entries),
        "bytes": total,
        "files": entries,
        "skipped": skipped,
    }
    (destination / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest


def restore_checkpoint(project_root: str, storage_root: str, workflow_id: str,
                       checkpoint: dict[str, Any]) -> dict[str, Any]:
    root = _root(project_root)
    if str(checkpoint.get("workflow_id") or "") != str(workflow_id):
        raise ValueError("快照不属于当前工作流")
    saved_root = Path(str(checkpoint.get("project_root") or "")).resolve()
    if saved_root != root:
        raise ValueError("快照不属于当前项目")
    checkpoint_id = str(checkpoint.get("id") or "")
    if not checkpoint_id or any(part in checkpoint_id for part in ("/", "\\", "..")):
        raise ValueError("快照标识无效")
    base = Path(storage_root).resolve() / "project_checkpoints" / workflow_id / checkpoint_id
    files_dir = base / "files"
    restored: list[str] = []
    failed: list[dict[str, str]] = []
    for item in checkpoint.get("files") or []:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "").replace("\\", "/").lstrip("/")
        if not rel or ".." in rel.split("/"):
            failed.append({"path": rel, "reason": "invalid_path"})
            continue
        source = (files_dir / rel).resolve()
        target = (root / rel).resolve()
        if not _inside(files_dir, source) or not _inside(root, target) or not source.is_file():
            failed.append({"path": rel, "reason": "snapshot_file_missing"})
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            restored.append(rel)
        except OSError as exc:
            failed.append({"path": rel, "reason": type(exc).__name__})
    return {"ok": not failed, "checkpoint_id": checkpoint_id,
            "restored": restored, "failed": failed,
            "created_at": _now(), "deletes": []}


__all__ = ["create_checkpoint", "restore_checkpoint"]
