"""Project registry and activation endpoints."""
import os

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ProjectCreateReq(BaseModel):
    root: str = ""
    name: str = ""


class ProjectRenameReq(BaseModel):
    name: str = ""


async def activate_project(ctx, pid: str) -> dict:
    record = ctx.projects.get_project(pid) or {}
    root = record.get("root") or ""
    stopped, warnings = [], []
    if root and os.path.isdir(root):
        ctx.set_runtime("code_root", root)
        try:
            ctx.set_runtime("project_rules", ctx.load_project_rules(root))
        except Exception:
            pass
        stopped, warnings = await ctx._stop_other_engines(root)
    notices = []
    if stopped:
        notices.append("已自动停止其它项目的引擎：" + "、".join(stopped))
    notices.extend(warnings)
    return {"code_root": root, "notice": "；".join(notices)}


def build_router(ctx) -> APIRouter:
    router = APIRouter(prefix="/api/projects", tags=["projects"])

    @router.get("")
    async def list_projects():
        return {"ok": True, "current": ctx.projects.current_project_id(),
                "projects": ctx.projects.list_projects()}

    @router.post("")
    async def create_project(req: ProjectCreateReq):
        root = (req.root or "").strip()
        if not root or not os.path.isdir(root):
            return JSONResponse({"ok": False, "error": "目录不存在：%s" % root}, status_code=400)
        pid = ctx.projects.ensure_project(root, req.name or None)
        ctx.projects.set_current(pid)
        info = await ctx._activate_project(pid)
        return {"ok": True, "project_id": pid,
                "project": ctx.projects.get_project(pid), **info}

    @router.post("/{pid}/activate")
    async def activate(pid: str):
        if not ctx.projects.get_project(pid):
            return JSONResponse({"ok": False, "error": "项目不存在：%s" % pid}, status_code=404)
        ctx.projects.set_current(pid)
        info = await ctx._activate_project(pid)
        return {"ok": True, "project_id": pid, **info}

    @router.patch("/{pid}")
    async def rename(pid: str, req: ProjectRenameReq):
        record = ctx.projects.get_project(pid)
        if not record:
            return JSONResponse({"ok": False, "error": "项目不存在：%s" % pid}, status_code=404)
        name = (req.name or "").strip()
        if not name:
            return JSONResponse({"ok": False, "error": "项目名称不能为空。"}, status_code=400)
        ctx.projects.ensure_project(record["root"], name)
        return {"ok": True, "project_id": pid, "project": ctx.projects.get_project(pid)}

    @router.delete("/{pid}")
    async def delete(pid: str):
        if not ctx.projects.remove_project(pid):
            return JSONResponse({"ok": False, "error": "项目不存在：%s" % pid}, status_code=404)
        return {"ok": True, "project_id": pid, "current": ctx.projects.current_project_id()}

    return router


__all__ = ["ProjectCreateReq", "ProjectRenameReq", "activate_project", "build_router"]
