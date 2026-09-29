"""Disposable project workspace with conflict-checked, review-gated application."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any, Mapping

from .enterprise_sandbox import _copy_project, stage_backend
from .project_checkpoint import _iter_files


class ProjectStageError(RuntimeError):
    pass


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _inventory(root: Path) -> dict[str, str]:
    return {rel: _hash(path) for rel, path in _iter_files(root)}


def create_stage(project_root: str, state_root: str, workflow_id: str) -> dict[str, Any]:
    source = Path(project_root).resolve()
    if not source.is_dir():
        raise ProjectStageError("项目目录不存在")
    if not workflow_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in workflow_id):
        raise ProjectStageError("工作流标识无效")
    base = Path(state_root).resolve() / "project_stages" / workflow_id
    if base.exists():
        raise ProjectStageError("工作流试做目录已存在，拒绝覆盖")
    baseline = base / "baseline"
    workspace = base / "workspace"
    try:
        baseline.mkdir(parents=True)
        workspace.mkdir()
        _copy_project(source, baseline)
        _copy_project(baseline, workspace)
        files = _inventory(baseline)
        if _inventory(workspace) != files:
            raise ProjectStageError("试做目录复制校验失败")
        manifest_path = base / "baseline.json"
        manifest_path.write_text(json.dumps(files, ensure_ascii=False), encoding="utf-8")
    except Exception:
        shutil.rmtree(base, ignore_errors=True)
        raise
    return {"id": workflow_id, "workflow_id": workflow_id,
            "status": "draft", "original_root": str(source),
            "workspace_root": str(workspace), "baseline_root": str(baseline),
            "baseline_dir": str(baseline),
            "manifest_path": str(manifest_path), "file_count": len(files),
            "skipped": [], "process_backend": stage_backend()}


def cleanup_stage(stage: Mapping[str, Any], state_root: str) -> dict[str, Any]:
    """Remove only this workflow's disposable copy, never its original project."""
    workflow_id = str(stage.get("workflow_id") or "")
    if not workflow_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in workflow_id):
        raise ProjectStageError("试做区标识无效")
    base = (Path(state_root).resolve() / "project_stages" / workflow_id).resolve()
    expected = Path(state_root).resolve() / "project_stages"
    if base.parent != expected or Path(str(stage.get("workspace_root") or "")).resolve() != base / "workspace":
        raise ProjectStageError("试做区路径不匹配")
    if base.is_symlink():
        raise ProjectStageError("试做区路径不能是符号链接")
    if base.is_dir():
        shutil.rmtree(base)
    return {"ok": True, "status": "discarded", "workflow_id": workflow_id}


def stage_changes(stage: Mapping[str, Any]) -> list[dict[str, str]]:
    baseline = json.loads(Path(str(stage.get("manifest_path") or "")).read_text(encoding="utf-8"))
    if not isinstance(baseline, dict):
        raise ProjectStageError("试做区基线清单损坏")
    workspace = Path(str(stage.get("workspace_root") or "")).resolve()
    if not workspace.is_dir():
        raise ProjectStageError("试做目录不存在")
    current = _inventory(workspace)
    return [{"path": path,
             "status": "added" if path not in baseline else "deleted" if path not in current else "modified",
             "before_sha256": baseline.get(path, ""), "after_sha256": current.get(path, "")}
            for path in sorted(set(baseline) | set(current))
            if baseline.get(path, "") != current.get(path, "")]


def _safe_target(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not relative or ".." in path.parts:
        raise ProjectStageError("变更路径越界")
    target = root / path
    if target.resolve() != root and root not in target.resolve().parents:
        raise ProjectStageError("变更路径越界")
    current = root
    for component in path.parts:
        current = current / component
        if current.is_symlink():
            raise ProjectStageError("变更路径包含符号链接")
    return target


def apply_stage(stage: Mapping[str, Any], reviewed_changes: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Apply exactly the reviewed bytes if the original project is unchanged."""
    if stage.get("status") != "draft":
        raise ProjectStageError("试做区当前状态不能应用")
    original = Path(str(stage.get("original_root") or "")).resolve()
    workspace = Path(str(stage.get("workspace_root") or "")).resolve()
    if not original.is_dir() or not workspace.is_dir():
        raise ProjectStageError("原项目或试做区不存在")
    actual = stage_changes(stage)
    expected = [{key: str(item.get(key) or "") for key in
                 ("path", "status", "before_sha256", "after_sha256")}
                for item in reviewed_changes]
    if actual != expected:
        raise ProjectStageError("试做区在复核后发生变化，请重新测试并复核")
    for item in actual:
        target = _safe_target(original, item["path"])
        staged = _safe_target(workspace, item["path"])
        before = _hash(target) if target.is_file() else ""
        if before != item["before_sha256"]:
            raise ProjectStageError("原项目文件已变化：" + item["path"])
        if staged.is_file() and _hash(staged) != item["after_sha256"]:
            raise ProjectStageError("试做区文件已变化：" + item["path"])
    backup = Path(str(stage["baseline_root"])).parent / ("apply-backup-" + uuid.uuid4().hex[:8])
    backup.mkdir()
    applied: list[str] = []
    try:
        for item in actual:
            target = _safe_target(original, item["path"])
            staged = _safe_target(workspace, item["path"])
            saved = backup / item["path"]
            if target.is_file():
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(target, saved)
            if item["status"] == "deleted":
                target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                try:
                    shutil.copy2(staged, temporary)
                    os.replace(temporary, target)
                finally:
                    temporary.unlink(missing_ok=True)
            applied.append(item["path"])
    except Exception as exc:
        for relative in reversed(applied):
            target = _safe_target(original, relative)
            saved = backup / relative
            if saved.is_file():
                shutil.copy2(saved, target)
            else:
                target.unlink(missing_ok=True)
        raise ProjectStageError("应用失败，已尝试恢复原项目") from exc
    finally:
        shutil.rmtree(backup, ignore_errors=True)
    return {"ok": True, "status": "applied", "paths": applied}
