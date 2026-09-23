"""GPU process, lease and sampling endpoints."""
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool


class GpuOwnerReq(BaseModel):
    owner: str = ""


class GpuConfigureReq(BaseModel):
    idle_unload_seconds: Optional[float] = None
    poll_interval: Optional[float] = None


class GpuProcessReq(BaseModel):
    pid: int
    owner: str = ""
    gpu: int | None = None
    purpose: str = ""


def build_router(ctx) -> APIRouter:
    router = APIRouter(prefix="/api/gpu", tags=["gpu"])

    @router.get("/status")
    async def status():
        return {"ok": True, **ctx.gpu_status()}

    @router.post("/process/register")
    async def register_process(req: GpuProcessReq):
        return {"ok": True, "process": ctx.gpu.register_process(
            req.pid, req.owner, req.gpu, req.purpose)}

    @router.post("/process/heartbeat")
    async def heartbeat_process(req: GpuProcessReq):
        return {"ok": ctx.gpu.heartbeat_process(req.pid)}

    @router.post("/process/unregister")
    async def unregister_process(req: GpuProcessReq):
        return {"ok": bool(ctx.gpu.unregister_process(req.pid))}

    @router.get("/environment")
    async def environment(device_index: int = -1):
        index = None if device_index < 0 else device_index
        return {"ok": True, "environment": ctx.process_environment(index)}

    @router.post("/cancel")
    async def cancel(req: GpuOwnerReq):
        owner = (req.owner or "").strip()
        if not owner:
            return {"ok": False, "error": "缺少 owner"}
        return {"ok": True, "canceled": ctx.gpu.cancel_wait(owner)}

    @router.post("/force-release")
    async def force_release(req: GpuOwnerReq):
        owner = (req.owner or "").strip() or None
        previous = await run_in_threadpool(ctx.gpu.force_release, owner)
        if previous is None:
            return {"ok": False, "error": "没有可回收的租约"}
        return {"ok": True, "released": previous}

    @router.post("/configure")
    async def configure(req: GpuConfigureReq):
        ctx.gpu.configure(idle_unload_seconds=req.idle_unload_seconds,
                          poll_interval=req.poll_interval)
        if req.idle_unload_seconds is not None:
            value = float(req.idle_unload_seconds)
            ctx.set_runtime("gpu_idle_unload_seconds", value)
            ctx.save_state("gpu_idle_unload_seconds", value)
        if req.poll_interval is not None:
            value = float(req.poll_interval)
            ctx.set_runtime("gpu_poll_interval", value)
            ctx.save_state("gpu_poll_interval", value)
        return {"ok": True, **ctx.gpu_status()}

    return router


__all__ = ["GpuConfigureReq", "GpuOwnerReq", "GpuProcessReq", "build_router"]
