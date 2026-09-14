"""FastAPI 服务：把 Agent 封装为 HTTP 服务（对应简历中"将 Agent 封装为服务"）。

接口：
  POST /api/ingest   上传并摄取文档（PDF/MD/TXT）
  POST /api/chat     问答，SSE 流式返回 Agent 推理过程与最终答案
  GET  /            演示前端页面
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error
import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from agent import Agent
from config import (
    PROVIDERS,
    LLM_PROVIDER,
    LLM_MODEL,
    EMBEDDING_PROVIDER,
    EMBEDDING_MODEL,
    LLM_API_KEY,
    CODE_COLLECTION_NAME,
    CODE_ROOT,
    set_runtime,
    get_runtime,
    save_state,
    load_state,
    edit_confirm_enabled,
    API_TOKEN,
    DOCMIND_CORS_ORIGINS,
    CHAT_IMAGE_MAX_FILES,
    CHAT_IMAGE_MAX_BYTES,
    CHAT_IMAGE_ALLOWED_TYPES,
)
from ingest import ingest_file, ingest_code_directory, load_project_rules
from vectorstore import reset_collection, list_sources, count
from llm import LLMClient
from tools import (
    set_embedding_provider,
    list_pending_edits,
    confirm_edit as apply_pending_edit,
    reject_edit as drop_pending_edit,
    clear_read_files,
    dev_asset_get,
    dev_asset_register,
    dev_capture_bug,
    dev_list_bugs,
    dev_update_bug,
    _run_region_cmd,
)
from regions import (
    init_regions,
    scaffold_region,
    fill_region_exports,
    list_regions,
    load_region_config,
    verify_contracts,
    rebuild_dev_index,
    commit_all,
    commit_region,
    list_changesets,
    rollback_changeset,
    region_git_info,
    propose_regions,
)
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from engine_adapters import skill_for_engine
import gpu_coordinator as gpu
from gpu_coordinator import status as gpu_status, process_environment
from agent_policy import route_for, permission_check, record_permission, approval_allows, apply_approved_external, routing_status, redact_for_cloud, create_external_approval, list_approvals, decide_approval
import secrets_store
_DESKTOP_HOST_HWND = None

import workbench_fs
import mcp_client
import web_export
import unity_graph
from config import PROJECT_WEB_DIR
from scene_runtime import scene_graph, scene_op, runtime_sessions, runtime_clear
from game_workbench import list_tasks, upsert_task, validate_task_scope, task_impact, task_snapshot, verify_task, engine_catalog, engine_scan, engine_inspect, engine_prepare, install_unreal_bridge, engine_config, engine_status, engine_start, engine_stop, engine_logs, engine_verify, engine_embed, engine_detach, engine_focus, engine_resize, engine_place, EMBED_TOP_STRIP, install_runtime_probe, comfy_status, comfy_templates, comfy_template_workflow, comfy_queue, comfy_history, comfy_wait, comfy_watch, comfy_watch_status, comfy_cancel, comfy_import, comfy_import_all, comfy_resource_duplicates, comfy_unused_resources, parse_unreal_diagnostics, scene_tree, set_scene_property, runtime_events, task_revert, validate_data, localization_check, release_check, project_memory, simulate_growth, asset_dependencies, preview_resource, create_placeholder, impact_analysis, generate_test_scene, playtest, performance_sample, approval, approval_status, godot_check_script, godot_addon_status, install_godot_addon, _resolve_engine_executable


@asynccontextmanager
async def _app_lifespan(app):
    # GPU 协调后台线程：显存采样环、TTL 回收/FIFO pump、Ollama 空闲卸载。
    # Ollama 卸载钩子定义在下方（模块级函数，启动时已就绪）；用户在 GPU 面板
    # 保存的空闲卸载秒数/采样间隔从 .docmind_state.json 恢复。
    idle_s = get_runtime("gpu_idle_unload_seconds")
    poll_s = get_runtime("gpu_poll_interval")
    gpu.configure(idle_unload_seconds=idle_s, poll_interval=poll_s)
    gpu.register_hook("ollama", _gpu_ollama_evict_hook)
    gpu.start_background()
    try:
        yield
    finally:
        gpu.stop_background()


app = FastAPI(title="DocMind RAG Agent", lifespan=_app_lifespan)
agent = Agent()

class AgentRouteReq(BaseModel):
    prompt: str = ''
    files: list = []
    requested: str = 'auto'

@app.post('/api/agent/route')
async def agent_route_ep(req: AgentRouteReq):
    return route_for(req.prompt, req.files, req.requested)

@app.post('/api/agent/permission')
async def agent_permission_ep(payload: dict):
    root=_project_root_or_error()
    if not root: return {'ok':False,'allowed':False,'reason':'未配置代码库'}
    approved=bool(payload.get('approved',False))
    if payload.get('approval_id'):
        approved=approval_allows(root, payload.get('approval_id'), payload.get('path',''))
    return record_permission(payload.get('path',''), root, bool(payload.get('allow_external',False)), approved)

@app.get('/api/agent/routing')
async def agent_routing_status_ep():
    return {'ok': True, **routing_status()}

@app.get('/api/agent/secrets')
async def agent_secrets_ep():
    root=_project_root_or_error()
    return {'ok':bool(root),'providers':secrets_store.providers(root) if root else []}

@app.get('/api/agent/approvals')
async def agent_approvals_ep():
    root=_project_root_or_error(); return {'ok':bool(root),'approvals':list_approvals(root) if root else []}

@app.post('/api/agent/approvals')
async def agent_approval_create_ep(payload: dict):
    root=_project_root_or_error()
    if not root: return {'ok':False,'error':'未配置代码库'}
    return {'ok':True,'approval':create_external_approval(root,payload.get('paths',[]),payload.get('summary',''),payload.get('diff',''),payload.get('before',''),payload.get('after',''))}

@app.post('/api/agent/approvals/decide')
async def agent_approval_decide_ep(payload: dict):
    root=_project_root_or_error(); status=payload.get('status','')
    if status not in ('approved','rejected'): return {'ok':False,'error':'status 必须是 approved 或 rejected'}
    row=decide_approval(root,payload.get('id',''),status) if root else None
    return {'ok':bool(row),'approval':row}

@app.get('/api/agent/approvals/{approval_id}')
async def agent_approval_get_ep(approval_id: str):
    root=_project_root_or_error()
    if not root: return {'ok':False,'error':'未配置代码库'}
    row=next((x for x in list_approvals(root) if x.get('id')==approval_id),None)
    return {'ok':bool(row),'approval':row}

@app.post('/api/agent/external-write')
async def agent_external_write_ep(payload: dict):
    root=_project_root_or_error()
    if not root: return {'ok':False,'error':'未配置代码库'}
    return apply_approved_external(root, payload.get('approval_id',''), payload.get('path',''), payload.get('content'))

@app.delete('/api/agent/secrets/{provider}')
async def agent_secret_delete_ep(provider: str):
    root=_project_root_or_error()
    return secrets_store.remove(root, provider) if root else {'ok':False,'error':'未配置代码库'}

@app.get('/api/agent/connectors')
async def agent_connectors_ep():
    """返回可供 Agent 选择的连接器及其启用状态；不自动启动外部进程。"""
    root = _project_root_or_error()
    if not root: return {'ok': False, 'error': '未配置代码库', 'connectors': []}
    try:
        rows = mcp_client.server_configs(root)
        return {'ok': True, 'connectors': [
            {'key': x.get('key'), 'label': x.get('label'), 'transport': x.get('transport'),
             'enabled': bool(x.get('enabled')), 'requires_approval': True,
             'config_error': x.get('config_error')}
            for x in rows
        ]}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'connectors': []}

class DesktopHostReq(BaseModel):
    hwnd: int

class DesktopResizeReq(BaseModel):
    child_hwnd: int
    width: int
    height: int

class EngineEmbedReq(BaseModel):
    """嵌入请求。

    * 给了 x/y/width/height → **引擎视窗模式**：引擎只占工作台里那一块矩形，
      界面照常可用（推荐，前端按 .pb-framewrap 的位置算出来）。
    * 都不给 → **铺满模式**：按宿主客户区铺满，顶部留 ``offset_y`` 像素给工作台顶栏。
    """
    host_hwnd: int = 0
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    offset_y: int = -1
    title_hint: str = ""

class EnginePlaceReq(BaseModel):
    x: int
    y: int
    width: int
    height: int

class EngineFocusReq(BaseModel):
    pass

@app.post('/api/desktop/host')
async def desktop_host_ep(req: DesktopHostReq):
    global _DESKTOP_HOST_HWND
    _DESKTOP_HOST_HWND = int(req.hwnd)
    return {'ok': True, 'host_hwnd': _DESKTOP_HOST_HWND}

@app.get('/api/desktop/host')
async def desktop_host_get_ep():
    """桌面宿主信息。浏览器模式下 host_hwnd 为 null，前端据此降级（不显示"自动嵌入"）。"""
    info = {'ok': True, 'host_hwnd': _DESKTOP_HOST_HWND}
    try:
        from desktop_bridge import (client_rect, dpi_awareness, dpi_of, embedded_children,
                                    is_window)
        info['desktop'] = bool(_DESKTOP_HOST_HWND) and is_window(_DESKTOP_HOST_HWND)
        info['dpi_awareness'] = dpi_awareness()
        if info['desktop']:
            info['client'] = client_rect(_DESKTOP_HOST_HWND)
            info['dpi'] = dpi_of(_DESKTOP_HOST_HWND)
        info['embedded'] = embedded_children()
    except Exception as e:  # noqa: BLE001  桥接层不可用时保持最小响应
        info['desktop'] = False
        info['bridge_error'] = str(e)
    return info

@app.post('/api/desktop/resize')
async def desktop_resize_ep(req: DesktopResizeReq):
    try:
        from desktop_bridge import resize
        return {'ok': bool(resize(req.child_hwnd, req.width, req.height)), 'child_hwnd': req.child_hwnd, 'width': req.width, 'height': req.height}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


@app.middleware("http")
async def optional_api_auth(request: Request, call_next):
    """Enable bearer/token auth only when DOCMIND_API_TOKEN is configured."""
    # 预检(OPTIONS)放行，交给 CORS 中间件处理，避免鉴权在 CORS 之前拦截导致浏览器跨域失败
    if request.method == "OPTIONS":
        return await call_next(request)
    if API_TOKEN and request.url.path.startswith("/api/"):
        supplied = request.headers.get("x-docmind-token", "")
        auth = request.headers.get("authorization", "")
        if supplied != API_TOKEN and auth != f"Bearer {API_TOKEN}":
            return JSONResponse({"ok": False, "error": "需要有效的 DocMind API Token。"}, status_code=401)
    return await call_next(request)


# 外部接入：允许浏览器/前端跨域调用本服务（来源由 DOCMIND_CORS_ORIGINS 控制，默认 *）。
# 必须在 optional_api_auth 之后注册——Starlette 中间件栈"后注册者在最外层"，
# 这样 401 响应也会经过 CORS 中间件并带上 Access-Control-Allow-Origin，
# 跨域网页端才能读到鉴权失败响应体（否则只能拿到不透明的网络错误）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=DOCMIND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 已上传文档来源（basename 集合）。启动时从 chroma 已有元数据回填，
# 用于：1) 上传新文档时清空多轮上下文避免污染；2) 聊天时给 LLM 上下文提示
# （让它知道"里面/这个文档"指什么）；3) 在 UI 状态栏展示。
_INGESTED = set()
for _s in list_sources():
    _INGESTED.add(os.path.basename(_s))


def _project_root_or_error():
    root = get_runtime("code_root") or CODE_ROOT
    return root if root and os.path.isdir(root) else None


class TaskReq(BaseModel):
    id: str = ""
    title: str
    description: str = ""
    region: str = ""
    priority: str = "normal"
    status: str = "open"
    owner: str = ""
    files: list = []
    allowed_paths: list = []
    symbols: list = []
    verification: list = []
    impact_files: list = []
    snapshot: dict = {}
    verification_result: dict = {}


@app.get("/api/tasks")
async def tasks_ep(status: str = ""):
    root = _project_root_or_error()
    return {"ok": bool(root), "tasks": list_tasks(root, status) if root else [], "error": None if root else "未配置代码库"}

@app.get("/api/tasks/{task_id}")
async def task_get_ep(task_id: str):
    root = _project_root_or_error()
    if not root: return JSONResponse({"ok": False, "error": "未配置代码库"}, status_code=400)
    rows = [x for x in list_tasks(root) if str(x.get("id")) == task_id]
    if not rows: return JSONResponse({"ok": False, "error": "任务不存在"}, status_code=404)
    return {"ok": True, "task": rows[0]}


@app.post("/api/tasks")
async def task_upsert_ep(req: TaskReq):
    root = _project_root_or_error()
    if not root: return JSONResponse({"ok": False, "error": "未配置代码库"}, status_code=400)
    fields = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    scope = validate_task_scope(root, fields)
    if not scope["ok"]:
        return JSONResponse({"ok": False, "error": "任务范围校验失败", "scope": scope}, status_code=422)
    if not fields.get("snapshot"):
        fields["snapshot"] = task_snapshot(root, fields)
    return {"ok": True, "task": upsert_task(root, fields), "scope": scope}


@app.post("/api/tasks/validate")
async def task_validate_ep(req: TaskReq):
    root = _project_root_or_error()
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库"}, status_code=400)
    fields = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    return validate_task_scope(root, fields)

@app.post("/api/tasks/impact")
async def task_impact_ep(req: TaskReq):
    root = _project_root_or_error()
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库"}, status_code=400)
    fields = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    scope = validate_task_scope(root, fields)
    if not scope["ok"]:
        return JSONResponse({"ok": False, "scope": scope}, status_code=422)
    return task_impact(root, fields)

@app.post("/api/tasks/snapshot")
async def task_snapshot_ep(req: TaskReq):
    root = _project_root_or_error()
    if not root: return JSONResponse({"ok": False, "error": "未配置代码库"}, status_code=400)
    fields = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    scope = validate_task_scope(root, fields)
    if not scope["ok"]: return JSONResponse({"ok": False, "scope": scope}, status_code=422)
    return {"ok": True, "snapshot": task_snapshot(root, fields)}

@app.post("/api/tasks/verify")
async def task_verify_ep(req: TaskReq):
    root = _project_root_or_error()
    if not root: return JSONResponse({"ok": False, "error": "未配置代码库"}, status_code=400)
    fields = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    scope = validate_task_scope(root, fields)
    if not scope["ok"]: return JSONResponse({"ok": False, "scope": scope}, status_code=422)
    return verify_task(root, fields)

@app.post("/api/tasks/revert")
async def task_revert_ep(req: TaskReq):
    root=_project_root_or_error()
    if not root: return JSONResponse({"ok":False,"error":"未配置代码库"}, status_code=400)
    fields=req.model_dump() if hasattr(req,'model_dump') else req.dict()
    scope=validate_task_scope(root, fields)
    if not scope['ok']: return JSONResponse({"ok":False,"scope":scope}, status_code=422)
    return task_revert(root, fields)


@app.post("/api/tasks/branch")
async def task_branch_ep(req: TaskReq):
    root=_project_root_or_error()
    if not root: return JSONResponse({"ok":False,"error":"未配置代码库"}, status_code=400)
    fields=req.model_dump() if hasattr(req,"model_dump") else req.dict()
    return task_branch(root, fields)

@app.get("/api/validate_data")
async def validate_data_ep():
    root = _project_root_or_error()
    return validate_data(root) if root else {"ok": False, "error": "未配置代码库"}


@app.get("/api/localization_check")
async def localization_ep():
    root = _project_root_or_error()
    return localization_check(root) if root else {"ok": False, "error": "未配置代码库"}


@app.get("/api/release_check")
async def release_ep():
    root = _project_root_or_error()
    return release_check(root) if root else {"ok": False, "error": "未配置代码库"}


class MemoryReq(BaseModel):
    content: str = ""

class CommandReq(BaseModel):
    command: str = ""
    timeout: int = 30
class EngineReq(BaseModel):
    executable: str = "godot"
    scene: str = ""
    engine: str = "godot"
    embed: bool = False
    host_hwnd: int = 0
    # 前端给的"引擎视窗"（宿主客户区物理像素）。给了它引擎只占那一块，工作台 UI 照常可用。
    rect: dict = {}
class ComfyReq(BaseModel):
    url: str = "http://127.0.0.1:8188"
    workflow: dict = {}
class ComfyCancelReq(BaseModel):
    url: str = "http://127.0.0.1:8188"
    prompt_id: str
class GpuOwnerReq(BaseModel):
    owner: str = ""
class GpuConfigureReq(BaseModel):
    idle_unload_seconds: Optional[float] = None
    poll_interval: Optional[float] = None
class ComfyImportReq(BaseModel):
    url: str = "http://127.0.0.1:8188"
    prompt_id: str
    image: dict
    dest_dir: str = "assets/generated"
    images: list = []
class RuntimeEventsReq(BaseModel):
    events: list = []
class ScenePropertyReq(BaseModel):
    path: str
    node: str
    property: str
    value: str
class SceneOpReq(BaseModel):
    """场景画布的受控编辑请求：一个入口覆盖 add/delete/rename/reparent/duplicate/set_props/move/restore。"""
    path: str
    op: str
    node: str = ""
    parent: str = "."
    name: str = ""
    type: str = ""
    properties: dict = {}
    remove: list = []
    order: dict = {}
    groups: list = []
    position: list = []
    lines: list = []
    at: int = 0
    if_mtime: Optional[float] = None
class RuntimeClearReq(BaseModel):
    scope: str = "stored"
class PreviewReq(BaseModel): path: str
class PlaceholderReq(BaseModel): path: str; kind: str = "text"
class ImpactReq(BaseModel): query: str
class TestSceneReq(BaseModel): name: str; region: str = "behaviors"
class ApprovalReq(BaseModel): action: str; user: str; approved: bool = False; target: str = ""; check: bool = False

@app.get("/api/simulate_growth")
async def simulate_ep(levels: int = 50, base: float = 100, growth: float = 1.08): return {"ok": True, "values": simulate_growth(levels,base,growth)}
@app.get("/api/asset_dependencies")
async def asset_deps_ep():
    root=_project_root_or_error(); return {"ok":bool(root),"dependencies":asset_dependencies(root) if root else []}
@app.post("/api/preview_resource")
async def preview_ep(req: PreviewReq):
    root=_project_root_or_error()
    try: return {"ok":True,"resource":preview_resource(root,req.path)}
    except Exception as e: return JSONResponse({"ok":False,"error":str(e)},status_code=400)
@app.post("/api/create_placeholder")
async def placeholder_ep(req: PlaceholderReq):
    root=_project_root_or_error()
    try: return {"ok":True,"resource":create_placeholder(root,req.path,req.kind)}
    except Exception as e: return JSONResponse({"ok":False,"error":str(e)},status_code=400)
@app.post("/api/impact")
async def impact_ep(req: ImpactReq):
    root=_project_root_or_error(); return {"ok":bool(root),"files":impact_analysis(root,req.query) if root else []}
@app.post("/api/test_scene")
async def test_scene_ep(req: TestSceneReq):
    root=_project_root_or_error()
    try:
        paths = generate_test_scene(root, req.name, req.region)
        return {"ok": True, "paths": paths, "path": paths[0] if paths else ""}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
@app.post("/api/playtest")
async def playtest_ep(req: CommandReq):
    root=_project_root_or_error(); return playtest(root,req.command,req.timeout) if root else {"ok":False,"error":"未配置代码库"}
@app.get("/api/engine/status")
async def engine_status_ep():
    root=_project_root_or_error(); return engine_status(root) if root else {"ok":False,"error":"未配置代码库"}
@app.get("/api/engine/catalog")
async def engine_catalog_ep(): return engine_catalog()

@app.get("/api/engine/scan")
async def engine_scan_ep():
    root=_project_root_or_error(); return engine_scan(root) if root else {"ok":False,"error":"未配置代码库"}

@app.get('/api/engine/inspect')
async def engine_inspect_ep(engine: str = ''):
    root=_project_root_or_error(); return engine_inspect(root, engine) if root else {'ok':False,'error':'未配置代码库'}

@app.get('/api/unity/guid-graph')
async def unity_guid_graph_ep():
    """P1-2：Unity .meta GUID 引用图（纯文本静态分析，不启动编辑器）。"""
    root=_project_root_or_error()
    return await run_in_threadpool(unity_graph.build_unity_graph, root) if root else {'ok':False,'error':'未配置代码库'}

@app.post('/api/engine/prepare')
async def engine_prepare_ep(req: EngineReq):
    root=_project_root_or_error(); return engine_prepare(root, req.engine, req.executable) if root else {'ok':False,'error':'未配置代码库'}

class UnrealBridgeReq(BaseModel):
    confirm: bool = False
    force: bool = False

@app.post('/api/engine/unreal-bridge/install')
async def unreal_bridge_install_ep(req: UnrealBridgeReq):
    root = _project_root_or_error()
    if not root: return {'ok': False, 'error': '未配置代码库'}
    if not req.confirm: return JSONResponse({'ok': False, 'error': '安装 Unreal 桥接脚本需要明确确认。'}, status_code=400)
    return install_unreal_bridge(root, req.force)

@app.get('/api/engine/unreal-bridge/status')
async def unreal_bridge_status_ep(url: str = 'http://127.0.0.1:8765'):
    try:
        with urllib.request.urlopen(url.rstrip('/') + '/', timeout=2) as r: data = json.loads(r.read().decode())
        return {'ok': True, 'available': True, 'bridge': data}
    except Exception as e:
        return {'ok': True, 'available': False, 'error': str(e)}

async def _unreal_bridge_get(path: str, url: str):
    try:
        with urllib.request.urlopen(url.rstrip('/') + path, timeout=5) as r:
            return {'ok': True, 'available': True, **json.loads(r.read().decode())}
    except Exception as e:
        return {'ok': True, 'available': False, 'error': str(e)}

@app.get('/api/engine/unreal-bridge/assets')
async def unreal_bridge_assets_ep(url: str = 'http://127.0.0.1:8765'):
    return await _unreal_bridge_get('/assets', url)

@app.get('/api/engine/unreal-bridge/actors')
async def unreal_bridge_actors_ep(url: str = 'http://127.0.0.1:8765'):
    return await _unreal_bridge_get('/actors', url)

# 注意：EngineEmbedReq 只在文件上方定义一次（带 x/y/offset_y 的完整版）。
# 这里曾经又定义了一次窄版本，把上面的覆盖掉——处理器读 req.x 会 AttributeError，
# 而因为当时还有一个重复的旧处理器在生效，这个错被完全掩盖了。

# 注意：/api/engine/embed 只保留下面这一个处理器（支持引擎视窗矩形）。
# 曾经这里还有一个"不支持 rect"的旧版，导致同路径同方法注册两次——FastAPI 先注册的生效，
# 新写的那个变成永远收不到请求的死代码，而且不会有任何报错。tests/test_api_routes.py 守着这条。
@app.get("/api/engine/skill")
async def engine_skill_ep(engine: str = "godot"):
    if engine not in {"godot", "unity", "unreal"}: return JSONResponse({"ok":False,"error":"不支持的引擎。"}, status_code=400)
    return {"ok":True,"engine":engine,"skill":skill_for_engine(engine)}
@app.get("/api/engine/config")
async def engine_config_get_ep():
    root=_project_root_or_error(); return engine_config(root) if root else {"ok":False,"error":"未配置代码库"}
@app.post("/api/engine/config")
async def engine_config_post_ep(req: EngineReq):
    root=_project_root_or_error(); return engine_config(root, req.engine, req.executable) if root else {"ok":False,"error":"未配置代码库"}

@app.post("/api/engine/start")
async def engine_start_ep(req: EngineReq):
    root=_project_root_or_error()
    host = req.host_hwnd or _DESKTOP_HOST_HWND
    rect = req.rect if (req.rect or {}).get('width') and (req.rect or {}).get('height') else None
    return engine_start(root,req.executable,req.scene,host,req.embed,rect) if root else {"ok":False,"error":"未配置代码库"}
@app.post("/api/engine/stop")
async def engine_stop_ep():
    root=_project_root_or_error(); return engine_stop(root) if root else {"ok":False,"error":"未配置代码库"}
@app.post("/api/engine/embed")
async def engine_embed_ep(req: EngineEmbedReq):
    """把已运行的引擎窗口嵌进桌面宿主（引擎视窗模式优先，否则铺满宿主客户区）。"""
    root=_project_root_or_error()
    if not root: return {"ok":False,"error":"未配置代码库"}
    host = req.host_hwnd or _DESKTOP_HOST_HWND
    offset = EMBED_TOP_STRIP if req.offset_y < 0 else req.offset_y
    rect = None
    if req.width > 0 and req.height > 0:
        rect = {'x': req.x, 'y': req.y, 'width': req.width, 'height': req.height}
    return engine_embed(root, host, req.width or None, req.height or None,
                        req.title_hint, offset, rect)

@app.post("/api/engine/place")
async def engine_place_ep(req: EnginePlaceReq):
    """引擎视窗随前端布局变化重新定位（弹窗移动、窗口缩放时调用）。"""
    root=_project_root_or_error()
    if not root: return {"ok":False,"error":"未配置代码库"}
    return engine_place(root, req.x, req.y, req.width, req.height)
@app.post("/api/engine/detach")
async def engine_detach_ep():
    root=_project_root_or_error(); return engine_detach(root) if root else {"ok":False,"error":"未配置代码库"}
@app.post("/api/engine/focus")
async def engine_focus_ep():
    root=_project_root_or_error(); return engine_focus(root) if root else {"ok":False,"error":"未配置代码库"}
@app.post("/api/engine/resize")
async def engine_resize_ep(offset_y: int = -1):
    root=_project_root_or_error()
    if not root: return {"ok":False,"error":"未配置代码库"}
    return engine_resize(root, None if offset_y < 0 else offset_y)
@app.get("/api/engine/logs")
async def engine_logs_ep(limit: int = 200):
    root=_project_root_or_error(); return engine_logs(root, limit) if root else {"ok":False,"error":"未配置代码库"}
@app.post("/api/runtime/probe")
async def runtime_probe_ep():
    root=_project_root_or_error()
    return install_runtime_probe(root) if root else {"ok":False,"error":"未配置代码库"}

@app.post("/api/engine/verify")
async def engine_verify_ep(req: EngineReq):
    root=_project_root_or_error(); return engine_verify(root, req.executable) if root else {"ok":False,"error":"未配置代码库"}

class EngineDiagnosticsReq(BaseModel):
    engine: str = "unreal"
    text: str = ""

@app.post("/api/engine/diagnostics")
async def engine_diagnostics_ep(req: EngineDiagnosticsReq):
    if req.engine.lower() != "unreal":
        return {"ok": False, "error": "当前仅支持 Unreal 诊断解析。"}
    return {"ok": True, "engine": "unreal", "diagnostics": parse_unreal_diagnostics(req.text)}

# ---------------------------------------------------------------- P0：Godot 单文件校验 + godot-ai 插件
class GodotCheckReq(BaseModel):
    path: str
    executable: str = ""
    timeout: int = 120

@app.post("/api/engine/check")
async def godot_check_ep(req: GodotCheckReq):
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    return await run_in_threadpool(godot_check_script, root, req.path, req.executable, req.timeout)

@app.get("/api/engine/addon/status")
async def godot_addon_status_ep():
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    return godot_addon_status(root)

class GodotAddonInstallReq(BaseModel):
    confirm: bool = False
    force: bool = False

@app.post("/api/engine/addon/install")
async def godot_addon_install_ep(req: GodotAddonInstallReq):
    # 安装会改写用户项目目录与 project.godot，必须经前端二次确认
    if not req.confirm:
        return JSONResponse({"ok": False, "error": "安装 godot-ai 插件需要用户明确确认。"}, status_code=400)
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    return await run_in_threadpool(install_godot_addon, root, req.force)

# ---------------------------------------------------------------- P1：Web 导出 + iframe 试玩
def _godot_exe_for(root):
    cfg = engine_config(root)
    if cfg.get("engine", "godot") != "godot":
        return None
    return _resolve_engine_executable("godot", cfg.get("executable", "godot"))

@app.get("/api/engine/web/templates")
async def web_templates_ep():
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    exe = _godot_exe_for(root)
    if not exe: return {"ok": False, "error": "当前引擎不是 Godot 或未找到可执行文件。"}
    return await run_in_threadpool(web_export.templates_status, exe)

class WebTemplateInstallReq(BaseModel):
    confirm: bool = False

@app.post("/api/engine/web/templates/install")
async def web_templates_install_ep(req: WebTemplateInstallReq):
    # 模板包来自官方 GitHub（约数百 MB），下载与磁盘写入需用户明确确认
    if not req.confirm:
        return JSONResponse({"ok": False, "error": "下载 Godot Web 导出模板需要用户明确确认。"}, status_code=400)
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    exe = _godot_exe_for(root)
    if not exe: return {"ok": False, "error": "当前引擎不是 Godot 或未找到可执行文件。"}
    started = await run_in_threadpool(web_export.install_templates_async, exe)
    return {"ok": True, "started": started, "install": web_export.install_state()}

@app.post("/api/engine/web/export")
async def web_export_ep():
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    exe = _godot_exe_for(root)
    if not exe: return {"ok": False, "error": "当前引擎不是 Godot 或未找到可执行文件。"}
    return await run_in_threadpool(web_export.export_web, root, exe, 300)

_COOP_HEADERS = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Embedder-Policy": "require-corp",
    "Cross-Origin-Resource-Policy": "same-origin",
    # 试玩产物文件名固定（index.pck/index.js 无哈希），重新导出后必须重新验证；
    # no-cache 配合 FileResponse 的 ETag：未变走 304，变了立即拉新。
    "Cache-Control": "no-cache",
}

@app.get("/play/{token}")
@app.get("/play/{token}/{file_path:path}")
async def play_file_ep(token: str, file_path: str = "index.html"):
    # 试玩产物服务：token 绑定当前代码库绝对路径，隔离不同项目；带 COOP/COEP 以启用 SharedArrayBuffer
    root = _project_root_or_error()
    if not root or token != web_export.play_token(root):
        return JSONResponse({"ok": False, "error": "未知试玩会话。"}, status_code=404)
    target = web_export.resolve_play_file(root, file_path)
    if not target:
        return JSONResponse({"ok": False, "error": "文件不存在。"}, status_code=404)
    ext = os.path.splitext(target)[1].lower()
    media = web_export.PLAY_MIME.get(ext, "application/octet-stream")
    if ext == ".js" and target.endswith(".worker.js"):
        media = "application/javascript; charset=utf-8"
    return FileResponse(target, media_type=media, headers=_COOP_HEADERS)

# ---------------------------------------------------------------- P0：MCP 服务器（引擎桥）
class McpServerReq(BaseModel):
    key: str
    config: dict

@app.get("/api/mcp/servers")
async def mcp_servers_ep():
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    return {"ok": True, "servers": mcp_client.server_configs(root)}

@app.post("/api/mcp/servers")
async def mcp_server_save_ep(req: McpServerReq):
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    try: return mcp_client.save_server(root, req.key, req.config)
    except mcp_client.MCPError as e: return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

class McpServerKeyReq(BaseModel):
    key: str

@app.post("/api/mcp/servers/remove")
async def mcp_server_remove_ep(req: McpServerKeyReq):
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    try:
        mcp_client.close_server(root, req.key)
        return mcp_client.remove_server(root, req.key)
    except mcp_client.MCPError as e: return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

class McpProbeReq(BaseModel):
    key: str

@app.post("/api/mcp/probe")
async def mcp_probe_ep(req: McpProbeReq):
    # stdio 冷启动（uvx 首次构建环境）可能耗时数分钟，放线程池避免阻塞事件循环
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    try: return await run_in_threadpool(mcp_client.probe_server, root, req.key)
    except mcp_client.MCPError as e: return JSONResponse({"ok": False, "error": str(e)}, status_code=200)

@app.get("/api/mcp/tools")
async def mcp_tools_ep(key: str):
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    try: return await run_in_threadpool(mcp_client.list_tools, root, key)
    except mcp_client.MCPError as e: return JSONResponse({"ok": False, "error": str(e)}, status_code=200)

class McpCallReq(BaseModel):
    key: str
    name: str
    arguments: dict = {}

@app.post("/api/mcp/call")
async def mcp_call_ep(req: McpCallReq):
    root = _project_root_or_error()
    if not root: return {"ok": False, "error": "未配置代码库"}
    try:
        return await run_in_threadpool(mcp_client.call_tool, root, req.key, req.name, req.arguments)
    except mcp_client.MCPError as e: return JSONResponse({"ok": False, "error": str(e)}, status_code=200)

@app.get("/api/comfy/status")
async def comfy_status_ep(url: str = "http://127.0.0.1:8188"):
    return comfy_status(url)
@app.get("/api/comfy/templates")
async def comfy_templates_ep(): return comfy_templates()
@app.get("/api/comfy/templates/{template_id}")
async def comfy_template_ep(template_id: str): return comfy_template_workflow(template_id)
@app.get("/api/gpu/status")
async def gpu_status_ep():
    return {"ok": True, **gpu_status()}
@app.get("/api/gpu/environment")
async def gpu_environment_ep(device_index: int = -1):
    """返回引擎/外部子进程应注入的 GPU 环境变量；device_index<0 表示按当前租约推断。"""
    return {"ok": True, "environment": process_environment(None if device_index < 0 else device_index)}
@app.post("/api/gpu/cancel")
async def gpu_cancel_ep(req: GpuOwnerReq):
    """取消指定 owner 的排队请求（不影响已持有的租约）。"""
    owner = (req.owner or "").strip()
    if not owner:
        return {"ok": False, "error": "缺少 owner"}
    return {"ok": True, "canceled": gpu.cancel_wait(owner)}
@app.post("/api/gpu/force-release")
async def gpu_force_release_ep(req: GpuOwnerReq):
    """强制回收租约：给了 owner 只收它，没给则回收全部。"""
    owner = (req.owner or "").strip() or None
    prev = await run_in_threadpool(gpu.force_release, owner)
    if prev is None:
        return {"ok": False, "error": "没有可回收的租约"}
    return {"ok": True, "released": prev}
@app.post("/api/gpu/configure")
async def gpu_configure_ep(req: GpuConfigureReq):
    """设置 Ollama 空闲自动卸载秒数（0=关闭）与显存采样间隔，并持久化本机偏好。"""
    gpu.configure(idle_unload_seconds=req.idle_unload_seconds,
                  poll_interval=req.poll_interval)
    if req.idle_unload_seconds is not None:
        set_runtime("gpu_idle_unload_seconds", float(req.idle_unload_seconds))
        save_state("gpu_idle_unload_seconds", float(req.idle_unload_seconds))
    if req.poll_interval is not None:
        set_runtime("gpu_poll_interval", float(req.poll_interval))
        save_state("gpu_poll_interval", float(req.poll_interval))
    return {"ok": True, **gpu_status()}
@app.post("/api/comfy/queue")
async def comfy_queue_ep(req: ComfyReq):
    return comfy_queue(req.workflow, req.url)
@app.get("/api/comfy/history/{prompt_id}")
async def comfy_history_ep(prompt_id: str, url: str = "http://127.0.0.1:8188"):
    return comfy_history(prompt_id, url)
@app.get("/api/comfy/wait/{prompt_id}")
async def comfy_wait_ep(prompt_id: str, url: str = "http://127.0.0.1:8188", timeout: int = 120, interval: float = 1.0):
    return comfy_wait(prompt_id, url, timeout, interval)
@app.post("/api/comfy/watch/{prompt_id}")
async def comfy_watch_ep(prompt_id: str, url: str = "http://127.0.0.1:8188", timeout: int = 900, interval: float = 1.0):
    return comfy_watch(prompt_id, url, timeout, interval)
@app.get("/api/comfy/watch/{prompt_id}")
async def comfy_watch_status_ep(prompt_id: str):
    return comfy_watch_status(prompt_id)
@app.post("/api/comfy/cancel")
async def comfy_cancel_ep(req: ComfyCancelReq):
    """中断 ComfyUI 当前生成（/interrupt）并释放该作业的 GPU 租约。"""
    return await run_in_threadpool(comfy_cancel, req.prompt_id, req.url)
@app.post("/api/comfy/import")
async def comfy_import_ep(req: ComfyImportReq):
    root=_project_root_or_error()
    return comfy_import(root, req.prompt_id, req.image, req.url, req.dest_dir) if root else {"ok":False,"error":"未配置代码库"}
@app.post("/api/comfy/import-all")
async def comfy_import_all_ep(req: ComfyImportReq):
    root=_project_root_or_error()
    return comfy_import_all(root, req.prompt_id, req.images, req.url, req.dest_dir) if root else {"ok":False,"error":"未配置代码库"}
@app.get("/api/comfy/resources/duplicates")
async def comfy_duplicates_ep(directory: str = "assets/generated"):
    root = _project_root_or_error()
    return comfy_resource_duplicates(root, directory) if root else {"ok":False,"error":"未配置代码库"}
@app.get("/api/comfy/resources/unused")
async def comfy_unused_ep(directory: str = "assets/generated"):
    root = _project_root_or_error()
    return comfy_unused_resources(root, directory) if root else {"ok":False,"error":"未配置代码库"}
@app.get("/api/fs/scene-tree")
async def scene_tree_ep(path: str):
    root=_project_root_or_error(); return scene_tree(root,path) if root else {"ok":False,"error":"未配置代码库"}
@app.get("/api/runtime/events")
async def runtime_events_get_ep(from_ts: str = "", to_ts: str = "", types: str = "", sources: str = "",
                                keyword: str = "", limit: int = 0, sessions: bool = False):
    """运行时事件检索：支持时间区间、类型/来源多选、关键字与条数上限。"""
    root=_project_root_or_error()
    if not root: return {"ok":False,"error":"未配置代码库"}
    return runtime_events(root, None, from_ts=from_ts or None, to_ts=to_ts or None,
                          types=[x for x in types.split(",") if x],
                          sources=[x for x in sources.split(",") if x],
                          keyword=keyword or None, limit=limit or None, with_sessions=sessions)
@app.get("/api/runtime/sessions")
async def runtime_sessions_ep(gap: int = 120):
    """把运行时事件按时间间隔切成会话（前端时间线的会话分组）。"""
    root=_project_root_or_error()
    return runtime_sessions(root, gap) if root else {"ok":False,"error":"未配置代码库"}
@app.post("/api/runtime/clear")
async def runtime_clear_ep(req: RuntimeClearReq):
    root=_project_root_or_error()
    if not root: return {"ok":False,"error":"未配置代码库"}
    try: return runtime_clear(root, req.scope)
    except workbench_fs.FsError as e: return workbench_fs._err(e)
@app.post("/api/fs/scene-property")
async def scene_property_ep(req: ScenePropertyReq):
    root=_project_root_or_error()
    if not root: return {"ok":False,"error":"未配置代码库"}
    try: return set_scene_property(root, req.path, req.node, req.property, req.value)
    except Exception as e: return JSONResponse({"ok":False,"error":str(e)}, status_code=400)
@app.get("/api/scene/graph")
async def scene_graph_ep(path: str):
    """场景画布图模型：节点 + 外部引用 + 层级/脚本/实例化边 + 写护栏。"""
    root=_project_root_or_error()
    if not root: return {"ok":False,"error":"未配置代码库"}
    try: return scene_graph(root, path)
    except workbench_fs.FsError as e: return workbench_fs._err(e)
    except Exception as e: return JSONResponse({"ok":False,"error":str(e)}, status_code=400)
@app.post("/api/scene/op")
async def scene_op_ep(req: SceneOpReq):
    """场景受控编辑。写前过双层沙箱，写后自检，结构非法自动回滚，并回传 undo 描述。"""
    root=_project_root_or_error()
    if not root: return {"ok":False,"error":"未配置代码库"}
    payload = req.model_dump(exclude={"path", "op"})
    try: return scene_op(root, req.path, req.op, **payload)
    except workbench_fs.FsError as e: return workbench_fs._err(e)
    except Exception as e: return JSONResponse({"ok":False,"error":str(e)}, status_code=400)
@app.post("/api/runtime/events")
async def runtime_events_post_ep(req: RuntimeEventsReq):
    root=_project_root_or_error(); return runtime_events(root, req.events) if root else {"ok":False,"error":"未配置代码库"}
@app.post("/api/performance")
async def performance_ep(req: CommandReq):
    root=_project_root_or_error(); return performance_sample(root,req.command) if root else {"ok":False,"error":"未配置代码库"}
@app.post("/api/approval")
async def approval_ep(req: ApprovalReq):
    root=_project_root_or_error()
    if not root:
        return {"ok":False,"error":"未配置代码库"}
    # check 模式：仅查询是否已审批，不落记录
    if req.check:
        return {"ok":True, **approval_status(root, req.action, req.target)}
    return {"ok":True, "approval": approval(root, req.action, req.user, req.approved, req.target)}


@app.get("/api/memory")
async def memory_get_ep():
    root = _project_root_or_error()
    return {"ok": bool(root), "content": project_memory(root) if root else ""}


@app.put("/api/memory")
async def memory_put_ep(req: MemoryReq):
    root = _project_root_or_error()
    if not root: return JSONResponse({"ok": False, "error": "未配置代码库"}, status_code=400)
    return {"ok": True, "content": project_memory(root, req.content)}


@app.post("/api/ingest")
async def ingest(file: UploadFile = File(...)):
    os.makedirs("./uploads", exist_ok=True)
    path = f"./uploads/{uuid.uuid4().hex}_{file.filename}"
    with open(path, "wb") as f:
        f.write(await file.read())
    try:
        n = ingest_file(path)
        # 上传新文档 = 新话题开始，清空多轮上下文避免旧问答污染当前问题
        agent.history = []
        _INGESTED.add(file.filename)  # 用原始文件名（无 uuid），配合 pretty_source 保证一致
        return {"ok": True, "chunks": n, "file": file.filename, "ingested_files": sorted(_INGESTED)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.post("/api/ingest_code")
async def ingest_code(root: str = Form(...)):
    """代码问答模式：接收一个代码根目录，遍历并索引其中的源码/配置文件到独立代码集合。"""
    if not os.path.isdir(root):
        return JSONResponse({"ok": False, "error": f"目录不存在: {root}"}, status_code=400)
    try:
        # 重新索引前先清空代码集合：本端点语义是"把该目录完整重建索引"，
        # 不 reset 会导致同一切片被重复写入（实测 117 → 234 翻倍）。
        reset_collection(CODE_COLLECTION_NAME)
        n = ingest_code_directory(root)
        abs_root = os.path.abspath(root)
        set_runtime("code_root", abs_root)
        save_state("code_root", abs_root)  # 跨重启记住上次选择，启动时由 config 恢复
        # 读项目规则文件（若有），注入 Agent 系统消息，让分区约定随项目生效
        rules = load_project_rules(abs_root)
        set_runtime("project_rules", rules)
        # 索引代码 = 新话题，清空多轮上下文避免旧问答污染
        agent.history = []
        return {
            "ok": True,
            "chunks": n,
            "code_root": abs_root,
            "code_sources": count(CODE_COLLECTION_NAME),
            "project_rules_loaded": bool(rules),
        }
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


def _read_chat_images(uploads):
    """校验并读取聊天图片，返回 base64 字符串列表（ollama 原生多模态格式）。

    校验：数量上限 / Content-Type 白名单 / 大小上限 / 文件头魔数与类型一致，
    任何一项不通过直接抛 ValueError（端点转成 400 JSON，绝不带着坏数据进 SSE）。
    """
    import base64

    uploads = [u for u in (uploads or []) if u is not None and u.filename]
    if len(uploads) > CHAT_IMAGE_MAX_FILES:
        raise ValueError(f"单条消息最多附带 {CHAT_IMAGE_MAX_FILES} 张图片。")

    # Content-Type 缺失/不可信时按扩展名兜底
    ext_mime = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif",
    }
    # 文件头魔数 -> 允许的 MIME（防止伪造 Content-Type 上传非图片）
    magic_map = (
        (b"\x89PNG", "image/png"),
        (b"\xff\xd8", "image/jpeg"),
        (b"GIF8", "image/gif"),
    )
    b64_list = []
    for up in uploads:
        mime = (up.content_type or "").lower()
        if mime not in CHAT_IMAGE_ALLOWED_TYPES:
            mime = ext_mime.get(os.path.splitext(up.filename)[1].lower(), "")
        if mime not in CHAT_IMAGE_ALLOWED_TYPES:
            raise ValueError(f"不支持的图片类型：{up.filename}（仅支持 PNG/JPEG/WebP/GIF）。")
        raw = up.file.read()
        if len(raw) > CHAT_IMAGE_MAX_BYTES:
            raise ValueError(
                f"图片 {up.filename} 超过 {CHAT_IMAGE_MAX_BYTES // 1024 // 1024}MB 上限。"
            )
        # RIFF....WEBP 单独判断（魔数不在头部连续位置）
        real_mime = ""
        for magic, m in magic_map:
            if raw.startswith(magic):
                real_mime = m
                break
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
            real_mime = "image/webp"
        if not real_mime:
            raise ValueError(f"文件 {up.filename} 不是有效的图片（文件头校验失败）。")
        if real_mime != mime:
            # 声明类型与实际类型不符：以实际魔数为准，但只接受白名单内类型
            if real_mime not in CHAT_IMAGE_ALLOWED_TYPES:
                raise ValueError(f"文件 {up.filename} 类型不受支持。")
        b64_list.append(base64.b64encode(raw).decode("ascii"))
    return b64_list


@app.post("/api/chat")
async def chat(
    question: str = Form(""),
    images: list[UploadFile] = File(default=None),
):
    question = (question or "").strip()
    # 图片必须在进入 SSE 流之前完成读取与校验，错误才能以 400 JSON 返回给前端
    try:
        b64_images = _read_chat_images(images)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    if not question:
        # 纯图片消息：给一个通用分析指令，避免空 prompt
        question = "请分析这张图片的内容。" if b64_images else ""
    if not question:
        return JSONResponse({"ok": False, "error": "问题不能为空。"}, status_code=400)

    # 若当前 provider / 嵌入依赖 Ollama，但服务不可达，提前给出明确引导（避免进入 SSE 后才泛化报错）
    _prov = get_runtime("llm_provider") or LLM_PROVIDER
    _emb = get_runtime("embedding_provider") or EMBEDDING_PROVIDER
    if _prov == "ollama" or _emb == "ollama":
        try:
            # check_ollama 内部是同步 urllib（最坏阻塞 8s），丢到线程池避免卡住事件循环
            _ol = await run_in_threadpool(check_ollama)
        except Exception:  # noqa: BLE001
            _ol = None
        if _ol is not None and not _ol["reachable"]:
            return StreamingResponse(_ollama_down_stream(_ol["guidance"]), media_type="text/event-stream")

    # 给 LLM 一个上下文提示：列出知识库里已有哪些文档，让它知道"里面/这个文档"指什么
    code_root = get_runtime("code_root") or CODE_ROOT
    hints = []
    if _INGESTED:
        names = ", ".join(sorted(_INGESTED))
        hints.append(
            f"知识库中已上传以下文档：{names}。若用户问题涉及这些文档内容，请用 search_knowledge 检索后回答。"
        )
    if code_root:
        hints.append(
            f"代码库已索引，根目录：{code_root}。关于代码/实现/函数/类/配置/报错的问题，"
            f"请用 search_code / read_file / grep 工具。"
        )
    routing = route_for(question)
    selected_agent = agent
    if routing.get('route') == 'cloud' and routing.get('auto_cloud_enabled'):
        cloud_provider = get_runtime('cloud_llm_provider') or os.getenv('AGENT_CLOUD_PROVIDER', 'deepseek')
        if cloud_provider in PROVIDERS and PROVIDERS[cloud_provider].get('api_key_env'):
            key = get_runtime('llm_api_key') or os.getenv(PROVIDERS[cloud_provider]['api_key_env'], '')
            if key:
                selected_agent = Agent(LLMClient(provider=cloud_provider, api_key=key))
            else:
                routing = {**routing, 'route': 'local', 'reason': '云端未配置 API Key，已回退本地'}
    hints.append(f"模型路由建议：{routing['route']}（复杂度 {routing['complexity']}，{routing['reason']}）。若需云端模型，必须使用已配置且可审计的 provider。")
    if hints:
        grounded = "【系统提示】" + " ".join(hints) + f"\n\n用户问题：{question}"
    else:
        grounded = question

    def event_stream():
        # 注意：此处不得再申请 GPU 租约。llm.py 的 _ollama_chat 已以 owner="ollama"
        # 持租约，SSE 层若以别的 owner 再申请，serial 模式不可重入 → 必然自锁报"GPU 正忙"。
        try:
            yield f"data: {json.dumps({'type':'route','route':routing['route'],'complexity':routing['complexity'],'reason':routing['reason']}, ensure_ascii=False)}\n\n"
            cloud_grounded = redact_for_cloud(grounded) if selected_agent is not agent else grounded
            for ev in selected_agent.run(cloud_grounded, stream=True, images=b64_images or None):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            # LLM 崩溃 / Ollama CUDA 错 / 网络中断等：给前端一个明确的错误 final，不要让前端把检索原文当答案。
            err_msg = f"{type(e).__name__}: {e}"
            print(f"[chat] agent.run failed: {err_msg}", flush=True)
            try:
                dev_capture_bug(f"title: Agent 对话异常\nerror: {err_msg}\ntraceback: {__import__('traceback').format_exc()}")
            except Exception:
                pass
            yield f"data: {json.dumps({'type':'final','text':f'模型无响应：{err_msg[:300]}。请到「⚙ 模型设置」换一个能加载的模型再试。'}, ensure_ascii=False)}\n\n"
        yield "data: {\"type\":\"done\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


class EnhancePromptReq(BaseModel):
    prompt: str


def _local_enhance(draft: str) -> str:
    """无可用模型时的确定性兜底：在草稿后追加结构化检索与输出约束。"""
    return (
        f"{draft}\n\n"
        "（本地增强：请基于本地代码库检索后回答——优先用 search_code / read_file / grep 定位相关实现并引用"
        "文件与行号；若涉及报错先定位来源再给修复；输出分点、关键处附代码片段或路径。）"
    )


@app.post("/api/enhance_prompt")
async def enhance_prompt_ep(req: EnhancePromptReq):
    """增强提示词：把用户草稿重写成更清晰、更利于 Agent 检索回答的提示词。
    优先用已配置的 LLM 重写；未配置可用模型或调用失败时，降级为本地规则增强（仍可用）。"""
    draft = (req.prompt or "").strip()
    if not draft:
        return JSONResponse({"ok": False, "error": "请输入要增强的提示词。"}, status_code=400)

    provider = get_runtime("llm_provider") or LLM_PROVIDER
    usable = provider != "mock"
    envk = PROVIDERS.get(provider, {}).get("api_key_env", "")
    if usable and envk:
        key = get_runtime("llm_api_key") or LLM_API_KEY or os.getenv(envk, "")
        usable = bool(key)

    note = ""
    if usable:
        try:
            sys_p = (
                "你是提示词优化助手。用户会给你一段针对「本地代码库 RAG 问答 Agent」的原始提问草稿。"
                "请把这段草稿改写为一条更清晰、更具体、更利于 Agent 检索与回答的提示词。要求：\n"
                "1. 保留用户原意，不要臆造代码库里不存在的文件/函数/路径。\n"
                "2. 明确意图（定位代码 / 解释逻辑 / 诊断报错 / 总结 / 对比 等）。\n"
                "3. 补充约束：涉及的语言、模块、文件类型，以及期望输出形式（代码片段 / 步骤 / 表格）。\n"
                "4. 若草稿过短或含糊，可合理补全上下文，但用「(推测)」标注补全部分。\n"
                "5. 只输出改写后的提示词本身，不要解释、不要任何前缀或引号包裹。"
            )
            enhanced = LLMClient().chat([
                {"role": "system", "content": sys_p},
                {"role": "user", "content": "原始草稿：\n" + draft},
            ], stream=False)
            enhanced = (enhanced or "").strip()
            if enhanced:
                return {"ok": True, "mode": "llm", "enhanced": enhanced}
        except Exception as e:
            note = f"LLM 重写失败（{type(e).__name__}: {str(e)[:160]}），已改用本地规则增强。"
    else:
        note = "未检测到可用模型（mock 模式或未配置 API Key），已用本地规则增强；在 ⚙ 模型设置 配置模型后可获得 LLM 重写。"

    return {"ok": True, "mode": "local", "enhanced": _local_enhance(draft), "note": note}


# ---------------------------------------------------------------- P2：选区 AI（直连快通道）
class SelectionAiReq(BaseModel):
    path: str
    lang: str = "text"
    start_line: int = 0
    end_line: int = 0
    selection: str
    instruction: str = ""
    file_context: str = ""
    mode: str = "rewrite"
    task_id: str = ""
    task_region: str = ""
    allowed_paths: list = []
    verification: list = []
    engine: str = "godot"


SELECTION_MAX_CHARS = 40_000
SELECTION_CTX_MAX_CHARS = 60_000
SELECTION_INSTR_MAX_CHARS = 4_000


def _selection_rewrite_messages(req: SelectionAiReq) -> list[dict]:
    """构造"只输出替换代码"的强约束对话。选区代码是焦点，文件全文仅作上下文参考。"""
    lang = (req.lang or "text").strip() or "text"
    instr = (req.instruction or "").strip() or "在不改变对外行为的前提下优化这段代码、修正明显问题。"
    sys_p = (
        f"你是资深 {lang} 工程师，正在 IDE 中协助用户就地改写选中的代码片段。严格遵守：\n"
        "1. 只输出用于【替换选中片段】的完整代码本身；不要 markdown 代码围栏、不要解释、"
        "不要任何前后缀文字、不要输出选区之外的文件代码。\n"
        "2. 保持该语言与原片段一致的缩进与命名风格；除非用户明确要求，不要改动选区对外暴露的"
        "类名/函数名/签名/公共类型。\n"
        "3. 即使需求牵涉更大范围，也只给出替换该片段所需的代码，不输出任何说明。"
    )
    if req.task_region or req.allowed_paths:
        scope = req.task_region or "未指定"
        allowed = ", ".join(str(x) for x in req.allowed_paths) or scope
        verify = ", ".join(str(x) for x in req.verification) or "保存后执行项目验证"
        sys_p += (f"\n4. 当前任务分区：{scope}；允许路径：{allowed}。"
                  f"不要提出或依赖范围外文件的修改；修改后验证：{verify}。")
    adapter = skill_for_engine(req.engine)
    if adapter:
        sys_p += "\n\n引擎适配 Skill（必须遵守）：\n" + adapter
    loc = f"第 {req.start_line}–{req.end_line} 行" if req.start_line and req.end_line else "行号未知"
    parts = [
        f"文件：{req.path}",
        f"语言：{lang}",
        f"选区位置：{loc}",
    ]
    ctx = (req.file_context or "").strip()
    if ctx:
        parts.append(
            "文件内容参考（仅帮助理解上下文，禁止原样输出选区之外的部分）：\n"
            f"```{lang}\n{ctx}\n```"
        )
    parts.append("选中的代码：\n" + f"```{lang}\n{req.selection}\n```")
    parts.append("改写要求：" + instr)
    return [
        {"role": "system", "content": sys_p},
        {"role": "user", "content": "\n\n".join(parts)},
    ]


@app.post("/api/selection_ai")
async def selection_ai_ep(req: SelectionAiReq):
    """P2 选区"改写"快通道：直连 LLM 单轮流式，强约束只产出可替换的纯代码。
    解释/Review/自由提问仍走 /api/chat（ReAct agent，可检索项目并引用行号）。"""
    if not (req.selection or "").strip():
        return JSONResponse({"ok": False, "error": "选区为空，请先在编辑器中选中代码。"}, status_code=400)
    if len(req.selection) > SELECTION_MAX_CHARS:
        return JSONResponse(
            {"ok": False, "error": f"选区过长（{len(req.selection)} 字符），上限 {SELECTION_MAX_CHARS} 字符。"},
            status_code=413,
        )
    if len(req.file_context or "") > SELECTION_CTX_MAX_CHARS:
        return JSONResponse({"ok": False, "error": "文件上下文过长，请缩小选区后重试。"}, status_code=413)
    if len(req.instruction or "") > SELECTION_INSTR_MAX_CHARS:
        return JSONResponse({"ok": False, "error": "改写指令过长。"}, status_code=413)

    # 选区改写同样必须受任务分区约束，避免模型绕过保存接口修改越权文件。
    if req.task_id:
        root = _project_root_or_error()
        if not root:
            return JSONResponse({"ok": False, "error": "未配置代码库，无法校验任务范围。"}, status_code=400)
        rows = [x for x in list_tasks(root) if str(x.get("id")) == str(req.task_id)]
        if not rows:
            return JSONResponse({"ok": False, "error": "任务不存在。"}, status_code=422)
        task = rows[0]
        scope = validate_task_scope(root, {**task, "files": [req.path]})
        if not scope["ok"]:
            return JSONResponse({"ok": False, "error": "选区文件不在任务范围内。", "scope": scope}, status_code=422)

    # 进入 SSE 前做可用性预检，错误才能以 JSON 直接返回（与 /api/chat 的图片校验同理）
    provider = get_runtime("llm_provider") or LLM_PROVIDER
    if provider == "mock":
        return JSONResponse(
            {"ok": False, "error": "当前为离线演示（mock）模式，未配置可用模型；请到「⚙ 模型设置」选择模型后再用选区 AI。"},
            status_code=409,
        )
    envk = PROVIDERS.get(provider, {}).get("api_key_env", "")
    if envk:
        key = get_runtime("llm_api_key") or LLM_API_KEY or os.getenv(envk, "")
        if not key:
            return JSONResponse(
                {"ok": False, "error": f"当前供应商 {provider} 未配置 API Key；请到「⚙ 模型设置」填写。"},
                status_code=409,
            )
    if provider == "ollama":
        try:
            _ol = await run_in_threadpool(check_ollama)
        except Exception:  # noqa: BLE001
            _ol = None
        if _ol is not None and not _ol["reachable"]:
            return StreamingResponse(_ollama_down_stream(_ol["guidance"]), media_type="text/event-stream")

    req.selection = req.selection.replace("\r\n", "\n").rstrip("\n")
    messages = _selection_rewrite_messages(req)

    def event_stream():
        # 同问答 SSE：llm._ollama_chat 内部已持 owner="ollama" 租约，此处禁止二次申请
        try:
            client = LLMClient()
            acc: list[str] = []
            for tok in client.chat(messages, stream=True, temperature=0.2):
                if not tok:
                    continue
                acc.append(tok)
                yield f"data: {json.dumps({'type': 'token', 'text': tok}, ensure_ascii=False)}\n\n"
            text = "".join(acc).strip()
            yield f"data: {json.dumps({'type': 'final', 'text': text}, ensure_ascii=False)}\n\n"
        except Exception as e:  # noqa: BLE001
            err_msg = f"{type(e).__name__}: {e}"
            print(f"[selection_ai] LLM stream failed: {err_msg}", flush=True)
            yield f"data: {json.dumps({'type': 'final', 'text': f'模型无响应：{err_msg[:300]}。请到「⚙ 模型设置」换一个能加载的模型再试。'}, ensure_ascii=False)}\n\n"
        yield 'data: {"type":"done"}\n\n'

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/favicon.ico")
async def favicon():
    """浏览器默认会去根路径要图标；不接这个路由每个页面都留一条 404 噪音。

    图标取自 web/（Vite 会把 frontend/public/ 原样拷进去，所以打包后也在），
    缺失时返回 204 而不是 404——"没有图标"不该被记成错误。
    """
    path = os.path.join(PROJECT_WEB_DIR, "favicon.ico")
    if os.path.isfile(path):
        return FileResponse(path, media_type="image/x-icon")
    return Response(status_code=204)

@app.get("/")
async def index():
    return FileResponse(os.path.join(PROJECT_WEB_DIR, "index.html"))


@app.get("/workbench")
async def workbench():
    """开发工作台页（Vite 多页构建产物 web/workbench.html，由 frontend/ 工程构建）。

    源码态若尚未执行 npm run build，返回明确提示而不是 500。
    """
    page = os.path.join(PROJECT_WEB_DIR, "workbench.html")
    if not os.path.isfile(page):
        return JSONResponse(
            {"ok": False, "error": "工作台前端未构建：请先在 frontend/ 目录执行 npm install && npm run build。"},
            status_code=404,
        )
    return FileResponse(page)


class ConfigReq(BaseModel):
    provider: str = "mock"
    api_key: str = ""
    model: str = ""
    embedding_provider: str = ""
    edit_confirm: Optional[bool] = None


def _build_time() -> str:
    """返回当前运行二进制（exe）的构建时间，用于核对线上运行的是哪次构建产物。

    打包后的 onedir 中 sys.executable 即 dist/DocMind/DocMind.exe，其修改时间 == 构建时间；
    未打包（源码直接跑）时返回 "dev"。
    """
    try:
        if getattr(sys, "frozen", False) and os.path.exists(sys.executable):
            t = os.path.getmtime(sys.executable)
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    except Exception:
        pass
    return "dev"


@app.get("/api/config")
async def get_config():
    """返回当前生效的模型配置（不回显 key）。"""
    prov = get_runtime("llm_provider") or LLM_PROVIDER
    emb = get_runtime("embedding_provider") or EMBEDDING_PROVIDER
    eff_model = get_runtime("llm_model") or LLM_MODEL or PROVIDERS.get(prov, {}).get("default_model", "")
    has_key = bool(
        get_runtime("llm_api_key")
        or LLM_API_KEY
        or os.getenv("DASHSCOPE_API_KEY", "")
        or os.getenv("DEEPSEEK_API_KEY", "")
    )
    return {
        "llm_provider": prov,
        "llm_model": eff_model,
        "embedding_provider": emb,
        "has_key": has_key,
        "providers": list(PROVIDERS.keys()),
        "embedding_options": ["local", "ollama", "qwen"],
        "ingested_files": sorted(_INGESTED),
        "code_root": get_runtime("code_root") or CODE_ROOT,
        "code_sources": count(CODE_COLLECTION_NAME),
        "project_rules_loaded": bool(get_runtime("project_rules")),
        "edit_confirm": edit_confirm_enabled(),
        "build_time": _build_time(),
        # 同步 urllib 探活放线程池，Ollama 不可达时不阻塞事件循环/拖慢面板打开
        "ollama_status": await run_in_threadpool(check_ollama),
    }


OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")


def _check_ollama_model(model: str, timeout: float = 60.0):
    """探活：让目标模型做一次极短推理（no-stream）。失败返回 (False, 错误信息)。

    超时给 60s：首次切换到大参数模型时 Ollama 需要把模型冷加载进显存（远超 12s），
    旧的 12s 会把健康模型误判为不可切换。
    """
    payload = json.dumps({"model": model, "prompt": "hi", "stream": False}).encode()
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/generate", data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", "replace")
        try:
            j = json.loads(body)
        except Exception:
            return False, f"Ollama 返回非 JSON：{body[:200]}"
        if "error" in j:
            return False, j["error"]
        if not j.get("done"):
            return False, "Ollama 响应未完成"
        return True, ""
    except urllib.error.URLError as e:
        return False, f"无法连接 Ollama：{e.reason}"
    except TimeoutError:
        return False, f"模型 {model} 加载超时（>{timeout:.0f}s），可能显存不足"
    except Exception as e:
        return False, f"探活失败：{type(e).__name__}: {e}"


def _ollama_name_variants(name: str):
    """Ollama 模型名归一化：无 tag 时默认 tag 是 latest。

    /api/tags 返回的是 'bge-m3:latest' 这种带 tag 的名字，而配置里常写 'bge-m3'，
    直接精确比较会把已安装模型误判为缺失。这里对两边都展开成 {原名, latest 补全名} 集合。
    """
    name = (name or "").strip()
    if not name:
        return set()
    out = {name}
    tail = name.rsplit("/", 1)[-1]
    if ":" not in tail:
        out.add(name + ":latest")
    if name.endswith(":latest"):
        out.add(name[: -len(":latest")])
    return out


def check_ollama():
    """探测本机 Ollama 服务可达性、所需模型是否已拉取，并返回用户引导文案。

    设计：即使 Ollama 不可用也绝不抛异常，返回一个结构化状态字典，
    让前端/启动器/聊天接口都能据此给出明确引导，而不是静默失败。
    仅当当前配置确实依赖 Ollama（needed_models 非空）时才生成引导文案，
    避免骚扰使用 mock/local/qwen 等不依赖本机 Ollama 的用户。
    """
    base = OLLAMA_BASE
    prov = get_runtime("llm_provider") or LLM_PROVIDER
    emb = get_runtime("embedding_provider") or EMBEDDING_PROVIDER
    llm_model = (get_runtime("llm_model") or LLM_MODEL
                 or PROVIDERS.get(prov, {}).get("default_model", ""))
    emb_model = EMBEDDING_MODEL
    needed = []
    if prov == "ollama":
        needed.append(llm_model)
    if emb == "ollama":
        needed.append(emb_model)
    needed = [m for m in needed if m]

    reachable = False
    present = []
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=8) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        reachable = True
        present = [m.get("name") for m in data.get("models", []) if isinstance(m, dict)]
    except Exception:  # noqa: BLE001
        reachable = False

    if reachable:
        present_variants = set()
        for p in present:
            present_variants |= _ollama_name_variants(p)
        missing = [m for m in needed if not (_ollama_name_variants(m) & present_variants)]
    else:
        missing = list(needed)

    guidance = ""
    if needed and not reachable:
        pull_lines = "\n".join(f"     ollama pull {m}" for m in needed)
        guidance = (
            "未检测到 Ollama 服务（{base} 无响应），而当前模型配置依赖本机 Ollama。\n"
            "解决步骤：\n"
            "  1) 安装 Ollama：https://ollama.com/download\n"
            "  2) 启动后在终端拉取模型：\n"
            "{pull_lines}\n"
            "  3) 若 Ollama 运行在其它地址，设置环境变量 OLLAMA_BASE=http://<host>:11434 后重启。\n"
            "也可在「⚙ 模型设置」改用 qwen / deepseek（需 API Key），"
            "或把 embedding_provider 设为 local（仅离线占位向量，非语义检索）。"
        ).format(base=base, pull_lines=pull_lines)
    elif missing:
        guidance = (
            "Ollama 已运行，但缺少所需模型：" + "、".join(missing) +
            "。请执行：\n  ollama pull " + "\n  ollama pull ".join(missing)
        )
    return {
        "reachable": reachable,
        "base": base,
        "provider": prov,
        "embedding_provider": emb,
        "needed_models": needed,
        "present_models": present,
        "missing_models": missing,
        "guidance": guidance,
    }


def _ollama_down_stream(guidance: str):
    """Ollama 不可达时，给聊天接口返回一个清晰的引导 final（而非进入 SSE 后才报错）。"""
    msg = "⚠️ 本机 Ollama 服务未运行或不可达，无法调用本地模型。\n\n" + (guidance or "")
    yield f"data: {json.dumps({'type': 'final', 'text': msg}, ensure_ascii=False)}\n\n"
    yield "data: {\"type\":\"done\"}\n\n"


# ---------------------------------------------------------------------------
# 模型驻留（显存占用）开关：查询 /api/ps，用 keep_alive=0 立即卸载、keep_alive
# 预加载。本地大模型（如 22GB 的 qwen3.6:35b）默认会长时间驻留显存，不聊天时
# 也占着 GPU，玩游戏/跑别的 GPU 任务前可一键卸载；下次对话 Ollama 会自动重载。
# ---------------------------------------------------------------------------
def _ollama_ps(timeout: float = 8.0):
    """调用 Ollama /api/ps 列出当前驻留的模型；服务不可达时抛异常。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"{OLLAMA_BASE}/api/ps", timeout=timeout) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return data.get("models") or []


def _ollama_http_post(path: str, payload: dict, timeout: float):
    """POST JSON 到 Ollama；HTTP 4xx/5xx 时把响应体里的 error 文本带回来。"""
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        OLLAMA_BASE + path, data=data, headers={"Content-Type": "application/json"},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as r:
            body = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", "replace")).get("error", "")
        except Exception:  # noqa: BLE001
            pass
        return False, detail or f"HTTP {e.code}", e.code
    if isinstance(body, dict) and body.get("error"):
        return False, str(body["error"]), 200
    return True, "", 200


def _ollama_keep_alive(model: str, keep_alive, timeout: float = 600.0):
    """控制模型驻留：keep_alive=0 立即卸载；如 "30m" 则预加载并驻留。

    - 生成型模型：/api/generate 只带 model + keep_alive、不带 prompt，仅改驻留不推理
      （卸载响应 done_reason="unload"）；
    - 嵌入型模型（bge-m3 等）：不支持 generate（400 "does not support generate"），
      改走 /api/embeddings，用一次极小的嵌入计算完成加载/卸载。
    冷加载 22GB 模型可能要 1-2 分钟，超时给到 600s。
    """
    ok, err, code = _ollama_http_post(
        "/api/generate",
        {"model": model, "keep_alive": keep_alive, "stream": False},
        timeout,
    )
    if ok:
        return True, ""
    if code == 400 and "does not support generate" in err:
        ok2, err2, _ = _ollama_http_post(
            "/api/embeddings",
            {"model": model, "prompt": ".", "keep_alive": keep_alive},
            timeout,
        )
        return ok2, err2
    return False, err


def _gpu_ollama_evict_hook():
    """GPU 协调器驱逐钩子：把所有驻留的 Ollama 模型立即卸载，给新作业腾显存。

    由 gpu_coordinator 在显存门槛拒绝时触发（锁外执行）；Ollama 不在线或没有
    驻留模型都算"没腾出东西"，返回 False。任何异常都吞掉，绝不能拖死协调线程。

    实测多模型同驻（如 qwen3.6:35b 生成模型 + bge-m3 嵌入模型）时，紧接大模型
    卸载后立刻发出的嵌入模型 keep_alive=0 可能被 Ollama 吞掉（HTTP 成功但模型
    仍在 /api/ps 中），因此卸载后短暂等待，用新 /api/ps 对幸存者再补一轮（最多两轮）。
    """
    try:
        pending = [m.get("name") or m.get("model") for m in _ollama_ps()]
        pending = [n for n in pending if n]
    except Exception:  # noqa: BLE001
        return False
    if not pending:
        return False
    freed = False
    for round_no in range(2):
        for name in pending:
            try:
                ok, _err = _ollama_keep_alive(name, 0, timeout=120)
            except Exception:  # noqa: BLE001
                ok = False
            freed = freed or ok
        if round_no == 0:
            time.sleep(0.8)  # 给 Ollama 的异步卸载留处理时间
            try:
                pending = [m.get("name") or m.get("model") for m in _ollama_ps()]
                pending = [n for n in pending if n]
            except Exception:  # noqa: BLE001
                pending = []
            if not pending:
                break
    return freed


def _ollama_expires_minutes(expires_at: str):
    """把 /api/ps 的 expires_at（带纳秒的 ISO 时间）换算成"还有多少分钟到期"。"""
    if not expires_at:
        return None
    try:
        m = re.match(r"^(.*\.\d{1,6})\d*(.*)$", expires_at)  # 纳秒截到微秒，fromisoformat 才认
        dt = datetime.fromisoformat((m.group(1) + m.group(2)) if m else expires_at)
        return max(0, int((dt - datetime.now(dt.tzinfo)).total_seconds() // 60))
    except Exception:  # noqa: BLE001
        return None


def _format_ps_model(m: dict) -> dict:
    """规整 /api/ps 单条模型信息给前端展示。"""
    size_vram = int(m.get("size_vram") or 0)
    size = int(m.get("size") or 0)
    return {
        "name": m.get("name") or m.get("model") or "",
        "size_vram_gb": round(size_vram / 1024 ** 3, 2),
        "size_gb": round(size / 1024 ** 3, 2),
        "expires_minutes": _ollama_expires_minutes(m.get("expires_at") or ""),
        "processor": m.get("processor") or "",
    }


def _ollama_needed_models():
    """当前 DocMind 配置实际会用到的 Ollama 模型（LLM + 检索向量），用于预加载。"""
    prov = get_runtime("llm_provider") or LLM_PROVIDER
    emb = get_runtime("embedding_provider") or EMBEDDING_PROVIDER
    out = []
    if prov == "ollama":
        out.append(get_runtime("llm_model") or LLM_MODEL or PROVIDERS["ollama"]["default_model"])
    if emb == "ollama":
        out.append(EMBEDDING_MODEL or "bge-m3")
    # 去重保序（LLM 与 embedding 理论上不会同名，稳妥起见）
    seen, uniq = set(), []
    for n in out:
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def _preload_keep_alive():
    """预加载时应保持的驻留时长。

    预加载按钮只负责"把模型加载进显存、避免首条消息冷启动"，驻留时长必须
    **跟随服务器默认配置**（OLLAMA_KEEP_ALIVE），不能写死成 30m——否则会把本就
    驻留更久（如 24h）的模型活活砍短，导致用户体感"刚预加载就自动卸载了"。

    读取失败 / 值为 0 或负（"请求结束即卸载"或"无限"等语义不清的值）时，
    回落到一个合理的长驻留（24h），避免预加载反而触发立刻卸载。
    """
    env = (os.getenv("OLLAMA_KEEP_ALIVE") or "").strip().lower()
    if not env or env in ("0", "-1", "inf", "infinite", "none"):
        return "24h"
    return env


class ModelPowerReq(BaseModel):
    action: str = "off"  # off=立即卸载所有驻留模型释放显存；on=预加载当前配置所需模型


@app.get("/api/model_status")
async def model_status_ep():
    """查询 Ollama 当前驻留模型与显存占用，供前端"模型开关"展示。"""
    prov = get_runtime("llm_provider") or LLM_PROVIDER
    emb = get_runtime("embedding_provider") or EMBEDDING_PROVIDER
    try:
        models = await run_in_threadpool(_ollama_ps)
    except Exception as e:  # noqa: BLE001
        return {
            "reachable": False, "loaded": [], "vram_gb": 0,
            "needs_ollama": prov == "ollama" or emb == "ollama",
            "error": f"{type(e).__name__}: {e}",
        }
    loaded = [_format_ps_model(m) for m in models]
    return {
        "reachable": True,
        "loaded": loaded,
        "vram_gb": round(sum(m["size_vram_gb"] for m in loaded), 2),
        "needs_ollama": prov == "ollama" or emb == "ollama",
        "needed_models": _ollama_needed_models(),
    }


@app.post("/api/model_power")
async def model_power_ep(req: ModelPowerReq):
    """模型开关：off 卸载全部驻留模型（释放显存，下次对话自动重载）；on 预加载。"""
    if req.action not in ("off", "on"):
        return JSONResponse({"ok": False, "error": f"未知 action: {req.action}"}, status_code=400)
    try:
        if req.action == "off":
            # 卸载当前所有驻留模型（一台单机一个 Ollama，DocMind 是主要使用方；
            # 只动 /api/ps 里实际驻留的，不会影响未加载的模型）
            resident = await run_in_threadpool(_ollama_ps)
            names = [m.get("name") or m.get("model") for m in resident]
            unloaded, errors = [], []
            for name in [n for n in names if n]:
                ok, err = await run_in_threadpool(_ollama_keep_alive, name, 0)
                (unloaded if ok else errors).append(name if ok else f"{name}（{err}）")
            result = {"ok": not errors, "action": "off", "unloaded": unloaded}
            if errors:
                result["error"] = "部分模型卸载失败：" + "、".join(errors)
            # keep_alive=0 后大模型 runner 释放显存需要几秒（/api/ps 会短暂显示
            # Stopping...），轮询等待最多 15s，让前端拿到的就是"已清空"的终态
            if unloaded:
                deadline = time.time() + 15
                while time.time() < deadline:
                    still = await run_in_threadpool(_ollama_ps)
                    if not still:
                        break
                    await run_in_threadpool(time.sleep, 1)
        else:
            wanted = _ollama_needed_models()
            if not wanted:
                return {
                    "ok": False,
                    "error": "当前 Provider 不是 Ollama（云端/演示模型不占本机显存），无需预加载。",
                }
            loaded, errors = [], []
            for name in wanted:
                ok, err = await run_in_threadpool(_ollama_keep_alive, name, _preload_keep_alive())
                (loaded if ok else errors).append(name if ok else f"{name}（{err}）")
            result = {"ok": not errors, "action": "on", "preloaded": loaded}
            if errors:
                result["error"] = "部分模型预加载失败：" + "、".join(errors)
        # 回读最新驻留状态，前端直接渲染
        models = await run_in_threadpool(_ollama_ps)
        result["loaded"] = [_format_ps_model(m) for m in models]
        result["vram_gb"] = round(sum(m["size_vram_gb"] for m in result["loaded"]), 2)
        return result
    except urllib.error.URLError as e:
        return JSONResponse({"ok": False, "error": f"无法连接 Ollama：{e.reason}"}, status_code=400)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=400)


@app.post("/api/config")
async def set_config(req: ConfigReq):
    """页面内切换模型：更新运行时覆盖、重建 Agent 的 LLM 客户端，即时生效。"""
    if req.provider not in PROVIDERS:
        return JSONResponse({"ok": False, "error": f"未知 provider: {req.provider}"}, status_code=400)

    # 切到 Ollama 时先做模型健康检查：避免选了一个加载不起来的模型后页面卡死、显示原始检索内容
    target_model = req.model or PROVIDERS[req.provider]["default_model"]
    if req.provider == "ollama" and target_model:
        # 真实推理探活最坏等待模型冷加载（60s），必须在线程池执行
        ok, err = await run_in_threadpool(_check_ollama_model, target_model)
        if not ok:
            return {
                "ok": False,
                "model_error": err,
                "llm_provider": get_runtime("llm_provider") or LLM_PROVIDER,
                "llm_model": get_runtime("llm_model") or LLM_MODEL or PROVIDERS[(get_runtime("llm_provider") or LLM_PROVIDER)]["default_model"],
                "embedding_provider": get_runtime("embedding_provider") or EMBEDDING_PROVIDER,
                "ingested_files": sorted(_INGESTED),
            }

    warnings = []
    set_runtime("llm_provider", req.provider)
    if req.model:
        set_runtime("llm_model", req.model)
    if req.api_key:
        set_runtime("llm_api_key", req.api_key)
        root_for_secret = _project_root_or_error()
        if root_for_secret:
            secrets_store.save(root_for_secret, req.provider, req.api_key)

    # 重建 Agent 的 LLM 客户端（即时生效），并清空多轮上下文避免旧回答混淆
    agent.llm = LLMClient(
        provider=req.provider,
        model=req.model or None,
        api_key=req.api_key or None,
    )
    agent.history = []

    # 切换 embedding provider：清空向量缓存 + 重建集合（维度可能变化）
    if req.embedding_provider:
        cur = get_runtime("embedding_provider") or EMBEDDING_PROVIDER
        if req.embedding_provider != cur:
            set_embedding_provider(req.embedding_provider)
            reset_collection()
            warnings.append("已切换检索向量模型，旧文档向量已清空，请重新上传文档以保证检索准确。")

    # 写工具是否「人工确认」：可选开关（None 表示不改动）
    if req.edit_confirm is not None:
        set_runtime("edit_confirm", bool(req.edit_confirm))

    # 需要 key 但未提供
    if req.provider in ("qwen", "deepseek"):
        envk = PROVIDERS[req.provider]["api_key_env"]
        if not (get_runtime("llm_api_key") or LLM_API_KEY or os.getenv(envk, "")):
            warnings.append(f"{req.provider} 需要 API Key，请在设置中填写后保存。")

    prov = get_runtime("llm_provider") or LLM_PROVIDER
    emb = get_runtime("embedding_provider") or EMBEDDING_PROVIDER
    eff_model = get_runtime("llm_model") or LLM_MODEL or PROVIDERS[prov]["default_model"]
    return {
        "ok": True,
        "llm_provider": prov,
        "llm_model": eff_model,
        "embedding_provider": emb,
        "ingested_files": sorted(_INGESTED),
        "warnings": warnings,
    }


@app.post("/api/reset_code")
async def reset_code():
    """清空代码集合并解除代码库配置（重新索引前调用，避免旧切片累积）。"""
    reset_collection(CODE_COLLECTION_NAME)
    set_runtime("code_root", "")
    save_state("code_root", "")  # 同步清除持久化选择，避免重启后又恢复
    set_runtime("project_rules", "")
    clear_read_files()
    agent.history = []
    return {"ok": True, "code_sources": count(CODE_COLLECTION_NAME)}


@app.get("/api/pending_edits")
async def pending_edits():
    return {"pending": list_pending_edits(), "edit_confirm": edit_confirm_enabled()}


# ---------------------------------------------------------------------------
# 分区开发（Region-based Development）：一键初始化分工区域 + 查询分区状态。
# init_regions 建目录 / 每区 git init / 写 DEV_INDEX.md 与 DOCMIND_RULES.md；
# 完成后把分区契约重新注入 Agent 系统提示，让 Agent 自动守约。
# ---------------------------------------------------------------------------
@app.post("/api/init_regions")
async def init_regions_ep():
    ok, msg = init_regions()
    if not ok:
        return JSONResponse({"ok": False, "error": msg}, status_code=400)
    # 把分区契约（DOCMIND_RULES.md）重新注入运行时，使 Agent 立即按分区约束工作
    root = get_runtime("code_root") or CODE_ROOT
    if root:
        rules = load_project_rules(os.path.abspath(root))
        set_runtime("project_rules", rules)
    return {"ok": True, "message": msg, "regions": list_regions()}


@app.get("/api/regions")
async def regions_ep():
    root = get_runtime("code_root") or CODE_ROOT
    return {"code_root": root, "regions": list_regions()}


class CreateRegionReq(BaseModel):
    key: str


@app.post("/api/regions/create")
async def create_region_ep(req: CreateRegionReq):
    """分区可视化一键创建：为缺失分区补建目录 + 声明的导出接口桩/README。"""
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    ok, detail = scaffold_region(root, req.key.strip())
    if not ok:
        return JSONResponse({"ok": False, "error": detail}, status_code=400)
    return {"ok": True, **detail, "regions": list_regions()}


@app.post("/api/regions/fill_exports")
async def fill_exports_ep(req: CreateRegionReq):
    """分区可视化补齐导出桩：为已存在但缺导出文件的分区生成缺失桩文件。"""
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    ok, detail = fill_region_exports(root, req.key.strip())
    if not ok:
        return JSONResponse({"ok": False, "error": detail}, status_code=400)
    return {"ok": True, **detail, "regions": list_regions()}


@app.get("/api/health")
async def health_ep():
    """轻量健康检查：返回 Ollama 可达性/模型就绪状态与引导文案。

    供启动器、外部探针及前端配置面板按需查询（同步 urllib 探活放线程池执行）。
    """
    return await run_in_threadpool(check_ollama)


@app.get("/api/region_git/{region}")
async def region_git_ep(region: str):
    root = get_runtime("code_root") or CODE_ROOT
    if not root: return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    ok, data = region_git_info(root, region)
    return {"ok": ok, **data}


# ---------------------------------------------------------------------------
# Agent 研判分区接口：propose（扫描代码库给建议）/ apply（落地自定义方案）/ add（增补单分区）。
# 默认 8 个分区仅作初始建议，真实分区由 Agent 依据代码库判断后应用。
# ---------------------------------------------------------------------------
class ApplyRegionsReq(BaseModel):
    regions: list


class AddRegionReq(BaseModel):
    key: str
    dir: str = ""
    name: str = ""
    desc: str = ""
    access: str = ""
    depends_on: list = []
    exports: list = []


@app.post("/api/propose_regions")
async def propose_regions_ep():
    """依据真实代码库结构研判分区方案（默认 8 区仅作初始建议）。"""
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    res = propose_regions(root)
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error", "研判失败")}, status_code=400)
    return {"ok": True, **res}


@app.post("/api/apply_regions")
async def apply_regions_ep(req: ApplyRegionsReq):
    """应用 Agent 研判后的自定义分区方案：写入 regions.json 并初始化（建目录/每区 git/规则）。"""
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    ok, msg = init_regions(root, req.regions)
    if not ok:
        if isinstance(msg, dict) and msg.get("blocked"):
            return JSONResponse({"ok": False, **msg}, status_code=403)
        return JSONResponse({"ok": False, "error": msg}, status_code=400)
    # 把新分区契约（DOCMIND_RULES.md）重新注入运行时，使 Agent 立即按新分区约束工作
    rules = load_project_rules(os.path.abspath(root)) if root else ""
    set_runtime("project_rules", rules)
    return {"ok": True, "message": msg, "regions": list_regions()}


@app.post("/api/add_region")
async def add_region_ep(req: AddRegionReq):
    """向现有分区配置追加（或覆盖同名）一个分区，并立即初始化。"""
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    cur = load_region_config(root)
    new_r = {
        "key": req.key,
        "dir": (req.dir or req.key).strip("/\\").replace("\\", "/"),
        "name": req.name or req.key,
        "desc": req.desc,
        "access": req.access,
        "depends_on": req.depends_on or [],
        "exports": req.exports or [],
        "verify": "",
    }
    cur = [r for r in cur if (r.get("key") or "") != req.key]
    cur.append(new_r)
    ok, msg = init_regions(root, cur)
    if not ok:
        if isinstance(msg, dict) and msg.get("blocked"):
            return JSONResponse({"ok": False, **msg}, status_code=403)
        return JSONResponse({"ok": False, "error": msg}, status_code=400)
    rules = load_project_rules(os.path.abspath(root)) if root else ""
    set_runtime("project_rules", rules)
    return {"ok": True, "message": msg, "regions": list_regions()}


# ---------------------------------------------------------------------------
# 分区开发 2.0 接口：契约校验 / 索引重算 / 单区校验 / 跨区提交 / 变更集查询与回滚。
# 这些接口把 tools.py 里的 dev_* 能力以 HTTP 暴露给前端工作台，无需走 Agent。
# ---------------------------------------------------------------------------
@app.get("/api/verify_contracts")
async def verify_contracts_ep():
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    r = verify_contracts(root)
    return {"ok": r["ok"], "errors": r["errors"], "graph": r["graph"]}


@app.post("/api/rebuild_index")
async def rebuild_index_ep():
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    ok, msg = rebuild_dev_index(root)
    return {"ok": ok, "message": msg}


class RegionReq(BaseModel):
    region: str


class RegionCommitReq(BaseModel):
    region: str
    message: str = "docmind: update"


class AssetReq(BaseModel):
    asset_id: str = ""
    path: str = ""
    consumer_region: str = ""
    type: str = ""
    license: str = ""
    tags: str = ""


class BugReq(BaseModel):
    error: str = ""
    exception: str = ""
    traceback: str = ""
    source_region: str = ""
    reproduction: str = ""
    severity: str = "error"
    title: str = ""


class BugStatusReq(BaseModel):
    bug_id: str
    status: str


@app.post("/api/dev_asset_get")
async def dev_asset_get_ep(req: AssetReq):
    fields = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    payload = "\n".join(f"{k}: {v}" for k, v in fields.items() if v)
    result = dev_asset_get(payload)
    try:
        data = json.loads(result)
        return data if isinstance(data, dict) else {"ok": False, "error": result}
    except Exception:
        return JSONResponse({"ok": False, "error": result}, status_code=400)


@app.post("/api/dev_asset_register")
async def dev_asset_register_ep(req: AssetReq):
    fields = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    payload = "\n".join(f"{k}: {v}" for k, v in fields.items() if v)
    result = dev_asset_register(payload)
    try: return json.loads(result)
    except Exception: return JSONResponse({"ok": False, "error": result}, status_code=400)


@app.post("/api/dev_capture_bug")
async def dev_capture_bug_ep(req: BugReq):
    fields = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    payload = "\n".join(f"{k}: {v}" for k, v in fields.items() if v)
    result = dev_capture_bug(payload)
    try:
        data = json.loads(result)
        return data if isinstance(data, dict) else {"ok": False, "error": result}
    except Exception:
        return JSONResponse({"ok": False, "error": result}, status_code=400)


@app.get("/api/bugs")
async def bugs_ep(status: str = "", source_region: str = ""):
    result = dev_list_bugs("")
    try:
        data = json.loads(result)
        bugs = data.get("bugs", [])
        if status: bugs = [b for b in bugs if b.get("status") == status]
        if source_region: bugs = [b for b in bugs if b.get("source_region") == source_region]
        data["bugs"] = bugs
        return data
    except Exception:
        return {"ok": False, "bugs": [], "error": result}


@app.post("/api/bugs/status")
async def bug_status_ep(req: BugStatusReq):
    result = dev_update_bug(f"bug_id: {req.bug_id}\nstatus: {req.status}")
    try: return json.loads(result)
    except Exception: return JSONResponse({"ok": False, "error": result}, status_code=400)


@app.post("/api/region_verify")
async def region_verify_ep(req: RegionReq):
    """校验单个分区：执行其 verify 命令，或检查导出接口是否齐全。"""
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    from regions import get_region_map, run_verify
    rmap = get_region_map(root)
    if req.region not in rmap:
        return JSONResponse({"ok": False, "error": f"未知分区：{req.region}"}, status_code=400)
    region_abs = os.path.join(root, rmap[req.region]["dir"])
    verify_cmd = (rmap[req.region].get("verify") or "").strip()
    if verify_cmd:
        ok, output = run_verify(region_abs, verify_cmd)
        if ok is not None or output is not None:
            return {"ok": ok, "verified_by": "builtin", "command": verify_cmd, "output": output[:2000]}
        output = _run_region_cmd(region_abs, verify_cmd)
        ok = "[exit code 0]" in output
        return {"ok": ok, "verified_by": "command", "command": verify_cmd, "output": output[:2000]}
    exports = rmap[req.region].get("exports") or []
    if not exports:
        return {"ok": True, "verified_by": "none", "output": "未配置 verify 命令，也无导出接口，跳过。"}
    miss = [ex for ex in exports if not os.path.isfile(os.path.join(region_abs, ex))]
    if miss:
        return {"ok": False, "verified_by": "exports", "output": f"导出接口缺失：{', '.join(miss)}"}
    return {"ok": True, "verified_by": "exports", "output": f"导出接口齐全：{', '.join(exports)}"}


class CommitAllReq(BaseModel):
    message: str = "docmind: update"


@app.post("/api/commit_all")
async def commit_all_ep(req: CommitAllReq):
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    ok, info = commit_all(root, req.message)
    if isinstance(info, dict) and info.get("blocked"):
        return JSONResponse({"ok": False, **info}, status_code=403)
    payload = {"ok": ok, "id": info.get("id"), "commits": info.get("commits", {})}
    if info.get("message"):
        payload["message"] = info["message"]
    if info.get("errors"):
        payload["errors"] = info["errors"]
    return payload


class RollbackReq(BaseModel):
    changeset: str


@app.post("/api/rollback_changeset")
async def rollback_changeset_ep(req: RollbackReq):
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    ok, detail = rollback_changeset(root, req.changeset)
    if isinstance(detail, dict) and detail.get("blocked"):
        return JSONResponse({"ok": False, **detail}, status_code=403)
    return {"ok": ok, "detail": detail}


@app.get("/api/changesets")
async def changesets_ep():
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return {"changesets": []}
    return {"changesets": list_changesets(root)}


@app.post("/api/dev_commit")
async def dev_commit_ep(req: RegionCommitReq):
    """提交单个分区的改动（该分区独立 git 仓库内 commit）。"""
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录。"}, status_code=400)
    ok, out = commit_region(root, req.region, req.message)
    if isinstance(out, dict) and out.get("blocked"):
        return JSONResponse({"ok": False, **out}, status_code=403)
    return {"ok": ok, "region": req.region, "output": out}


class PendingId(BaseModel):
    id: str


@app.post("/api/confirm_edit")
async def confirm_edit(req: PendingId):
    ok, msg = apply_pending_edit(req.id)
    return {"ok": ok, "message": msg}


@app.post("/api/reject_edit")
async def reject_edit(req: PendingId):
    removed = drop_pending_edit(req.id)
    return {"ok": removed, "message": "已拒绝并丢弃该修改。" if removed else f"未找到待确认修改 #{req.id}。"}


# 开发工作台文件系统接口（P0）：/api/fs/tree|file|save|create|rename|delete|gitlog
app.include_router(workbench_fs.router)

app.mount("/static", StaticFiles(directory=PROJECT_WEB_DIR), name="static")

# Vite 构建产物（web/assets/*，workbench.html 以 /assets/... 绝对路径引用）。
# 未执行前端构建时该目录不存在，跳过挂载以免源码态启动崩溃。
_ASSETS_DIR = os.path.join(PROJECT_WEB_DIR, "assets")
if os.path.isdir(_ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=_ASSETS_DIR), name="assets")
