"""FastAPI 服务：把 Agent 封装为 HTTP 服务（对应简历中"将 Agent 封装为服务"）。

接口：
  POST /api/ingest   上传并摄取文档（PDF/MD/TXT）
  POST /api/chat     问答，SSE 流式返回 Agent 推理过程与最终答案
  GET  /            演示前端页面
"""
import json
import os
import urllib.request
import urllib.error
import uuid

from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent import Agent
from config import (
    PROVIDERS,
    LLM_PROVIDER,
    LLM_MODEL,
    EMBEDDING_PROVIDER,
    LLM_API_KEY,
    CODE_COLLECTION_NAME,
    CODE_ROOT,
    set_runtime,
    get_runtime,
    edit_confirm_enabled,
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
)
from pydantic import BaseModel

from config import PROJECT_WEB_DIR

app = FastAPI(title="DocMind RAG Agent")
agent = Agent()

# 已上传文档来源（basename 集合）。启动时从 chroma 已有元数据回填，
# 用于：1) 上传新文档时清空多轮上下文避免污染；2) 聊天时给 LLM 上下文提示
# （让它知道"里面/这个文档"指什么）；3) 在 UI 状态栏展示。
_INGESTED = set()
for _s in list_sources():
    _INGESTED.add(os.path.basename(_s))


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
        n = ingest_code_directory(root)
        abs_root = os.path.abspath(root)
        set_runtime("code_root", abs_root)
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


@app.post("/api/chat")
async def chat(question: str = Form(...)):
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
    if hints:
        grounded = "【系统提示】" + " ".join(hints) + f"\n\n用户问题：{question}"
    else:
        grounded = question

    def event_stream():
        try:
            for ev in agent.run(grounded, stream=True):
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            # LLM 崩溃 / Ollama CUDA 错 / 网络中断等：给前端一个明确的错误 final，不要让前端把检索原文当答案。
            err_msg = f"{type(e).__name__}: {e}"
            print(f"[chat] agent.run failed: {err_msg}", flush=True)
            yield f"data: {json.dumps({'type':'final','text':f'模型无响应：{err_msg[:300]}。请到「⚙ 模型设置」换一个能加载的模型再试。'}, ensure_ascii=False)}\n\n"
        yield "data: {\"type\":\"done\"}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/")
async def index():
    return FileResponse(os.path.join(PROJECT_WEB_DIR, "index.html"))


class ConfigReq(BaseModel):
    provider: str = "mock"
    api_key: str = ""
    model: str = ""
    embedding_provider: str = ""
    edit_confirm: Optional[bool] = None


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
        "embedding_options": ["local", "qwen"],
        "ingested_files": sorted(_INGESTED),
        "code_root": get_runtime("code_root") or CODE_ROOT,
        "code_sources": count(CODE_COLLECTION_NAME),
        "project_rules_loaded": bool(get_runtime("project_rules")),
        "edit_confirm": edit_confirm_enabled(),
    }


OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://127.0.0.1:11434")


def _check_ollama_model(model: str, timeout: float = 12.0):
    """探活：让目标模型做一次极短推理（no-stream）。失败返回 (False, 错误信息)。"""
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


@app.post("/api/config")
async def set_config(req: ConfigReq):
    """页面内切换模型：更新运行时覆盖、重建 Agent 的 LLM 客户端，即时生效。"""
    if req.provider not in PROVIDERS:
        return JSONResponse({"ok": False, "error": f"未知 provider: {req.provider}"}, status_code=400)

    # 切到 Ollama 时先做模型健康检查：避免选了一个加载不起来的模型后页面卡死、显示原始检索内容
    target_model = req.model or PROVIDERS[req.provider]["default_model"]
    if req.provider == "ollama" and target_model:
        ok, err = _check_ollama_model(target_model)
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
    set_runtime("project_rules", "")
    clear_read_files()
    agent.history = []
    return {"ok": True, "code_sources": count(CODE_COLLECTION_NAME)}


@app.get("/api/pending_edits")
async def pending_edits():
    return {"pending": list_pending_edits(), "edit_confirm": edit_confirm_enabled()}


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


app.mount("/static", StaticFiles(directory=PROJECT_WEB_DIR), name="static")
