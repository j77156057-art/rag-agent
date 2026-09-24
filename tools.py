"""Agent 工具集：知识库检索 / 素材筛选 / 计算器 / 联网搜索 / 代码执行 / 视频提示词生成。

每个工具含 description（给 LLM 看的说明）与 func（实际执行函数）。
新增工具：在 TOOLS 字典里追加一项即可，Agent 会自动识别。
"""
import ast
import base64
import contextvars
import io
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import threading
import urllib.request
import urllib.parse
import datetime
import hashlib
import time
import mcp_client
import mcp_capabilities
from artifact_tools import create_artifact

from config import (TOP_K, COLLECTION_NAME, CODE_COLLECTION_NAME, CODE_ROOT, get_runtime, set_runtime,
                     edit_confirm_enabled, external_access_high, EXTERNAL_API_ALLOWLIST,
                     get_web_search_provider, get_web_search_api_key, get_web_search_api_url,
                     get_web_fetch_provider, get_web_fetch_api_key, get_web_fetch_api_url)
from config import STATE_ROOT
from embeddings import EmbeddingClient
from vectorstore import pretty_source
from agent_runtime.retrieval import get_retriever
from ingest import _CODE_EXT, _SKIP_DIRS
from agent_runtime.tools import ToolResult, ToolSpec, coerce_tool_spec, upgrade_registry

_emb = None

# 联网搜索的轻量缓存只保存公开搜索摘要，不保存网页正文或 API 密钥。
# 缓存文件放在运行时状态根，最多 64 项、默认 10 分钟，写入失败不影响搜索。
_WEB_CACHE_FILE = os.path.join(STATE_ROOT, ".docmind_web_search_cache.json")
_WEB_CACHE_TTL = int(os.getenv("DOCMIND_WEB_CACHE_TTL", "600"))
_WEB_CACHE_MAX = 64
_WEB_CACHE_LOCK = threading.RLock()


# ---------------------------------------------------------------------------
# start_workflow 依赖注入
#
# 工具只负责解析参数/渲染指引；真正「组装项目、策略、LLM 回调并落工作流状态」
# 的 launcher 由 api_routes 在 build_router 时注册（与 WORKFLOWS 的执行回调
# 同风格）。tools 层不 import api_routes，避免循环依赖。
# ---------------------------------------------------------------------------
_WORKFLOW_LAUNCHER = None


def set_workflow_launcher(fn):
    """注册对话内发起工作流的 launcher：fn(goal, *, kind, web_enabled) -> 工作流 public dict。"""
    global _WORKFLOW_LAUNCHER
    _WORKFLOW_LAUNCHER = fn if callable(fn) else None


# 对话 SSE 联动：start_workflow 成功后把有界摘要投递到「当前 chat 流」的持有者，
# api.py 的 chat 流每轮工具执行后 take_pending_workflow(holder) 取走（取走即清），
# 向前端补发结构化 workflow 事件，由对话内工作流卡片直接接管，无需独立面板。
#
# 为什么不用 contextvars：chat 是同步生成器，Starlette/anyio 在线程池里每次 next()
# 都会 copy_context，工具线程里的 set 既可能对下一次迭代不可见、清除也可能不生效
# （实测会重复补发）。模块级活动流栈是跨线程共享的真实对象，append/pop 确定生效；
# 单机单用户桌面场景下并发发起工作流的竞态可忽略，工具固定投递到栈顶（最近注册）流。
_pending_streams: list = []
_orphan_pending: list = []


def push_pending_stream() -> list:
    """chat SSE 流开始时注册自己的待取队列，返回该队列持有者。"""
    holder: list = []
    _pending_streams.append(holder)
    return holder


def pop_pending_stream(holder) -> None:
    """chat SSE 流结束时注销（finally 调用，残留条目转交兜底队列）。"""
    try:
        _pending_streams.remove(holder)
    except ValueError:
        pass
    if holder:
        _orphan_pending.extend(holder[-1:] if len(holder) > 1 else holder)


def _notify_workflow_started(workflow: dict) -> None:
    try:
        summary = {
            "workflow_id": str(workflow.get("workflow_id") or ""),
            "status": str(workflow.get("status") or "awaiting_choice"),
            "phase": str(workflow.get("phase") or "clarify"),
            "kind": str(workflow.get("kind") or "generic"),
            "options_count": len(workflow.get("options") or []),
        }
        (_pending_streams[-1] if _pending_streams else _orphan_pending).append(summary)
    except Exception:
        pass


def take_pending_workflow(holder=None):
    """取走并清除指定流（或兜底队列）内待通知的工作流摘要（无则 None）。"""
    src = holder if holder is not None else _orphan_pending
    try:
        return src.pop(0)
    except (IndexError, TypeError):
        return None


# 本轮会话联网开关：Agent.run 开始时注入。start_workflow 未显式给 web 时缺省继承，
# 保证「对话里开了联网 → 工作流方案也能联网补资料」。
_session_web_enabled = contextvars.ContextVar(
    "docmind_session_web_enabled", default=False)


def set_session_web_enabled(enabled):
    """在当前 context 内设置会话联网缺省值，返回 reset token。"""
    return _session_web_enabled.set(bool(enabled))


# 本轮会话当前模型的视觉能力模式（native/unknown/none...）。云端按请求覆盖
# llm 时全局 runtime/环境变量反映不出该模型能力，web 抓图门据此与本轮模型对齐。
_session_vision_mode = contextvars.ContextVar(
    "docmind_session_vision_mode", default=None)


def set_session_vision_mode(mode):
    """在当前 context 内设置本轮模型的视觉能力画像，返回 reset token。"""
    return _session_vision_mode.set(mode or None)


def _web_cache_key(prefix, query):
    return f"{prefix}:{hashlib.sha256((query or '').encode('utf-8')).hexdigest()}"


def _web_cache_read(key):
    if os.getenv("DOCMIND_WEB_CACHE", "1").strip().lower() in ("0", "false", "no"):
        return None
    with _WEB_CACHE_LOCK:
        try:
            with open(_WEB_CACHE_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            item = data.get(key) if isinstance(data, dict) else None
            if not isinstance(item, dict) or time.time() - float(item.get("time", 0)) > _WEB_CACHE_TTL:
                return None
            return item.get("value")
        except (OSError, ValueError, TypeError):
            return None


def _web_cache_write(key, value):
    if os.getenv("DOCMIND_WEB_CACHE", "1").strip().lower() in ("0", "false", "no"):
        return
    with _WEB_CACHE_LOCK:
        try:
            os.makedirs(os.path.dirname(_WEB_CACHE_FILE), exist_ok=True)
            try:
                with open(_WEB_CACHE_FILE, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data[key] = {"time": time.time(), "value": value}
            fresh = sorted(data.items(), key=lambda kv: float(kv[1].get("time", 0)), reverse=True)[:_WEB_CACHE_MAX]
            temporary = _WEB_CACHE_FILE + ".tmp"
            with open(temporary, "w", encoding="utf-8") as fh:
                json.dump(dict(fresh), fh, ensure_ascii=False)
            os.replace(temporary, _WEB_CACHE_FILE)
        except (OSError, ValueError, TypeError):
            return


def clear_web_search_cache():
    """清空摘要缓存；设置页或测试可调用，正文缓存从不落盘。"""
    with _WEB_CACHE_LOCK:
        try:
            os.unlink(_WEB_CACHE_FILE)
        except FileNotFoundError:
            pass


def _source_score(url, title="", snippet=""):
    """给检索结果一个可解释的 0~1 分数，不把分数当作事实正确性。"""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
    except Exception:
        host = ""
    score, reasons = 0.45, []
    if url.lower().startswith("https://"):
        score += .08; reasons.append("HTTPS")
    if host.endswith("github.com") or host.endswith("bilibili.com"):
        score += .18; reasons.append("平台原站")
    if any(x in host for x in ("docs.", "developer.", "dev.", "learn.")):
        score += .12; reasons.append("文档域名")
    if any(x in (title + " " + snippet).lower() for x in ("official", "官方", "documentation", "文档")):
        score += .08; reasons.append("标题含官方/文档")
    return round(min(score, .95), 2), "、".join(reasons) or "通用网页来源"


def _format_search_result(title, snippet, url, *, source="web"):
    score, reason = _source_score(url, title, snippet)
    return f"· {title}\n  {snippet[:240]}\n  {url}\n  可信度参考：{score:.2f}（{reason}；仅供排序，需核对正文）"


def _search_cache_enabled():
    # 单测会反复使用相同关键词并替换后端；缓存不能遮住这些调用。
    spec = getattr(sys.modules.get("__main__"), "__spec__", None)
    if getattr(spec, "name", None) == "unittest.__main__" or os.getenv("PYTEST_CURRENT_TEST"):
        return False
    return os.getenv("DOCMIND_WEB_CACHE", "1").strip().lower() not in ("0", "false", "no")


def _get_emb():
    global _emb
    if _emb is None:
        _emb = EmbeddingClient()
    return _emb

def dev_mcp_call(arg):
    """MCP 调用外层审计：允许用户在连接器边界插入断点/拦截。"""
    try:
        import hooks as _workflow_hooks
        before = _workflow_hooks.run_workflow("before_mcp", {
            "argument_chars": len(str(arg or "")),
        })
        if before.get("blocked"):
            return "MCP 调用已被钩子拦截：" + str(before.get("reason") or "需要人工审核")
    except Exception:
        _workflow_hooks = None
    result = _dev_mcp_call_impl(arg)
    if _workflow_hooks is not None:
        try:
            _workflow_hooks.run_workflow("after_mcp", {
                "ok": not str(result or "").startswith("MCP 调用失败"),
                "result_chars": len(str(result or "")),
            })
        except Exception:
            pass
    return result


def _dev_mcp_call_impl(arg):
    """Agent 受控调用已启用 MCP 连接器。输入 key/name/arguments(JSON)。"""
    root = get_runtime('code_root') or CODE_ROOT
    if not root: return 'MCP 调用失败：未配置代码库。'
    lines = str(arg or '').splitlines(); data={}; body=[]
    for line in lines:
        if ':' in line and not body:
            k,v=line.split(':',1); data[k.strip()]=v.strip()
        else: body.append(line)
    key=data.get('key',''); name=data.get('name',''); task_id=data.get('task_id','')
    if not key or not name: return 'MCP 调用失败：需要 key 和 name。'
    try:
        cfg=mcp_client.get_server_config(root,key)
        if not cfg.get('enabled'): return 'MCP 调用失败：连接器未启用，请先在工作台启用并审批。可调用 dev_route_connector 查看其它已启用连接器。'
        if task_id:
            from game_workbench import list_tasks
            task=next((t for t in list_tasks(root) if str(t.get('id'))==str(task_id)),None)
            if not task:
                return 'MCP 调用失败：task_id 不存在。'
            raw_args=data.get('arguments','{}'); check_args=raw_args
            if isinstance(raw_args,str):
                try: check_args=json.loads(raw_args) if raw_args else {}
                except Exception: check_args={}
            paths=[]
            if isinstance(check_args,dict):
                for k,v in check_args.items():
                    if k.lower() in ('path','file','scene','asset','script') and isinstance(v,str): paths.append(v.replace('res://','').lstrip('/'))
            if paths:
                from game_workbench import validate_task_scope
                scope=validate_task_scope(root,{**task,'files':paths})
                if not scope.get('ok'): return 'MCP 调用失败：参数路径超出任务分区。'
        args=data.get('arguments','{}')
        if isinstance(args,str): args=json.loads(args) if args else {}
        fallback = data.get('fallback_keys') or data.get('fallback') or []
        if isinstance(fallback, str):
            fallback = [item.strip() for item in fallback.split(',') if item.strip()]
        hint = data.get('hint') or data.get('task_hint') or ''
        side_effect = str(data.get('side_effect', '')).strip().lower() in ('1', 'true', 'yes', 'on')
        allow_side_effect_fallback = str(data.get('allow_side_effect_fallback', '')).strip().lower() in ('1', 'true', 'yes', 'on')
        try:
            result = mcp_client.call_tool_with_fallback(
                root, key, name, args, fallback_keys=fallback,
                task_hint=hint, timeout=mcp_client.CALL_TIMEOUT,
                side_effect=side_effect,
                allow_side_effect_fallback=allow_side_effect_fallback)
        except mcp_client.MCPError as exc:
            attempts = getattr(exc, 'attempts', [])
            suffix = (' 尝试记录：' + json.dumps(attempts, ensure_ascii=False)) if attempts else ''
            return f'MCP 调用失败：{exc}{suffix}（可改用内置工具或人工检查连接器）'
        return json.dumps(result, ensure_ascii=False)[:6000]
    except Exception as e: return f'MCP 调用失败：{e}（若怀疑是连接器选择有误，可先调用 dev_route_connector 重新挑选已启用连接器）'


def dev_list_connectors(arg):
    """列出已配置 MCP 连接器（key/label/engine/transport/启用状态/能力标签/适用说明），供 Agent 自主挑选。输入留空。"""
    root = get_runtime('code_root') or CODE_ROOT
    if not root: return '连接器列表失败：未配置代码库。'
    try:
        rows = mcp_client.connector_directory(root)
        return json.dumps({"ok": True, "connectors": rows}, ensure_ascii=False)
    except Exception as e:
        return f'连接器列表失败：{e}'


def dev_route_connector(arg):
    """按任务语义挑选最合适的【已启用】MCP 连接器。输入 hint（任务描述，如 'Godot 里打开 Main 场景并运行'）。

    返回排序候选与匹配理由；Agent 应取 top.key 作为 dev_mcp_call 的 key。无已启用连接器匹配时，
    提示改用具内工具（search_code/apply_edit/python_exec）或先在工作台启用对应引擎连接器。
    """
    root = get_runtime('code_root') or CODE_ROOT
    if not root: return '连接器路由失败：未配置代码库。'
    hint = str(arg or '').strip().splitlines()[0] if str(arg or '').strip() else ''
    try:
        ranked = mcp_client.select_connector(root, hint)
        if not ranked:
            return json.dumps({"ok": True, "hint": hint, "matches": [],
                               "message": "没有已启用的连接器匹配该任务；可改用内置工具（search_code/apply_edit/python_exec 等），或先在工作台启用对应引擎连接器。"}, ensure_ascii=False)
        return json.dumps({"ok": True, "hint": hint, "top": ranked[0]["key"], "matches": ranked}, ensure_ascii=False)
    except Exception as e:
        return f'连接器路由失败：{e}'


def dev_list_connector_tools(arg):
    """列出某连接器暴露的工具（name/description/input_schema），确定 dev_mcp_call 的 name 与参数。

    输入 key: <连接器key>。仅对打算调用的连接器使用（godot 等 stdio 需先建立会话，引擎未开会失败）。
    """
    root = get_runtime('code_root') or CODE_ROOT
    if not root: return '工具清单失败：未配置代码库。'
    key = str(arg or '').strip()
    if key.startswith('key:'):
        key = key[len('key:'):].strip()
    if not key: return '工具清单失败：需要 key（key: <连接器key>）。'
    try:
        r = mcp_client.list_tools(root, key)
        return json.dumps(r, ensure_ascii=False)[:6000]
    except Exception as e:
        return f'工具清单失败：{e}（连接器可能未启用或引擎未运行，可先用 dev_route_connector 换一个）'


# ---------------------------------------------------------------------------
# MCP 连接器自助装配：搜索目录 → 审批后添加 → 探活 → 发现能力 → 审批路由
#
# 与设置页 UI 走同一套后端（mcp_capabilities / mcp_client），不另造逻辑。
# 两道人工可审计的审批门（game_workbench 审批台账，30 分钟有效）：
#   1) add/remove 连接器（action=mcp_server）：stdio 的 command 等于可执行任意命令，
#      审批 target 绑定 key+command+args+url 的哈希，换命令必须重新审批；
#   2) 批准能力路由（action=mcp_capability）：发现只生成 pending 候选，批准后才进路由器。
# ---------------------------------------------------------------------------

def _mcp_project_root():
    return get_runtime('code_root') or CODE_ROOT


def _mcp_server_approval_target(key, cfg):
    """把 add 审批绑定到具体连接参数，防止同 key 审批被换成别的 command/url 复用。"""
    raw = json.dumps({
        "transport": cfg.get("transport"),
        "command": (cfg.get("command") or "").strip(),
        "args": [str(a) for a in (cfg.get("args") or [])],
        "url": (cfg.get("url") or "").strip(),
    }, ensure_ascii=False, sort_keys=True)
    digest = hashlib.sha1((key.strip().lower() + "|" + raw).encode("utf-8")).hexdigest()[:10]
    return f"{key.strip()}:{digest}"


def _mcp_gate_blocked(root, action, target):
    """审批门未通过时返回统一阻断结构（None=放行）。"""
    from game_workbench import require_approval
    return require_approval(root, action, target)


def dev_mcp_search(arg):
    """搜索 MCP 连接方式的【离线安全目录】：按能力关键词（如 kicad / pcb / 数据库 / github）
    返回匹配条目的能力清单、连接选项、安装步骤、可直接回填的连接模板。

    输入：能力关键词（至少 2 个字符）。本工具不联网；需要最新第三方 MCP 时，在联网开启下
    另行使用 web_search（如 '<软件名> MCP server uvx github'）与 web_fetch 读官方文档，
    确认官方 command/URL 后再调用 dev_mcp_add——严禁把搜索摘要里未经验证的命令直接装配。

    返回 JSON：ok / results[]（id,label,summary,capabilities,connection_options,
    setup_steps,template{key,label,transport,command,args,url},source_status）。
    注意 template.command 可能为空（离线指引类条目），此时必须以官方文档补全命令后再 add。
    """
    root = _mcp_project_root()
    if not root:
        return 'MCP 搜索失败：未配置代码库。'
    query = str(arg or '').strip()
    if query.lower().startswith("query:"):
        query = query[6:].strip()
    try:
        result = mcp_capabilities.search_directory(query, web_enabled=False)
    except Exception as e:  # noqa: BLE001
        return f'MCP 搜索失败：{type(e).__name__}: {e}'
    if not result.get("ok"):
        return f'MCP 搜索失败：{result.get("error") or "未知错误"}'
    items = []
    for item in result.get("results") or []:
        items.append({
            "id": item.get("id"),
            "label": item.get("label"),
            "summary": item.get("summary"),
            "capabilities": item.get("capabilities") or [],
            "connection_options": item.get("connection_options") or [],
            "setup_steps": item.get("setup_steps") or [],
            "template": item.get("template") or {},
            "source_status": item.get("source_status"),
        })
    return json.dumps({"ok": True, "query": query, "results": items},
                      ensure_ascii=False)[:6000]


def dev_mcp_add(arg):
    """装配（新增/更新）一个 MCP 连接器配置。敏感操作：必须先通过 mcp_server 审批。

    输入（多行 key: value）：
      key: <连接器标识，仅字母数字/_/-，≤40>
      label: <显示名，可选>
      transport: <stdio|http，默认 stdio>
      # stdio 必填：
      command: <启动命令，如 uvx>
      args: <空格分隔的参数，如 kicad-mcp-pro --transport stdio>
      args_json: <可选，JSON 数组，优先于 args>
      # http 必填：
      url: <http(s)://.../mcp>
      enabled: <true|false，默认 true>

    首次调用未审批时返回 blocked/approval_required，其中含 action 与 target；
    先调用 dev_approve(action: mcp_server, target: <阻断结构里给出的完整 target>)，
    再用【完全相同的参数】重试本工具。target 已绑定命令/URL，改参数需重新审批。
    成功返回 {ok, server:{key,transport,enabled,...}}，随后应调用 dev_mcp_probe 探活。
    """
    root = _mcp_project_root()
    if not root:
        return json.dumps({"ok": False, "error": "未配置代码库"}, ensure_ascii=False)
    f = _parse_keyed(str(arg or ""), ["key", "label", "transport", "command",
                                      "args", "args_json", "url", "enabled"])
    key = (f.get("key") or "").strip()
    if not key:
        return json.dumps({"ok": False, "error": "缺少 key（连接器标识）。"}, ensure_ascii=False)
    if not all(ch.isalnum() or ch in "_-" for ch in key) or len(key) > 40:
        return json.dumps({"ok": False,
                           "error": "key 仅允许字母数字、下划线、连字符（≤40）。"}, ensure_ascii=False)
    transport = (f.get("transport") or "stdio").strip().lower()
    if transport not in ("stdio", "http"):
        return json.dumps({"ok": False, "error": "transport 仅支持 stdio 或 http。"}, ensure_ascii=False)
    cfg = {"label": (f.get("label") or "").strip() or key}
    if transport == "stdio":
        command = (f.get("command") or "").strip()
        if not command:
            return json.dumps({"ok": False,
                               "error": "stdio 连接器缺少 command；请先从官方文档确认启动命令。"},
                              ensure_ascii=False)
        cfg["command"] = command
        raw_args = (f.get("args_json") or "").strip()
        if raw_args:
            try:
                parsed = json.loads(raw_args)
                args = [str(a) for a in parsed] if isinstance(parsed, list) else None
            except ValueError:
                args = None
            if args is None:
                return json.dumps({"ok": False, "error": "args_json 必须是 JSON 数组。"},
                                  ensure_ascii=False)
        else:
            args = [a for a in (f.get("args") or "").split() if a]
        cfg["args"] = args
        cfg["env"] = {}
    else:
        url = (f.get("url") or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return json.dumps({"ok": False, "error": "http 连接器需要合法 http(s) URL。"},
                              ensure_ascii=False)
        cfg["url"] = url
    enabled_raw = (f.get("enabled") or "").strip().lower()
    cfg["enabled"] = enabled_raw not in ("0", "false", "no", "off")
    cfg["transport"] = transport

    target = _mcp_server_approval_target(key, cfg)
    gate = _mcp_gate_blocked(root, "mcp_server", target)
    if gate:
        gate["hint"] = ("先调用 dev_approve，输入 action: mcp_server 换行 target: "
                        + target + "，审批通过后用完全相同的参数重试 dev_mcp_add。")
        return json.dumps(gate, ensure_ascii=False)
    try:
        mcp_client.save_server(root, key, cfg)
        server = mcp_client.get_server_config(root, key)
    except mcp_client.MCPError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    return json.dumps({"ok": True, "action": "added",
                       "next": "调用 dev_mcp_probe 测试连接（initialize + tools/list）",
                       "server": {k: server.get(k) for k in
                                  ("key", "label", "transport", "enabled", "engine")}},
                      ensure_ascii=False)


def dev_mcp_probe(arg):
    """探活一个已装配连接器：完成 MCP initialize + tools/list，返回工具数量与名称。

    输入：连接器 key（可带 'key: ' 前缀）。stdio 首次冷启动（如 uvx 下载依赖）可能耗时较久。
    返回 {ok,server,transport,tool_count,tools[],elapsed_ms}；失败返回 {ok:false,error}，
    据此修正 command/args/url 后重新 dev_mcp_add，或 dev_mcp_remove 移除。
    """
    root = _mcp_project_root()
    if not root:
        return json.dumps({"ok": False, "error": "未配置代码库"}, ensure_ascii=False)
    key = str(arg or "").strip()
    if key.lower().startswith("key:"):
        key = key[4:].strip()
    if not key:
        return json.dumps({"ok": False, "error": "缺少连接器 key。"}, ensure_ascii=False)
    try:
        result = mcp_client.probe_server(root, key)
        # 工具列表可能很长，只回传名称，schema 用 dev_list_connector_tools 看
        result["tools"] = (result.get("tools") or [])[:60]
        result["next"] = "探活成功后调用 dev_mcp_discover 生成能力候选"
        return json.dumps(result, ensure_ascii=False)[:6000]
    except mcp_client.MCPError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


def dev_mcp_discover(arg):
    """读取连接器工具并生成【待审批】能力候选（不会自动启用路由）。

    输入：连接器 key。前置：dev_mcp_add 已添加且 dev_mcp_probe 成功（引擎类 stdio 还需
    对应软件已打开）。返回候选摘要：domain/capabilities/tool_count/confidence/tool_mappings。
    之后必须向用户说明该连接器能做什么，用户确认后：dev_approve(action: mcp_capability,
    target: <key>) → dev_mcp_decide(decision: approve) 才允许 Agent 自动路由调用。
    """
    root = _mcp_project_root()
    if not root:
        return json.dumps({"ok": False, "error": "未配置代码库"}, ensure_ascii=False)
    key = str(arg or "").strip()
    if key.lower().startswith("key:"):
        key = key[4:].strip()
    if not key:
        return json.dumps({"ok": False, "error": "缺少连接器 key。"}, ensure_ascii=False)
    try:
        result = mcp_capabilities.discover(root, key)
    except mcp_client.MCPError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    if not result.get("ok"):
        return json.dumps(result, ensure_ascii=False)
    cand = result["candidate"]
    return json.dumps({"ok": True, "candidate": {
        "server": cand["server"], "domain": cand["domain"], "status": cand["status"],
        "capabilities": cand["capabilities"], "tool_count": cand["tool_count"],
        "confidence": cand["confidence"], "best_for": cand["best_for"],
        "tool_mappings": cand["tool_mappings"][:40],
    }, "next": ("向用户说明能力清单并取得确认；随后 dev_approve(action: mcp_capability, "
                f"target: {key}) 再 dev_mcp_decide(decision: approve, key: {key})")},
        ensure_ascii=False)[:6000]


def dev_mcp_decide(arg):
    """批准/拒绝连接器的能力候选。批准是敏感操作，需先通过 mcp_capability 审批。

    输入（多行 key: value）：
      decision: approve   # 或 reject
      key: <连接器key>
    批准后该连接器进入路由器，Agent 可经 dev_route_connector/dev_mcp_call 自动调用；
    拒绝则候选作废（不影响已保存的连接器配置，可用 dev_mcp_remove 彻底移除）。
    """
    root = _mcp_project_root()
    if not root:
        return json.dumps({"ok": False, "error": "未配置代码库"}, ensure_ascii=False)
    f = _parse_keyed(str(arg or ""), ["decision", "key", "approved"])
    key = (f.get("key") or "").strip()
    decision = (f.get("decision") or f.get("approved") or "").strip().lower()
    approved = decision in ("approve", "approved", "1", "true", "yes", "on")
    rejected = decision in ("reject", "rejected", "0", "false", "no", "off")
    if not key or not (approved or rejected):
        return json.dumps({"ok": False,
                           "error": "需要 key 与 decision(approve|reject)。"}, ensure_ascii=False)
    if approved:
        gate = _mcp_gate_blocked(root, "mcp_capability", key)
        if gate:
            gate["hint"] = (f"先调用 dev_approve，输入 action: mcp_capability 换行 target: {key}，"
                            "审批通过后重试 dev_mcp_decide。")
            return json.dumps(gate, ensure_ascii=False)
    try:
        result = mcp_capabilities.approve(root, key, approved)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    if not result.get("ok"):
        return json.dumps(result, ensure_ascii=False)
    return json.dumps({"ok": True, "decision": "approved" if approved else "rejected",
                       "server": key,
                       "routing_enabled": approved}, ensure_ascii=False)


def dev_mcp_remove(arg):
    """移除（自定义）或禁用（内置预设）一个 MCP 连接器。敏感操作：需先通过 mcp_server 审批。

    输入：连接器 key（可带 'key: ' 前缀）。会先关闭活动会话。审批 target 即 key 本身：
    dev_approve(action: mcp_server, target: <key>) 后重试。
    """
    root = _mcp_project_root()
    if not root:
        return json.dumps({"ok": False, "error": "未配置代码库"}, ensure_ascii=False)
    key = str(arg or "").strip()
    if key.lower().startswith("key:"):
        key = key[4:].strip()
    if not key:
        return json.dumps({"ok": False, "error": "缺少连接器 key。"}, ensure_ascii=False)
    gate = _mcp_gate_blocked(root, "mcp_server", key)
    if gate:
        gate["hint"] = (f"先调用 dev_approve，输入 action: mcp_server 换行 target: {key}，"
                        "审批通过后重试 dev_mcp_remove。")
        return json.dumps(gate, ensure_ascii=False)
    try:
        mcp_client.close_server(root, key)
        result = mcp_client.remove_server(root, key)
        servers = [{"key": s.get("key"), "enabled": s.get("enabled")}
                   for s in result.get("servers", [])]
        return json.dumps({"ok": True, "action": "removed_or_disabled",
                           "server": key, "servers": servers}, ensure_ascii=False)
    except mcp_client.MCPError as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)


def dev_mcp_discover_from_need(arg):
    """从自然语言需求发现可装配的 MCP 连接器候选（不写盘、不自动启用）。

    输入：需求描述，如「我需要能查高铁票的 MCP / 数据库 MCP / github MCP」。
    可选多行 web_enabled: true 开启联网（默认仅离线精选索引）。

    流程（离线优先，联网仅走 GitHub 域）：
      1) 离线精选索引命中已知热门 server；
      2) web_enabled 时 GitHub 域限定搜索（platform:github）→ 仓库 README → 解析官方命令；
    候选均过 R1-R9 信任闸门。返回后须向用户展示候选，逐条经 dev_mcp_add（审批）落盘。
    """
    root = _mcp_project_root()
    if not root:
        return json.dumps({"ok": False, "error": "未配置代码库"}, ensure_ascii=False)
    raw = str(arg or "")
    web_enabled = False
    lines = []
    for ln in raw.splitlines():
        m = re.match(r"^\s*web_enabled\s*[:：]\s*(\S+)", ln, re.I)
        if m:
            web_enabled = m.group(1).lower() in ("1", "true", "yes", "on")
        else:
            lines.append(ln)
    need = re.sub(r"^(?:need|需求)\s*[:：]\s*", "", " ".join(lines).strip(), flags=re.I).strip() or raw.strip()
    if not need:
        return json.dumps({"ok": False, "error": "需求描述为空"}, ensure_ascii=False)
    try:
        import mcp_autoconnect
        result = mcp_autoconnect.discover_from_need(
            root, need, web_enabled=web_enabled,
            github_search_fn=(lambda q: web_search("platform:github " + q + " MCP server")))
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    if not result.get("ok"):
        return json.dumps({"ok": False, "candidates": [],
                           "search_error": result.get("search_error") or "未找到匹配的 MCP 连接器",
                           "next": "可换更具体的需求词，或 web_enabled: true 联网经 GitHub 搜索"},
                          ensure_ascii=False)
    cands = []
    for c in result.get("candidates") or []:
        cfg = c.get("config") or {}
        cands.append({
            "transport": cfg.get("transport"), "command": cfg.get("command"),
            "args": cfg.get("args"), "url": cfg.get("url"),
            "trust": c.get("trust"), "validation_errors": c.get("validation_errors"),
            "provenance": cfg.get("provenance"),
            "command_unresolved": cfg.get("command_unresolved"),
        })
    return json.dumps({"ok": True, "need": need, "source": result.get("source"),
                       "candidates": cands, "search_error": result.get("search_error"),
                       "next": "向用户展示候选；逐条 dev_mcp_add（key/command/args 或 url）经审批落盘，"
                               "再 dev_mcp_probe 探活、dev_mcp_discover 生成能力候选"},
                      ensure_ascii=False)[:8000]


def _skill_pending_dir():
    """待审批技能草稿目录；reload() 扫描排除以 '.' 开头的目录，故草稿不会自动生效。"""
    import skills as _skills_mod
    return os.path.join(os.path.abspath(_skills_mod.SKILLS_DIR), ".pending")


def dev_skill_create(arg):
    """起草用户技能（待审批，不会自动启用）—— skill 是*可执行行为*，必须经用户确认才激活。

    输入（多行 key: value）：name / description / body（技能正文，markdown）。
    写入 SKILLS_DIR/.pending/<name>/SKILL.md，返回完整正文供代理向用户展示。
    用户明确同意后才调用 dev_skill_approve 激活；严禁未经确认直接激活。
    """
    f = _parse_keyed(str(arg or ""), ["name", "description", "body"])
    name = (f.get("name") or "").strip()
    desc = (f.get("description") or "").strip()
    body = (f.get("body") or "").strip()
    if not name or not body:
        return json.dumps({"ok": False, "error": "name 与 body 必填"}, ensure_ascii=False)
    import skills as _skills_mod
    if _skills_mod.get(name):
        return json.dumps({"ok": False, "error": f"技能 {name} 已存在（活动态），勿覆盖"}, ensure_ascii=False)
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)[:80].strip(".-")
    if not clean:
        return json.dumps({"ok": False, "error": "技能名清洗后为空"}, ensure_ascii=False)
    pdir = _skill_pending_dir()
    tdir = os.path.join(pdir, clean)
    if os.path.exists(tdir):
        return json.dumps({"ok": False, "error": f"草稿 {clean} 已存在待审批，先 dev_skill_approve 或 dev_skill_reject"},
                          ensure_ascii=False)
    os.makedirs(tdir, exist_ok=True)
    raw = ("---\nname: %s\nversion: 0.1.0\ndescription: %s\nwhen_to_use: 用户确认后启用的复用工作流\n---\n\n%s\n"
           % (clean, desc.replace("\n", " "), body))
    try:
        with open(os.path.join(tdir, "SKILL.md"), "w", encoding="utf-8", newline="\n") as s:
            s.write(raw)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
    return json.dumps({"ok": True, "name": clean, "status": "pending", "body": body,
                       "next": "向用户完整展示正文并取得明确同意后，调用 dev_skill_approve(name: %s) 激活" % clean},
                      ensure_ascii=False)


def dev_skill_approve(arg):
    """激活一个待审批技能：从 .pending 移到 SKILLS_DIR 并 reload（变为可用）。仅当用户已确认。"""
    name = str(arg or "").strip()
    if name.lower().startswith("name:"):
        name = name[5:].strip()
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)[:80].strip(".-")
    if not clean:
        return json.dumps({"ok": False, "error": "技能名无效"}, ensure_ascii=False)
    import skills as _skills_mod
    import shutil
    src = os.path.join(_skill_pending_dir(), clean, "SKILL.md")
    if not os.path.exists(src):
        return json.dumps({"ok": False, "error": f"无待审批草稿 {clean}"}, ensure_ascii=False)
    dst_dir = os.path.join(os.path.abspath(_skills_mod.SKILLS_DIR), clean)
    if os.path.exists(dst_dir):
        return json.dumps({"ok": False, "error": f"活动技能 {clean} 已存在"}, ensure_ascii=False)
    os.makedirs(os.path.abspath(_skills_mod.SKILLS_DIR), exist_ok=True)
    shutil.move(src, os.path.join(dst_dir, "SKILL.md"))
    try:
        os.rmdir(os.path.join(_skill_pending_dir(), clean))
    except OSError:
        pass
    _skills_mod.reload()
    return json.dumps({"ok": True, "name": clean, "status": "active",
                       "next": "已激活，可经 dev_use_skill 调用"}, ensure_ascii=False)


def dev_skill_reject(arg):
    """丢弃一个待审批技能草稿（不激活、不保留）。"""
    name = str(arg or "").strip()
    if name.lower().startswith("name:"):
        name = name[5:].strip()
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", name)[:80].strip(".-")
    if not clean:
        return json.dumps({"ok": False, "error": "技能名无效"}, ensure_ascii=False)
    import shutil
    tdir = os.path.join(_skill_pending_dir(), clean)
    if not os.path.isdir(tdir):
        return json.dumps({"ok": False, "error": f"无待审批草稿 {clean}"}, ensure_ascii=False)
    shutil.rmtree(tdir)
    return json.dumps({"ok": True, "name": clean, "status": "rejected"}, ensure_ascii=False)


def _dedup_docs(docs, metas):
    """向量检索偶发把同一 chunk 返回多次（同一 source+文本出现 N 遍，曾出现 4 次同片）。

    用 (source, 规范化文本) 作去重键只保留首次出现，避免重复片段浪费 token、
    并防止 Agent 误以为"信息很多"而空转。返回过滤后的 (docs, metas)。"""
    seen = set()
    out_d, out_m = [], []
    for d, m in zip(docs, metas):
        key = (m.get("source", "") if m else "", (d or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out_d.append(d)
        out_m.append(m)
    return out_d, out_m


def search_knowledge(query):
    """在已上传的知识库中检索与问题相关的文档片段。"""
    retrieved = get_retriever(collection=get_runtime("knowledge_collection") or COLLECTION_NAME,
                               top_k=TOP_K, embedding_client=_get_emb()).invoke(query)
    docs = [item.page_content for item in retrieved]
    metas = [dict(item.metadata or {}) for item in retrieved]
    docs, metas = _dedup_docs(docs, metas)
    if not docs:
        return "知识库中未找到相关内容。"
    out = []
    for d, m in zip(docs, metas):
        src = pretty_source(m.get("source", "")) if m else ""
        if not src:
            src = "?"
        # 截断单个 chunk 的长度：避免把大段原文塞给小模型逼它"逐条抄"
        text = d if len(d) <= 500 else d[:500].rstrip() + "…"
        out.append(f"[{src}] {text}")
    return "\n---\n".join(out)


_CALC_BAD = "表达式包含非法字符，仅支持数字、+ - * / % ** //、括号与比较运算 > < >= <= == !=。"
_CALC_ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Compare, ast.Constant,
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow, ast.FloorDiv,
    ast.USub, ast.UAdd,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Load,
)
# 幂运算防护：** 的指数必须「可静态求出」且是 |值| ≤ _CALC_MAX_POW_EXPONENT 的整数，
# 且结果位数不得超过 _CALC_MAX_RESULT_DIGITS。否则 9**9**9 之类会让 eval 长时间占满
# CPU/内存（实测 >6s 不返回）。前置拒绝，不依赖事后计时。
_CALC_MAX_POW_EXPONENT = 512
_CALC_MAX_RESULT_DIGITS = 4000
# 静态求值中间结果的数值上界（bit_length）：超过即放弃（≈ 1.1e12），
# 保证静态分析本身绝不发生指数爆炸。
_CALC_STATIC_MAX_BITS = 40
_CALC_POW_EXP_BAD = (
    f"不支持的 ** 指数：指数必须是可静态求值且 ≤{_CALC_MAX_POW_EXPONENT} 的整数"
    f"（如 2**10、2**3**2；不支持 9**9**9）。"
)
_CALC_RESULT_TOO_BIG = (
    f"计算结果过大（超过 {_CALC_MAX_RESULT_DIGITS} 位），已拒绝以免卡死。"
)


def _calc_static_value(node, _depth=0):
    """有界静态求值：把 AST 子表达式算成具体数值（int/float）；算不出返回 None。

    仅支持：字面量、一元 +/-、以及 + - * / // % ** 运算。其中 `**` 只在
    「底数与指数都是可静态求出的整数、且各自 |值| ≤ 32」时才递归求值——这样
    9**9 可算出（供 2**3**2 之类的合法指数），而 9**9**9 的指数 9**9**9 在静态
    阶段就会被限流拒绝。任何中间整数结果 bit_length > _CALC_STATIC_MAX_BITS 即
    放弃。用于判断 ** 的指数是否「可静态求出」。
    """
    if _depth > 8:
        return None
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        return v
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _calc_static_value(node.operand, _depth + 1)
        if v is None:
            return None
        return -v if isinstance(node.op, ast.USub) else v
    if isinstance(node, ast.BinOp):
        left = _calc_static_value(node.left, _depth + 1)
        right = _calc_static_value(node.right, _depth + 1)
        if left is None or right is None:
            return None
        try:
            if isinstance(node.op, ast.Pow):
                # 仅当两侧都是可静态求出的整数且各自 ≤32 才计算，避开指数爆炸
                if (isinstance(left, int) and not isinstance(left, bool)
                        and isinstance(right, int) and not isinstance(right, bool)
                        and abs(left) <= 32 and abs(right) <= 32):
                    out = left ** right
                else:
                    return None
            elif isinstance(node.op, ast.Add):
                out = left + right
            elif isinstance(node.op, ast.Sub):
                out = left - right
            elif isinstance(node.op, ast.Mult):
                out = left * right
            elif isinstance(node.op, ast.Div):
                out = left / right
            elif isinstance(node.op, ast.FloorDiv):
                out = left // right
            elif isinstance(node.op, ast.Mod):
                out = left % right
            else:
                return None
        except (ZeroDivisionError, ValueError, OverflowError):
            return None
        if isinstance(out, int) and out.bit_length() > _CALC_STATIC_MAX_BITS:
            return None
        return out
    return None


def calculate(expression):
    """对数学表达式求值。

    支持数字、+ - * / % ** //、括号，以及比较运算 > < >= <= == !=
    （比较结果转成「成立/不成立」，方便模型解读为自然语言结论）。
    用 AST 白名单求值，名称/属性/调用等任何非算术节点一律拒绝。

    幂运算加固：** 的指数必须「可静态求出」且为 |值| ≤512（`_CALC_MAX_POW_EXPONENT`）
    的整数，结果位数不得超过 4000（`_CALC_MAX_RESULT_DIGITS`）；超限直接返回中文错误，
    避免 9**9**9 这类表达式长时间占满 CPU。
    """
    expression = (expression or "").strip().strip("'\"").strip()
    if not expression:
        return "未提供表达式。"
    if len(expression) > 200:
        return "表达式过长（上限 200 字符）。"
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError:
        return _CALC_BAD
    if any(not isinstance(node, _CALC_ALLOWED_NODES) for node in ast.walk(tree)):
        return _CALC_BAD
    # 前置拒绝：遍历 ** 运算（BinOp 且 op 为 Pow，注意 ast.Pow 是运算符节点、
    # 本身没有 left/right），要求其指数「可静态求出」且是 ≤512 的整数
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Pow):
            exp = _calc_static_value(node.right)
            if (not isinstance(exp, int) or isinstance(exp, bool)
                    or abs(exp) > _CALC_MAX_POW_EXPONENT):
                return _CALC_POW_EXP_BAD
    try:
        result = eval(compile(tree, "<calc>", "eval"), {"__builtins__": {}}, {})  # noqa: S307
    except Exception as e:  # noqa: BLE001
        return f"计算失败: {e}"
    if isinstance(result, bool):
        return "成立（True）" if result else "不成立（False）"
    # 结果规模兜底：即使指数合规，多重乘法也可能堆出超长整数。
    # str() 在超大整数上会触发 CPython 的 int↔str 转换上限（默认 4300 位）而抛
    # ValueError，这里把该异常也视为「结果过大」，顺带覆盖超过 4300 位的情形。
    if isinstance(result, int):
        try:
            too_big = len(str(result)) > _CALC_MAX_RESULT_DIGITS
        except ValueError:
            too_big = True
        if too_big:
            return _CALC_RESULT_TOO_BIG
    return str(result)


_SEARCH_EMPTY_MARKERS = ("搜索失败", "搜索未返回结果")


def _search_recency_query(q: str) -> str:
    """默认偏好近一年结果：给查询追加 after:<去年>；可用 env WEB_SEARCH_PREFER_RECENT=0 关闭，
    或查询已含 after:/before:/年份范围时跳过，避免重复拼接。"""
    if os.getenv("WEB_SEARCH_PREFER_RECENT", "1").strip().lower() in ("0", "false", "no"):
        return q
    if re.search(r"\b(after|before):", q) or re.search(r"\b\d{4}\.\.\d{4}\b", q):
        return q
    year = datetime.date.today().year - 1
    return f"{q} after:{year}"


# 平台别名 → 站点域名（web_search 的 platform: 参数自动映射，避免手敲 site:）。
_SEARCH_PLATFORMS = {
    "github": "github.com", "gh": "github.com",
    "b站": "bilibili.com", "bilibili": "bilibili.com", "哔哩哔哩": "bilibili.com",
    "微博": "weibo.com", "weibo": "weibo.com",
    "贴吧": "tieba.baidu.com", "tieba": "tieba.baidu.com",
    "百度": "baidu.com", "baidu": "baidu.com",
    "知乎": "zhihu.com", "zhihu": "zhihu.com",
    "csdn": "csdn.net",
    "stackoverflow": "stackoverflow.com", "so": "stackoverflow.com",
}


def _parse_web_search_arg(arg):
    """把 web_search 的单字符串入参解析成 (query, site)。

    - 多行 key: value：query:/site:/platform:（platform 自动映射为域名）；
    - 任何位置写 `site:github.com` 或 `platform:github`：自动从 query 剔除该标记；
    - 否则整串作为 query。
    返回 (query, site)，site 为 None 表示不限站。
    """
    text = (arg or "").strip()
    if not text:
        return "", None
    site = [None]

    def _take(key, val):
        val = val.strip().strip("'\"")
        site[0] = val if key == "site" else _SEARCH_PLATFORMS.get(val.lower(), val)

    q_parts = []
    for ln in text.splitlines():
        m = re.match(r"^\s*(site|platform)\s*[:：]\s*(.+?)\s*$", ln, re.I)
        if m:
            _take(m.group(1).lower(), m.group(2))
            continue
        # 行内 site:/platform:（与 query 同行）
        mi = re.search(r"\b(site|platform)\s*[:：]\s*(\S+)", ln, re.I)
        if mi:
            _take(mi.group(1).lower(), mi.group(2))
            ln = re.sub(r"\b(site|platform)\s*[:：]\s*\S+", "", ln, flags=re.I).strip()
        # 去掉本行可能的 query:/q:/keyword: 前缀
        ln = re.sub(r"^(?:query|q|keyword)\s*[:：]\s*", "", ln, flags=re.I).strip()
        if ln:
            q_parts.append(ln)
    q = re.sub(r"\s+", " ", " ".join(q_parts)).strip()
    return q, site[0]


def _json_request(url, *, headers=None, timeout=15):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "DocMind/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _github_search(q):
    """GitHub 专用仓库搜索；无需 token，遇到 API 限流时明确回退通用搜索。"""
    key = _web_cache_key("github", q)
    if _search_cache_enabled():
        cached = _web_cache_read(key)
        if cached:
            return cached + "\n（缓存结果）"
    url = "https://api.github.com/search/repositories?" + urllib.parse.urlencode({"q": q, "per_page": 5, "sort": "updated"})
    try:
        data = _json_request(url, headers={"User-Agent": "DocMind/1.0", "Accept": "application/vnd.github+json"})
        rows = data.get("items") or []
        if not rows:
            return "搜索未返回结果，可能是网络受限或该关键词无结果。"
        lines = ["GitHub 专用搜索（仓库 API）："]
        for item in rows[:5]:
            desc = (item.get("description") or "").replace("\n", " ")
            meta = f"★{item.get('stargazers_count', 0)} · 最近更新 {item.get('updated_at', '')[:10]}"
            lines.append(_format_search_result(item.get("full_name") or item.get("name", ""), f"{desc}（{meta}）", item.get("html_url", "")))
        result = "\n".join(lines)
        if _search_cache_enabled():
            _web_cache_write(key, result)
        return result
    except Exception as exc:
        return f"GitHub API 暂不可用（{type(exc).__name__}: {exc}）。可重试通用站点搜索。"


def _bilibili_search(q):
    """B 站专用视频搜索；公共接口被风控时返回可理解的降级信息。"""
    key = _web_cache_key("bilibili", q)
    if _search_cache_enabled():
        cached = _web_cache_read(key)
        if cached:
            return cached + "\n（缓存结果）"
    params = urllib.parse.urlencode({"search_type": "video", "keyword": q, "page": 1, "page_size": 5})
    url = "https://api.bilibili.com/x/web-interface/search/type?" + params
    try:
        data = _json_request(url, headers={"User-Agent": "Mozilla/5.0 (DocMind/1.0)", "Referer": "https://www.bilibili.com/"})
        rows = ((data.get("data") or {}).get("result") or [])
        if not rows:
            return "B 站搜索未返回结果（可能需要验证码或该关键词无结果）。"
        lines = ["B 站专用视频搜索："]
        for item in rows[:5]:
            title = re.sub(r"<[^>]+>", "", item.get("title", ""))
            bvid = item.get("bvid") or ""
            link = "https://www.bilibili.com/video/" + bvid if bvid else item.get("arcurl", "")
            desc = re.sub(r"<[^>]+>", "", item.get("description", "") or "").replace("\n", " ")
            lines.append(_format_search_result(title, desc, link))
        result = "\n".join(lines)
        if _search_cache_enabled():
            _web_cache_write(key, result)
        return result
    except Exception as exc:
        return f"B 站专用搜索暂不可用（{type(exc).__name__}: {exc}）。可改用 platform: b站 的通用搜索。"


def _bilibili_id(url):
    text = str(url or "")
    m = re.search(r"/(BV[0-9A-Za-z]+)", text, re.I) or re.search(r"[?&]bvid=(BV[0-9A-Za-z]+)", text, re.I)
    if m:
        return "bvid", m.group(1)
    m = re.search(r"(?:av|aid=)(\d+)", text, re.I)
    return ("aid", m.group(1)) if m else ("", "")


def web_subtitles(arg):
    """提取公开 B 站视频字幕；登录、UP 主未发布字幕时返回明确原因。"""
    kind, value = _bilibili_id((arg or "").strip())
    if not value:
        return "字幕提取失败：请输入包含 BV 号或 av 号的 B 站视频 URL。"
    try:
        params = {kind: value}
        view = _json_request("https://api.bilibili.com/x/web-interface/view?" + urllib.parse.urlencode(params),
                             headers={"User-Agent": "Mozilla/5.0 (DocMind/1.0)"})
        data = view.get("data") or {}
        if not data:
            return "字幕提取失败：视频不存在、不可见或接口被风控。"
        cid = data.get("cid") or ((data.get("pages") or [{}])[0].get("cid"))
        if not cid:
            return "字幕提取失败：视频没有可读取的分 P。"
        query = urllib.parse.urlencode({"bvid": data.get("bvid", value) if kind == "bvid" else "", "aid": data.get("aid", value) if kind == "aid" else "", "cid": cid})
        player = _json_request("https://api.bilibili.com/x/player/v2?" + query,
                               headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com/"})
        entries = (((player.get("data") or {}).get("subtitle") or {}).get("subtitles") or [])
        if not entries:
            return "该视频没有公开字幕，或字幕需要登录后才能读取。"
        sub = entries[0]
        sub_url = sub.get("subtitle_url", "")
        if sub_url.startswith("//"):
            sub_url = "https:" + sub_url
        payload = _json_request(sub_url, headers={"User-Agent": "Mozilla/5.0"})
        body = payload.get("body") or []
        if not body:
            return "字幕接口返回空内容。"
        lines = [f"标题：{data.get('title', '')}", f"来源：https://www.bilibili.com/video/{data.get('bvid', value)}", "字幕："]
        lines.extend(f"[{row.get('from', 0):.1f}s] {row.get('content', '').strip()}" for row in body[:600] if row.get("content"))
        return "\n".join(lines)
    except Exception as exc:
        return f"字幕提取失败：{type(exc).__name__}: {exc}"


def web_search(query):
    """联网搜索（按设置选择服务商，无需 Key 的内置后端默认可用）。

    入参为单字符串，可带站点限定：
    - `query: 关键词` 或直接写关键词；
    - 追加 `site: github.com` 或 `platform: github`（自动映射为 site:github.com），
      把结果收敛到指定站（GitHub / 哔哩哔哩 / 微博 / 百度贴吧 等）。

    服务商（设置中可切换）：auto（内置 ddg→baidu→bing 故障转移）/ ddg / bing / baidu /
    exa / tavily / searxng / bocha / firecrawl / zhipu / querit / parallel / mcp_exa。
    API 类服务商需先在设置填 API Key（或自建实例地址）。返回前 5 条标题/摘要/链接。
    """
    q, site = _parse_web_search_arg(query)
    if not q:
        return "未提供搜索关键词。"
    # 对 GitHub / B 站提供专用结构化入口；失败时仍可用通用 HTML 搜索。
    _in_unit_test = getattr(getattr(sys.modules.get("__main__"), "__spec__", None), "name", None) == "unittest.__main__"
    specialized_error = ""
    if site == "github.com" and not _in_unit_test:
        result = _github_search(q)
        if not result.startswith("GitHub API 暂不可用"):
            return result
        # API 被限流时继续给出普通站点结果，不把一次 API 失败当成整个搜索失败。
        specialized_error = result
    if site == "bilibili.com" and not _in_unit_test:
        result = _bilibili_search(q)
        if not result.startswith("B 站专用搜索暂不可用"):
            return result
        # B 站接口被验证码拦截时走 DDG/百度/Bing 的 site: 回退。
        specialized_error = result
    if site:
        q = f"{q} site:{site}"
    q = _search_recency_query(q)
    provider = get_web_search_provider()
    cache_key = _web_cache_key("search:" + provider, q)
    if _search_cache_enabled():
        cached = _web_cache_read(cache_key)
        if cached:
            return cached + "\n（缓存结果）"
    if provider in ("builtin_auto", "ddg", "bing", "baidu"):
        result = _builtin_search(provider, q)
    else:
        result = _api_web_search(provider, q)
    if specialized_error and not any(result.startswith(m) for m in _SEARCH_EMPTY_MARKERS):
        result = specialized_error + "\n\n通用搜索回退结果：\n" + result
    if _search_cache_enabled() and not any(result.startswith(m) for m in _SEARCH_EMPTY_MARKERS):
        _web_cache_write(cache_key, result)
    return result


def _builtin_search(provider, q):
    """内置无 Key 后端：auto 走 ddg→baidu→bing 故障转移；指定单后端则只走该后端。"""
    backends = {"ddg": _ddg_search, "bing": _bing_search, "baidu": _baidu_search}
    order = [provider] if provider in backends else ["ddg", "baidu", "bing"]
    last = ""
    for backend in order:
        try:
            r = backends[backend](q)
        except Exception as e:  # noqa: BLE001
            last = f"搜索失败: {backend}: {type(e).__name__}: {e}"
            continue
        if not any(r.startswith(m) for m in _SEARCH_EMPTY_MARKERS):
            return r
        last = r
    hard_error = last.startswith("搜索失败: ")
    return (last or "搜索失败：所有搜索后端均不可用。") + (
        "" if (not hard_error and last.startswith(_SEARCH_EMPTY_MARKERS)) else
        "（DuckDuckGo/百度/Bing 均不可达，请确认运行环境能访问外网，或配置带 Key 的搜索引擎）")


def _api_web_search(provider, q):
    """API 类搜索服务商（需 Key 或自建地址），失败给出清晰原因或回退内置。"""
    key = get_web_search_api_key()
    url = get_web_search_api_url()
    try:
        if provider == "exa":
            return _exa_search(q, key)
        if provider == "tavily":
            return _tavily_search(q, key)
        if provider == "searxng":
            return _searxng_search(q, url)
        if provider == "bocha":
            return _bocha_search(q, key)
        if provider == "firecrawl":
            return _firecrawl_search(q, key)
        # zhipu / querit / parallel / mcp_exa：通用 keyed POST，需自定义 API 地址
        if url:
            return _generic_api_search(provider, q, key, url)
        return ("该搜索服务商需要先在「网络搜索」设置里填写 API 地址或 API Key 才能使用；"
                "请填写后保存，或把服务商切回「自动（内置）」。")
    except Exception as e:  # noqa: BLE001
        return f"搜索失败（{provider}）：{type(e).__name__}: {e}"


def _exa_search(q, key):
    if not key:
        return "Exa 需要 API Key，请在「网络搜索」设置中填写后保存。"
    api_url = "https://api.exa.ai/search"
    payload = {"query": q, "numResults": 5, "contents": {"text": True}}
    req = urllib.request.Request(
        api_url, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    results = data.get("results") or []
    if not results:
        return "搜索未返回结果，可能是网络受限或该关键词无结果。"
    lines = []
    for it in results[:5]:
        text = (it.get("text") or "").strip().replace("\n", " ")
        lines.append(f"· {it.get('title', '')}\n  {text[:200]}\n  {it.get('url', '')}")
    return "\n".join(lines)


def _tavily_search(q, key):
    if not key:
        return "Tavily 需要 API Key，请在「网络搜索」设置中填写后保存。"
    api_url = "https://api.tavily.com/search"
    payload = {"api_key": key, "query": q, "max_results": 5, "search_depth": "basic"}
    req = urllib.request.Request(
        api_url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    results = data.get("results") or []
    if not results:
        return "搜索未返回结果，可能是网络受限或该关键词无结果。"
    lines = [f"· {it.get('title', '')}\n  {(it.get('content', '') or '')[:200]}\n  {it.get('url', '')}"
             for it in results[:5]]
    return "\n".join(lines)


def _searxng_search(q, url):
    if not url:
        return "SearXNG 需要填写自建实例地址（API 地址），请在「网络搜索」设置中填写后保存。"
    api_url = f"{url.rstrip('/')}/search?q={urllib.parse.quote(q)}&format=json"
    req = urllib.request.Request(api_url, headers={"User-Agent": "Mozilla/5.0 (DocMind/1.0)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    results = data.get("results") or []
    if not results:
        return "搜索未返回结果，可能是网络受限或该关键词无结果。"
    lines = [f"· {it.get('title', '')}\n  {(it.get('content', '') or '')[:200]}\n  {it.get('url', '')}"
             for it in results[:5]]
    return "\n".join(lines)


def _bocha_search(q, key):
    if not key:
        return "Bocha 需要 API Key，请在「网络搜索」设置中填写后保存。"
    api_url = "https://api.bochaai.com/openapi/v1/web-search"
    payload = {"query": q, "count": 5, "freshness": "noLimit"}
    req = urllib.request.Request(
        api_url, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    results = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
    if not results:
        return "搜索未返回结果，可能是网络受限或该关键词无结果。"
    lines = [f"· {it.get('name', '')}\n  {(it.get('snippet', '') or '')[:200]}\n  {it.get('url', '')}"
             for it in results[:5]]
    return "\n".join(lines)


def _firecrawl_search(q, key):
    if not key:
        return "Firecrawl 需要 API Key，请在「网络搜索」设置中填写后保存。"
    api_url = "https://api.firecrawl.dev/v1/search"
    payload = {"query": q, "limit": 5, "pageOptions": {"fetchPageContent": False}}
    req = urllib.request.Request(
        api_url, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    results = data.get("data") or []
    if not results:
        return "搜索未返回结果，可能是网络受限或该关键词无结果。"
    lines = [f"· {it.get('title', '')}\n  {(it.get('description', '') or '')[:200]}\n  {it.get('url', '')}"
             for it in results[:5]]
    return "\n".join(lines)


def _generic_api_search(provider, q, key, url):
    """zhipu/querit/parallel/mcp_exa 等未内置端点的服务商：通用 keyed POST。"""
    payload = {"query": q, "q": q, "numResults": 5, "max_results": 5, "count": 5}
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (DocMind/1.0)"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    results = data.get("results") or data.get("data") or []
    if isinstance(results, dict):
        results = results.get("results") or results.get("value") or []
    if not results:
        return "搜索未返回结果，可能是网络受限或该关键词无结果。"
    lines = []
    for it in results[:5]:
        if isinstance(it, dict):
            lines.append(f"· {it.get('title', it.get('name', ''))}\n"
                         f"  {(it.get('snippet', it.get('content', it.get('description', '')) or '')[:200])}\n"
                         f"  {it.get('url', it.get('link', ''))}")
        else:
            lines.append(f"· {it}")
    return "\n".join(lines)


def _ddg_search(q):
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; DocMind/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        page = r.read().decode("utf-8", "replace")

    def _clean(s):
        s = re.sub(r"<[^>]+>", "", s or "")
        return urllib.parse.unquote(s).strip()

    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', page, re.S)
    links = re.findall(r'class="result__a"[^>]*href="(.*?)"', page)
    snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
    if not titles:
        # 兜底：DDG 偶尔换结构，尝试更宽松的抓取
        titles = re.findall(r'<a[^>]+class="[^"]*result__a[^"]*"[^>]*>(.*?)</a>', page, re.S)
    if not titles:
        return "搜索未返回结果，可能是网络受限或该关键词无结果。"

    lines = []
    for i in range(min(5, len(titles))):
        t = _clean(titles[i])
        s = _clean(snippets[i]) if i < len(snippets) else ""
        link = links[i] if i < len(links) else ""
        # DDG 的跳转链接可能带 uddg= 编码，尽量还原
        if "uddg=" in link:
            try:
                link = urllib.parse.unquote(link.split("uddg=", 1)[1].split("&", 1)[0])
            except Exception:
                pass
        lines.append(_format_search_result(t, s, link))
    return "\n".join(lines)


def _bing_real_url(link):
    """把 Bing 的 ck/a 跳转链接还原成真实目标 URL；非跳转链接原样返回。

    形如 https://www.bing.com/ck/a?...&u=a1aHR0cHM6...&ntb=1，u 参数去掉
    前缀 a1 后是 URL 的 base64（urlsafe，常缺填充）。
    """
    if not link or "bing.com/ck/a" not in link:
        return link
    m = re.search(r"[?&]u=a1([A-Za-z0-9_\-]+)", link)
    if not m:
        return link
    import base64 as _b64
    raw = m.group(1)
    raw += "=" * (-len(raw) % 4)
    try:
        return _b64.urlsafe_b64decode(raw).decode("utf-8", "replace")
    except Exception:
        return link


def _bing_search(q):
    """Bing HTML 搜索（无需 Key）。用于 DuckDuckGo 被屏蔽的网络环境。"""
    url = "https://www.bing.com/search?q=" + urllib.parse.quote(q)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; DocMind/1.0)"}
    )
    with urllib.request.urlopen(req, timeout=12) as r:
        page = r.read().decode("utf-8", "replace")

    def _clean(s):
        s = re.sub(r"<[^>]+>", "", s or "")
        return urllib.parse.unquote(s).strip()

    # 每个结果都在 <li class="b_algo"> 块内：
    #   标题 = <h2 ...><a ... href="URL">文本</a></h2>（注意 h2 可能带 class 属性）
    #   摘要 = <p class="b_lineclampN ...">文本</p>
    blocks = re.split(r'<li class="b_algo"', page)[1:]
    results = []
    for blk in blocks:
        hm = re.search(
            r'<h2[^>]*>\s*<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>\s*</h2>',
            blk, re.S,
        )
        if not hm:
            continue
        link = _bing_real_url(hm.group(1))
        title = _clean(hm.group(2))
        sm = re.search(r'<p class="b_lineclamp[^"]*"[^>]*>(.*?)</p>', blk, re.S)
        snippet = _clean(sm.group(1)) if sm else ""
        results.append((title, snippet, link))
        if len(results) >= 5:
            break
    if not results:
        return "搜索未返回结果，可能是网络受限或该关键词无结果。"
    lines = [_format_search_result(t, s, link) for (t, s, link) in results]
    return "\n".join(lines)


def _baidu_search(q):
    """百度 HTML 搜索（无需 Key）。国内网络通常比 Bing 更稳，作为 auto 兜底层之一。"""
    url = "https://www.baidu.com/s?wd=" + urllib.parse.quote(q)
    req = urllib.request.Request(
        url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
    )
    with urllib.request.urlopen(req, timeout=12) as r:
        page = r.read().decode("utf-8", "replace")

    def _clean(s):
        s = re.sub(r"<[^>]+>", "", s or "")
        return urllib.parse.unquote(s).strip()

    # 每个自然结果在 <div class="result ..."> 块内：标题 <h3 ...><a href="URL">文本</a></h3>，
    # 摘要 <div class="c-abstract ...">文本</div>。百度偶把跳转塞进链接参数，原样保留即可。
    blocks = re.split(r'<div class="result', page)[1:]
    results = []
    for blk in blocks:
        hm = re.search(
            r'<h3[^>]*>\s*<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', blk, re.S
        )
        if not hm:
            continue
        link = hm.group(1)
        title = _clean(hm.group(2))
        sm = re.search(r'<div class="c-abstract[^"]*"[^>]*>(.*?)</div>', blk, re.S)
        if not sm:
            sm = re.search(r'class="[^"]*content-right[^"]*"[^>]*>(.*?)</span>', blk, re.S)
        snippet = _clean(sm.group(1)) if sm else ""
        results.append((title, snippet, link))
        if len(results) >= 5:
            break
    if not results:
        return "搜索未返回结果，可能是网络受限或该关键词无结果。"
    lines = [_format_search_result(t, s, link) for (t, s, link) in results]
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# 网页图片观察通道（仅 builtin 抓 HTML 时启用）
#
# 图片只是给视觉模型的【观察素材】，不是代码事实；下载与回传受四重约束：
#   1) DOCMIND_WEB_IMAGES 开关（默认 1）且当前会话模型具备看图途径才下载；
#   2) 仅 http/https，阻断内网/回环/链路本地地址（防 <img src=内网> SSRF）；
#   3) Content-Type 白名单 + Pillow 真实图头校验 + ≤800KB + 8s 超时；
#   4) web_fetch 单次 ≤2 张，web_research 跨源合计 ≤4，URL 去重。
# 搜索摘要缓存仍只存文本，任何缓存命中分支都不带图。
# ---------------------------------------------------------------------------
_WEB_FETCH_IMAGE_LIMIT = 2
_WEB_RESEARCH_IMAGE_LIMIT = 4
_WEB_IMAGE_MAX_BYTES = 800 * 1024
_WEB_IMAGE_TIMEOUT = 8.0
_WEB_IMAGE_CONTENT_TYPES = ("image/png", "image/jpeg", "image/jpg", "image/webp")
_WEB_IMAGE_UA = "Mozilla/5.0 (DocMind research; image-observation)"
_IMG_ICON_WORDS = ("icon", "logo", "sprite", "avatar", "badge", "button", "1x1",
                   "spacer", "pixel", "favicon", "thumb", "placeholder", "loading",
                   "banner-ad", "advert", "ads/", "/ads", "tracker", "emoji")
_IMG_EXT_RE = re.compile(r"\.(?:png|jpe?g|webp)(?:[?#]|$)", re.I)
_TAG_IMG_RE = re.compile(r"<img\b[^>]*>", re.I | re.S)
_ATTR_RE_TMPL = r"""{name}\s*=\s*["']([^"']*)["']"""


def _web_images_enabled():
    """开关 + 当前模型看图能力（能力判定必须复用 config 画像/vision 层配置）。"""
    if os.getenv("DOCMIND_WEB_IMAGES", "1").strip().lower() in ("0", "false", "no", "off"):
        return False
    # 本轮会话模型优先（云端按请求 llm 覆盖时全局画像不代表当前模型）。
    session_mode = _session_vision_mode.get()
    if session_mode is not None:
        return session_mode == "native" or bool(
            (os.getenv("DOCMIND_VISION_MODEL") or "").strip())
    try:
        from llm import LLM_PROVIDER, LLM_MODEL
        from config import model_capability
        provider = get_runtime("llm_provider") or LLM_PROVIDER
        model = get_runtime("llm_model") or LLM_MODEL
        vision_mode = model_capability(provider, model).get("vision")
        return vision_mode == "native" or bool((os.getenv("DOCMIND_VISION_MODEL") or "").strip())
    except Exception:
        return False


def _ip_is_internal(addr):
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _image_host_blocked(host):
    """图片目标 host 的 SSRF 守卫：回环/内网/链路本地/解析失败一律拒绝。

    域名解析到的任一地址落在内网即拒绝（防 DNS 混地址 rebinding）。
    """
    host = (host or "").strip("[]").lower()
    if not host or host == "localhost":
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except OSError:
            return True
        addrs = {info[4][0] for info in infos}
        return any(_ip_is_internal(addr) for addr in addrs) or not addrs
    else:
        return _ip_is_internal(host)


def _img_attr(tag, name):
    m = re.search(_ATTR_RE_TMPL.format(name=name), tag, re.I)
    return m.group(1).strip() if m else ""


def _query_terms(hint):
    """英文按词、中文按二元字组切查询词，用于 alt/文件名相关性打分。"""
    hint = (hint or "").lower()
    terms = set(re.findall(r"[a-z0-9][a-z0-9+\-_.]{1,}", hint))
    cjk = re.findall(r"[\u4e00-\u9fff]+", hint)
    for run in cjk:
        for i in range(len(run) - 1):
            terms.add(run[i:i + 2])
        if len(run) == 1:
            terms.add(run)
    return {t for t in terms if len(t) >= 2 or re.match(r"[\u4e00-\u9fff]", t)}


def _looks_like_icon(url, alt):
    blob = ((url or "") + " " + (alt or "")).lower()
    return any(w in blob for w in _IMG_ICON_WORDS)


def _extract_og_image(html):
    for pattern in (
        r'<meta\b[^>]*property\s*=\s*["\']og:image["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
        r'<meta\b[^>]*content\s*=\s*["\']([^"\']+)["\'][^>]*property\s*=\s*["\']og:image["\']',
        r'<meta\b[^>]*name\s*=\s*["\']twitter:image["\'][^>]*content\s*=\s*["\']([^"\']+)["\']',
        r'<meta\b[^>]*content\s*=\s*["\']([^"\']+)["\'][^>]*name\s*=\s*["\']twitter:image["\']',
    ):
        m = re.search(pattern, html, re.I | re.S)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return ""


def _extract_image_candidates(html, base_url, query_hint="", limit=2):
    """从 HTML 选取正文相关图片绝对 URL，按相关度排序（og:image 优先）。

    选取规则：og:image/twitter:image 头图优先并加分；正文 img 需有图片扩展名，
    过滤 data-uri、图标/广告/占位词、显式小尺寸；alt/title/文件名与查询词重合
    越多越靠前，同站加分；同 URL 去重。任何解析异常返回空列表。
    """
    if not html:
        return []
    terms = _query_terms(query_hint)
    base_host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    candidates = []  # (score, order, url)
    seen = set()

    def _consider(raw, order, og=False):
        if not raw or raw.strip().lower().startswith("data:"):
            return
        abs_url = urllib.parse.urljoin(base_url, raw.strip())
        ok, _ = _url_scheme_ok(abs_url)
        if not ok or abs_url in seen:
            return
        path = urllib.parse.urlparse(abs_url).path.lower()
        if not og and not _IMG_EXT_RE.search(path):
            return
        if _looks_like_icon(abs_url, ""):
            return
        seen.add(abs_url)
        score = 2.0 if og else 0.0
        host = (urllib.parse.urlparse(abs_url).hostname or "").lower()
        if base_host and (host == base_host or host.endswith("." + base_host)):
            score += 0.5
        candidates.append((score, order, abs_url))

    _consider(_extract_og_image(html), 0, og=True)
    order = 1
    for tag in _TAG_IMG_RE.findall(html):
        src = _img_attr(tag, "src") or _img_attr(tag, "data-src")
        if not src:
            continue
        alt = _img_attr(tag, "alt")
        title = _img_attr(tag, "title")
        if _looks_like_icon(src, alt + " " + title):
            order += 1
            continue
        # 显式小尺寸（图标/sprite）直接丢；缺尺寸的不拦。
        dims = []
        for attr in ("width", "height"):
            val = _img_attr(tag, attr)
            if val.isdigit():
                dims.append(int(val))
        if dims and max(dims) < 64:
            order += 1
            continue
        before = len(candidates)
        _consider(src, order, og=False)
        if len(candidates) > before and terms:
            path = urllib.parse.urlparse(candidates[-1][2]).path.lower()
            overlap = sum(1 for t in terms
                          if t in (alt + " " + title).lower()
                          or t in urllib.parse.unquote(path))
            score, _order, url = candidates[-1]
            candidates[-1] = (score + overlap, _order, url)
        order += 1
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [url for _s, _o, url in candidates[:limit]]


def _encode_observation_image(data):
    """Pillow 校验真实图头并缩放到最长边 1600、JPEG q82，返回 data URL 或 None。"""
    try:
        from PIL import Image, UnidentifiedImageError
        try:
            img = Image.open(io.BytesIO(data))
            img.verify()
        except (UnidentifiedImageError, OSError, ValueError):
            return None
        img = Image.open(io.BytesIO(data))
        if img.format not in ("PNG", "JPEG", "WEBP"):
            return None
        img = img.convert("RGB") if img.mode not in ("RGB", "L") else img
        if img.mode == "L":
            img = img.convert("RGB")
        img.thumbnail((1600, 1600), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82, optimize=True)
        encoded = base64.b64encode(buf.getvalue()).decode("ascii")
        return "data:image/jpeg;base64," + encoded
    except Exception:
        return None


class _ImageRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向每一跳都重新过 SSRF 守卫，禁止跨 scheme（防公网 302 到内网/file://）。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urljoin(req.full_url, newurl)
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in ("http", "https") or _image_host_blocked(parsed.hostname or ""):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# 显式组装 opener：build_opener 默认还带 File/FTP/Data handler，
# 图片下载通道不应有任何读本地文件/其它协议的能力（纵深防御，即使入口与
# 逐跳 scheme 白名单已经挡住 file/ftp，也不把这些处理器装进链里）。
_IMAGE_OPENER = urllib.request.OpenerDirector()
for _handler in (
    urllib.request.UnknownHandler(),
    _ImageRedirectHandler(),
    urllib.request.HTTPHandler(),
    urllib.request.HTTPSHandler(),
    urllib.request.HTTPDefaultErrorHandler(),
    urllib.request.HTTPErrorProcessor(),
):
    _IMAGE_OPENER.add_handler(_handler)
del _handler


def _download_observation_image(url, base_url=""):
    """下载并校验单张观察图片，成功返回 data URL；任何失败返回 None。"""
    abs_url = urllib.parse.urljoin(base_url or "", url)
    ok, _ = _url_scheme_ok(abs_url)
    if not ok:
        return None
    parsed = urllib.parse.urlparse(abs_url)
    if _image_host_blocked(parsed.hostname or ""):
        return None
    try:
        req = urllib.request.Request(abs_url, headers={"User-Agent": _WEB_IMAGE_UA})
        with _IMAGE_OPENER.open(req, timeout=_WEB_IMAGE_TIMEOUT) as resp:
            final = urllib.parse.urlparse(resp.geturl())
            # 服务器也可能不经过 302 而由代理层改写终点，终态再校验一次。
            if final.scheme not in ("http", "https") or _image_host_blocked(final.hostname or ""):
                return None
            content_type = (resp.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
            if content_type and content_type not in _WEB_IMAGE_CONTENT_TYPES:
                return None
            data = resp.read(_WEB_IMAGE_MAX_BYTES + 1)
        if not data or len(data) > _WEB_IMAGE_MAX_BYTES:
            return None
        encoded = _encode_observation_image(data)
        if not encoded:
            return None
        # 重编码 JPEG 必须仍是有界体积，防止原图绕过下载侧字节上限。
        if len(encoded) * 3 // 4 > _WEB_IMAGE_MAX_BYTES:
            return None
        return encoded
    except Exception:
        return None


def _collect_observation_images(html, base_url, query_hint, limit):
    """抽取候选 → 下载 → 去重，返回 (images, sources) 两个等长列表。"""
    images, sources = [], []
    candidates = _extract_image_candidates(html, base_url, query_hint=query_hint, limit=limit)
    for cand in candidates:
        if len(images) >= limit:
            break
        encoded = _download_observation_image(cand, base_url)
        if not encoded or encoded in images:
            continue
        images.append(encoded)
        sources.append(cand)
    return images, sources


def web_fetch(url):
    """读取公开网页正文的简化研究工具，返回标题、来源和清理后的文本。

    服务商（设置中可切换）：builtin（直接 urllib 抓 HTML 并清理）/ jina / firecrawl / custom。
    jina 与 firecrawl 需 API Key；custom 需填 API 地址（POST {url: ...} 取 markdown/text）。
    """
    ok, _ = _url_scheme_ok(url)
    if not ok: return "网页读取失败：只允许 http/https。"
    provider = get_web_fetch_provider()
    if provider == "builtin":
        return _fetch_page(url, image_budget=_WEB_FETCH_IMAGE_LIMIT)
    key = get_web_fetch_api_key()
    api_url = get_web_fetch_api_url()
    if provider == "jina":
        return _jina_fetch(url, key, api_url)
    if provider == "firecrawl":
        return _firecrawl_fetch(url, key, api_url)
    if provider == "custom":
        return _custom_fetch(url, key, api_url)
    return _fetch_page(url, image_budget=_WEB_FETCH_IMAGE_LIMIT)


def _jina_fetch(url, key, api_url):
    base = (api_url.rstrip("/") + "/") if api_url else "https://r.jina.ai/"
    target = base + url
    headers = {"User-Agent": "Mozilla/5.0 (DocMind research)"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        req = urllib.request.Request(target, headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read(2_000_000).decode("utf-8", "replace")
        raw = raw[:8000]
        return f"来源：{url}\n正文：\n{raw}" + ("\n[正文已截断]" if len(raw) >= 8000 else "")
    except Exception as e:
        return f"网页读取失败：{type(e).__name__}: {e}"


def _firecrawl_fetch(url, key, api_url):
    if not key:
        return "Firecrawl 需要 API Key，请在「URL 获取」设置中填写后保存。"
    endpoint = (api_url.rstrip("/") if api_url else "https://api.firecrawl.dev") + "/v1/scrape"
    payload = {"url": url, "formats": ["markdown"]}
    req = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
        md = ((data.get("data") or {}).get("markdown") or "").strip()
        if not md:
            return "网页读取失败：Firecrawl 未返回正文。"
        md = md[:8000]
        return f"来源：{url}\n正文：\n{md}" + ("\n[正文已截断]" if len(md) >= 8000 else "")
    except Exception as e:
        return f"网页读取失败：{type(e).__name__}: {e}"


def _custom_fetch(url, key, api_url):
    if not api_url:
        return "自定义 URL 获取需要填写 API 地址，请在「URL 获取」设置中填写后保存。"
    payload = {"url": url}
    headers = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0 (DocMind research)"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    try:
        req = urllib.request.Request(api_url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read(2_000_000).decode("utf-8", "replace")
        try:
            data = json.loads(raw)
            md = data.get("markdown") or data.get("text") or data.get("content") or raw
        except Exception:
            md = raw
        md = md[:8000]
        return f"来源：{url}\n正文：\n{md}" + ("\n[正文已截断]" if len(md) >= 8000 else "")
    except Exception as e:
        return f"网页读取失败：{type(e).__name__}: {e}"


def _fetch_page(url, *, image_budget=0, query_hint=""):
    """原 urllib 直抓 HTML 并清理的实现（builtin 服务商）。

    image_budget>0 且当前会话具备看图能力时，从同一份 HTML 抽取并下载相关图片，
    经 ToolResult.data 回传给 Agent 的统一视觉能力门；无图时返回纯字符串，
    保持与旧调用方的兼容。
    """
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (DocMind research)"})
        with urllib.request.urlopen(req, timeout=12) as r:
            raw = r.read(1_000_000).decode('utf-8','replace')
            final_url = r.geturl() or url
            content_type = r.headers.get('Content-Type', '')
        if 'html' not in content_type.lower() and '<html' not in raw[:500].lower():
            return f"来源：{final_url}\n内容类型：{content_type or '未知'}\n网页正文读取器仅支持 HTML 页面。"
        title = re.search(r'<title[^>]*>(.*?)</title>', raw, re.I|re.S)
        text = re.sub(r'<(script|style|noscript)[^>]*>.*?</\1>', ' ', raw, flags=re.I|re.S)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        clean_title = re.sub(r'<[^>]+>', '', title.group(1)).strip() if title else '未知'
        clipped = len(text) > 8000
        body = f"来源：{final_url}\n标题：{clean_title}\n正文：{text[:8000]}" + ("\n[正文已截断]" if clipped else "")
        if image_budget and _web_images_enabled():
            images, sources = _collect_observation_images(
                raw, final_url, query_hint or clean_title, image_budget)
            if images:
                return ToolResult(ok=True, text=body,
                                  data={"images": images, "image_sources": sources})
        return body
    except Exception as e: return f"网页读取失败：{type(e).__name__}: {e}"


def _builtin_fetch(url):
    """builtin 抓取的兼容入口（不带图片）；新代码请用 _fetch_page。"""
    return _fetch_page(url)

def web_research(query):
    """搜索并抓取多个公开来源，供 Agent 直接做联网研究。

    默认读取 5 个候选正文，可用 DOCMIND_WEB_RESEARCH_MAX_SOURCES 调整（1~8）。
    多轮不同查询由 Agent 决定，避免单次搜索样本不足就贸然下结论。
    """
    results = web_search(query)
    urls = re.findall(r'https?://[^\s)]+', results)
    if not urls:
        return results
    out = [f"研究主题：{query}", "搜索摘要：", results, "\n来源正文："]
    seen = set()
    # 图片只在 builtin（本地能看到 HTML）时聚合；jina/firecrawl/custom 返回纯文本。
    collect_images = _web_images_enabled() and get_web_fetch_provider() == "builtin"
    images, image_sources = [], []
    try:
        max_sources = max(1, min(8, int(os.getenv("DOCMIND_WEB_RESEARCH_MAX_SOURCES", "5"))))
    except (TypeError, ValueError):
        max_sources = 5
    for url in urls[:max_sources]:
        url = url.rstrip('.,')
        if url in seen: continue
        seen.add(url)
        if collect_images:
            remaining = _WEB_RESEARCH_IMAGE_LIMIT - len(images)
            # 预算耗尽后必须走无图分支，避免 web_fetch 自己再抓 2 张突破总上限。
            page = (_fetch_page(url, image_budget=remaining, query_hint=query)
                    if remaining > 0 else _fetch_page(url))
            if isinstance(page, ToolResult):
                for img, src in zip(page.data.get("images") or [],
                                    page.data.get("image_sources") or []):
                    if img not in images and src not in image_sources:
                        images.append(img)
                        image_sources.append(src)
                page = page.text
        else:
            page = web_fetch(url)
        out.append(page)
    conflict = _detect_source_conflicts(out[3:])
    if conflict:
        out.append("\n冲突提示（自动抽取，仅供复核）：\n" + conflict)
    text = "\n---\n".join(out)
    if images:
        return ToolResult(ok=True, text=text,
                          data={"images": images, "image_sources": image_sources})
    return text


def _detect_source_conflicts(chunks):
    """标记不同来源对带单位数字的明显分歧，不替用户裁决事实。"""
    values = {}
    rx = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(GB|MB|KB|Hz|秒|分钟|帧|%|个|层)(?!\w)", re.I)
    for chunk in chunks:
        seen = set()
        for val, unit in rx.findall(chunk or ""):
            key = unit.lower()
            item = (val, unit)
            if item not in seen:
                values.setdefault(key, set()).add(val)
                seen.add(item)
    rows = [f"单位 {unit} 出现多个数值：{', '.join(sorted(vals, key=lambda x: float(x)))}；请打开来源核对上下文。"
            for unit, vals in values.items() if len(vals) > 1]
    return "\n".join(rows)


_ALLOWED_URL_SCHEMES = ("http", "https")


def _url_scheme_ok(url):
    """只允许 http/https：白名单配 * 时也要阻止 file:// 读本地文件、gopher/ftp 等协议。"""
    try:
        scheme = (urllib.parse.urlparse(url).scheme or "").lower()
    except Exception:
        return False, ""
    return scheme in _ALLOWED_URL_SCHEMES, scheme


def _host_allowed(url):
    """按 EXTERNAL_API_ALLOWLIST 校验 url 的 host（防 SSRF）。空白名单一律拒绝。

    支持三种写法（与文档一致）：
      *                          放行任意 host（scheme 仍限 http/https）
      api.example.com            精确匹配，同时匹配其子域
      *.example.com              仅匹配子域（含多级，如 a.b.example.com），不含裸 example.com
    """
    if not EXTERNAL_API_ALLOWLIST:
        return False, "未配置 EXTERNAL_API_ALLOWLIST，dev_http_request 已禁用（请在启动环境设置白名单，如 EXTERNAL_API_ALLOWLIST=api.example.com,*.example.com）。"
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False, f"无法解析 URL：{url}"
    host = host.lower()
    for raw in EXTERNAL_API_ALLOWLIST:
        pat = (raw or "").strip().lower()
        if not pat:
            continue
        if pat == "*":
            return True, ""
        if pat.startswith("*."):
            suffix = pat[1:]  # ".example.com"
            if host.endswith(suffix) and len(host) > len(suffix):
                return True, ""
            continue
        if host == pat or host.endswith("." + pat):
            return True, ""
    return False, f"目标 host「{host}」不在 EXTERNAL_API_ALLOWLIST 白名单内，已拒绝（当前白名单：{', '.join(EXTERNAL_API_ALLOWLIST)}）。"


class _GuardRedirectHandler(urllib.request.HTTPRedirectHandler):
    """30x 跳转时对每一跳重新做 scheme + host 白名单校验。

    urllib 默认自动跟随重定向且不重新校验目标，白名单域若存在开放重定向，
    一跳即可访问 127.0.0.1 / 169.254.169.254 / 内网甚至跳到 file://，
    把响应内容回显给 Agent，等于绕过 SSRF 白名单。
    """

    # 301/303/307 在基类里都是 http_error_302 的别名；子类里重新绑定，确保全部走守卫
    def http_error_302(self, req, fp, code, msg, hdrs):
        # 注意：Python 3.13 起基类在调用 redirect_request 之前就会自行拒绝非 http(s)
        # 跳转（抛 HTTPError），这里提前校验是为了拿到统一、可读的拦截原因，
        # 并保证所有 3xx 状态码都先过本方法。
        loc = hdrs.get("location") or hdrs.get("uri") or ""
        newurl = urllib.parse.urljoin(req.full_url, loc)
        scheme_ok, _ = _url_scheme_ok(newurl)
        if not scheme_ok:
            raise urllib.error.URLError(f"重定向目标协议不允许（仅 http/https）：{newurl}")
        allowed, why = _host_allowed(newurl)
        if not allowed:
            raise urllib.error.URLError(f"重定向目标未通过白名单：{why}")
        return super().http_error_302(req, fp, code, msg, hdrs)

    http_error_301 = http_error_303 = http_error_307 = http_error_302

    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        # 纵深防御：即便未来 Python 版本改变 302 处理链，这里仍逐跳复核
        scheme_ok, _ = _url_scheme_ok(newurl)
        if not scheme_ok:
            raise urllib.error.URLError(f"重定向目标协议不允许（仅 http/https）：{newurl}")
        allowed, why = _host_allowed(newurl)
        if not allowed:
            raise urllib.error.URLError(f"重定向目标未通过白名单：{why}")
        return super().redirect_request(req, fp, code, msg, hdrs, newurl)


def dev_http_request(arg):
    """让 Agent 调用你自己的外部业务 API（REST/JSON）。

    受 EXTERNAL_API_ALLOWLIST 域名白名单约束（防止对内网/元数据地址做 SSRF），
    未配置白名单时本工具拒绝任何请求。输入（多行 key: value）：
        url: <必填，完整 URL>
        method: <GET|POST|PUT|PATCH|DELETE，默认 GET>
        timeout: <秒，默认 15>
        headers: <可选，单行 JSON 对象，如 {"Authorization":"Bearer x"}>
        body: <可选，请求体；与 method 配合，POST/PUT/PATCH 常用；可多行>
    返回：HTTP 状态码 + 响应头(部分) + 截断后的响应体（前 4000 字）。网络/解析错误会说明原因。
    """
    arg = (arg or "").lstrip("\n")
    keys = ["url", "method", "timeout", "headers", "body"]
    spans = []
    for key in keys:
        for m in re.finditer(r"^\s*" + key + r"\s*:\s*", arg, re.M):
            spans.append((m.start(), m.end(), key))
    spans.sort()
    fields = {}
    for i, (s, e, key) in enumerate(spans):
        val_end = spans[i + 1][0] if i + 1 < len(spans) else len(arg)
        val = arg[e:val_end]
        if val.startswith("\n"):
            val = val[1:]
        if val_end < len(arg):
            val = val.rstrip("\n")
        fields[key] = val

    url = (fields.get("url") or "").strip()
    if not url:
        return "参数缺失：请提供 url: <完整 URL>。"
    scheme_ok, scheme = _url_scheme_ok(url)
    if not scheme_ok:
        return f"安全拦截：仅允许 http/https 协议，拒绝「{scheme or '未知'}」。"
    ok, why = _host_allowed(url)
    if not ok:
        return f"安全拦截：{why}"

    method = (fields.get("method") or "GET").strip().upper()
    if method not in ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
        return f"不支持的 HTTP 方法：{method}。"
    try:
        timeout = float((fields.get("timeout") or "15").strip())
    except ValueError:
        return "timeout 参数不是合法数字（秒）。"
    timeout = max(1.0, min(timeout, 60.0))

    headers = {}
    raw_h = (fields.get("headers") or "").strip()
    if raw_h:
        try:
            headers = json.loads(raw_h)
            if not isinstance(headers, dict):
                return "headers 必须是 JSON 对象（如 {\"Authorization\":\"Bearer x\"}）。"
        except Exception as e:  # noqa: BLE001
            return f"headers 解析失败（需单行 JSON 对象）：{e}"

    body = fields.get("body")
    data = None
    if body is not None and body != "" and method in ("POST", "PUT", "PATCH"):
        data = body.encode("utf-8")
        headers.setdefault("Content-Type", "application/json")

    try:
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        # 使用带跳转守卫的 opener：302 每一跳都重过 scheme + host 白名单
        opener = urllib.request.build_opener(_GuardRedirectHandler)
        with opener.open(req, timeout=timeout) as r:
            status = r.status
            resp_body = r.read().decode("utf-8", "replace")
            ctype = r.headers.get("content-type", "")
    except urllib.error.HTTPError as e:
        try:
            resp_body = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            resp_body = ""
        return f"HTTP {e.code} {e.reason}\n{resp_body[:4000]}"
    except Exception as e:  # noqa: BLE001
        return f"请求失败: {type(e).__name__}: {e}"

    preview = resp_body[:4000]
    tail = "" if len(resp_body) <= 4000 else f"\n…（截断，共 {len(resp_body)} 字）"
    return f"HTTP {status}  content-type: {ctype}\n{preview}{tail}"


def python_exec(code):
    """在受限子进程中执行 Python 代码，返回 stdout/stderr（截断到 1500 字）。

    用于数值计算、数据处理、文本变换等"让 agent 真正动手"的场景。
    超时 12 秒；这是本地开发工具，以当前用户权限运行，请勿用于不可信代码。
    """
    code = (code or "").strip()
    # 清理模型可能包裹的代码围栏 / 语言提示词，避免把 "```python" 当代码执行
    code = re.sub(r"^```(?:python|py)?\s*", "", code)
    code = re.sub(r"\s*```$", "", code)
    code = re.sub(r"^\s*(?:python|py)\s*$", "", code, flags=re.I | re.M)  # 去掉独立的提示行
    code = code.strip().strip("`").strip()
    if not code:
        return "未提供有效代码。"
    # 打包版 sys.executable 是 DocMind.exe（onedir 内无 python.exe），
    # 不能拿它当解释器跑代码（只会再启动一个应用实例）。
    if getattr(sys, "frozen", False):
        return "分发版（DocMind.exe）未内置 Python 解释器，python_exec 仅在源码/venv 环境可用。"
    # 弱模型常把多行代码写成单行、换行用字面量 \n/\t（Action Input 是单行字段）；
    # 首次编译失败且代码里没有真实换行时，反转义一次再执行（正常代码不受影响）。
    try:
        compile(code, "<python_exec>", "exec")
    except SyntaxError:
        if "\\n" in code and "\n" not in code:
            code = code.replace("\\n", "\n").replace("\\t", "\t")
    # 已索引代码库时在代码根目录内执行：脚本里的相对路径（如 open("app/src/...")）
    # 才能按项目语义解析；未配置代码库时维持旧行为（服务端目录）。
    cwd = _get_code_root() or os.path.dirname(__file__)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=12,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired:
        return "代码执行超时（>12s），可能被死循环阻塞。"
    except Exception as e:  # noqa: BLE001
        return f"执行失败: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if not out.strip():
        return "（代码已执行，无输出）"
    return out[:1500] + ("…" if len(out) > 1500 else "")


# ---------------------------------------------------------------------------
# 视频提示词生成工具：按 MiniMax H3 的"三段结构"把创意描述转为可直接粘贴进
# ComfyUI 节点 (MiniMaxH3ImageToVideo) 的结构化提示词。
# 设计说明：三段骨架（integrated_multimodal_description / overall_soundscape /
# non_diegetic_music）由本工具确定性拼装，保证永远符合 H3 格式；LLM 仅用于润色
# Shot 1 的英文描述，失败则回退模板句。这样无论模型强弱都能产出可用提示词。
# ---------------------------------------------------------------------------
def _enrich_shot1(spec):
    """用 LLM 把中文创意扩写成一句英文分镜描述；失败时回退到确定性模板句。"""
    provider = get_runtime("llm_provider") or ""
    if provider and provider != "mock":
        try:
            from llm import LLMClient

            sys_p = (
                "把下面中文创意扩写成一句英文分镜描述（含主体动作、镜头运动、物理力），"
                "只输出这一句英文，不要解释，不超过 40 词。"
            )
            client = LLMClient()
            out = client.chat(
                [{"role": "system", "content": sys_p}, {"role": "user", "content": spec}],
                stream=False,
            )
            out = (out or "").strip().strip("`").strip()
            # 防御：若模型没按要求只给一句英文，回退模板
            if out and "integrated_multimodal" not in out and len(out) < 400:
                return out
        except Exception:  # noqa: BLE001
            pass
    return (
        f"{spec}。镜头缓慢推近，机位与主体视线平齐，"
        "注意点名物理力（重力/风/液体/布料张力）让画面更真实。"
    )


def gen_video_prompt(spec):
    """按 MiniMax H3 的三段结构，把一段创意描述生成结构化视频提示词。

    结构（integrated_multimodal_description / overall_soundscape / non_diegetic_music）
    由本工具确定性拼装，保证永远符合 H3 格式；LLM 仅用于润色 Shot 1 的英文描述。
    输出可直接粘贴进 ComfyUI 的 MiniMaxH3ImageToVideo 节点的 prompt 字段。
    """
    spec = (spec or "").strip().strip("'\"")
    if not spec:
        return "未提供创意描述，请描述你想生成的画面（主体/场景/动作/氛围）。"

    shot1 = _enrich_shot1(spec)

    # 关键词驱动的声音设计（diegetic），让画内音贴合场景
    amb = []
    if "雨" in spec:
        amb.append("steady rain")
    if "夜" in spec or "晚" in spec:
        amb.append("distant night traffic")
    if "风" in spec:
        amb.append("wind moving through the scene")
    if "海" in spec or "浪" in spec:
        amb.append("waves on the shore")
    if "城" in spec or "市" in spec:
        amb.append("low city hum")
    if "森" in spec or "林" in spec:
        amb.append("rustling leaves")
    if "雪" in spec:
        amb.append("soft snowfall")
    if not amb:
        amb.append("ambient room tone")
    sound = "画内音逐一点名：" + ", ".join(amb) + "。"

    music = (
        "配乐：pulsing synth arpeggio at a steady mid tempo over sustained low bass, "
        "with a filtered swell that builds then drops."
    )

    return (
        "integrated_multimodal_description:\n"
        f"[Shot 1] Live-action, cinematic. {shot1}\n"
        "[Shot 2] At 00:03.000，承接上一镜，引用 Shot 1 的角色与位置，推进后续动作与状态变化。\n"
        "overall_soundscape:\n"
        f"{sound}\n"
        "non_diegetic_music:\n"
        f"{music}\n\n"
        "（约束：无负面提示词；FPS 固定 24；时长 4-15s；提示词 < 7000 字符且需 >= 100 词则继续扩写；"
        "不说话的角色写 lips stay closed。）"
    )


# ---------------------------------------------------------------------------
# 素材筛选工具：在本地精选素材目录（Kenney CC0 等）中按关键词 / 类型 / 许可筛选。
# 设计说明：
#   - 素材库（Kenney / OpenGameArt / itch）没有稳定可用的公开 API，且多被
#     Cloudflare 保护，直接爬取易失败、也不礼貌。业界做法（如 Arcane Assets
#     MCP）同样是维护一份本地 / 远程的素材清单 JSON，由 Agent 在其上做检索。
#   - 因此这里用一份人工精选 + 持续可扩充的 assets_catalog.json，配合本工具的
#     关键词展开与打分排序，实现「根据提问筛选素材」。
# ---------------------------------------------------------------------------
_CATALOG_PATH = os.path.join(os.path.dirname(__file__), "assets_catalog.json")

# 中文关键词 -> 英文检索词 的同义展开表，使中文提问也能命中英文标签。
_CN_SYNONYMS = {
    "角色": "character sprite",
    "精灵": "sprite character",
    "人物": "character",
    "怪物": "monster creature",
    "动物": "animal",
    "龙": "dragon",
    "骑士": "knight",
    "巫师": "wizard",
    "战士": "knight warrior",
    "像素": "pixel",
    "地形": "tileset tile",
    "地块": "tileset tile",
    "贴图": "tile texture",
    "地图": "tile map",
    "等距": "isometric",
    "模型": "model 3d",
    "三维": "3d model",
    "界面": "ui interface",
    "图标": "icon ui",
    "按钮": "ui button",
    "音效": "sfx sound audio",
    "声音": "sound audio",
    "音乐": "music",
    "字体": "font",
    "特效": "particle vfx effect",
    "粒子": "particle",
    "动画": "animation",
    "战斗": "battle",
    "地牢": "dungeon",
    "城镇": "town",
    "小镇": "town",
    "城市": "city",
    "太空": "space",
    "宇宙": "space",
    "射击": "shooter",
    "坦克": "tank",
    "飞船": "ship",
    "平台": "platformer",
    "美术": "art asset",
    "资源": "asset",
    "素材": "asset",
}


def _expand_query(q):
    """把用户提问展开成一组检索 token（英文/数字原样保留，中文按同义词展开）。"""
    q = (q or "").lower()
    tokens = re.findall(r"[a-z0-9]+", q)  # 抽取英文 / 数字片段，中文串里也抓得到
    for cn, en in _CN_SYNONYMS.items():
        if cn in q:
            tokens.extend(en.split())
    # 去重保序
    seen, out = set(), []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            out.append(t)
    return out


def search_assets(query, asset_type=None, license=None):
    """在精选游戏素材目录（Kenney CC0 等）中按关键词/类型/许可筛选素材。

    返回命中条目的可读列表；按关键词命中数量打分排序，最多 8 条。
    asset_type 例：'2d-sprites' / 'tilesets' / 'ui' / 'audio' / 'fonts' / '3d'
    license    例：'CC0' / 'CC-BY' / 'CC-BY-SA-3.0'
    """
    try:
        with open(_CATALOG_PATH, encoding="utf-8") as f:
            catalog = json.load(f)
    except Exception:
        return "未找到相关素材。"

    tokens = _expand_query(query)
    type_filter = (asset_type or "").lower().strip()
    lic_filter = (license or "").strip()

    scored = []
    for item in catalog:
        cat = (item.get("category") or "").lower()
        lic = item.get("license") or ""
        if type_filter and cat != type_filter:
            continue
        if lic_filter and lic != lic_filter:
            continue
        hay = " ".join([
            item.get("name", ""),
            " ".join(item.get("tags", [])),
            item.get("desc", ""),
            item.get("category", ""),
            lic,
        ]).lower()
        if tokens:
            score = sum(1 for t in tokens if t in hay)
            if score == 0:
                continue
        else:
            score = 1
        scored.append((score, item))

    if not scored:
        return "未找到相关素材。"
    scored.sort(key=lambda x: -x[0])
    lines = []
    for _, it in scored[:8]:
        lines.append(
            f"· {it['name']} ［{it['category']}｜{it['license']}］ {it.get('desc', '')}  → {it.get('url', '')}"
        )
    return "\n".join(lines)


def set_embedding_provider(provider):
    """切换检索用的 embedding provider（如 local <-> qwen），并清空缓存。"""
    set_runtime("embedding_provider", provider)
    global _emb
    _emb = None


# ---------------------------------------------------------------------------
# 代码工具：让 Agent 能检索 / 阅读 / 搜索已索引的代码库（代码问答模式）。
# 所有路径操作都限定在 code_root 之内，避免越界读取本机其它文件。
# ---------------------------------------------------------------------------
def _get_code_root():
    return (get_runtime("code_root") or CODE_ROOT or "").strip()


def _region_write_allowed(target):
    """When regions are configured, ordinary write tools cannot bypass region tools."""
    if get_runtime("region_edit_context"):
        return True
    root = _get_code_root()
    if not root or not os.path.isfile(os.path.join(root, "regions.json")):
        return True
    try:
        from regions import get_region_map
        target = os.path.realpath(target)
        for meta in get_region_map(root).values():
            base = os.path.realpath(os.path.join(root, meta["dir"]))
            if target == base or target.startswith(base + os.sep):
                return False
    except Exception:
        return False
    return True


# 记录 Agent 本次会话内已用 read_file 读过的文件（绝对路径，normcase 归一），
# 用于 apply_edit 的"先读后写"安全护栏：未确认过内容的文件不允许整体重写。
_READ_FILES = set()
_REGION_LOCKS = {}
_REGION_LOCKS_GUARD = threading.Lock()


def _region_lock(key):
    with _REGION_LOCKS_GUARD:
        return _REGION_LOCKS.setdefault(key, threading.RLock())


def _resolve_in_root(path):
    """把 path 解析为 code_root 内的绝对路径；越界返回 (None, root_abs)。"""
    root = _get_code_root()
    root_abs = os.path.normpath(root)
    target = os.path.normpath(path if os.path.isabs(path) else os.path.join(root_abs, path))
    if not (target == root_abs or target.startswith(root_abs + os.sep)):
        alt = os.path.normpath(os.path.join(root_abs, path.lstrip("./\\")))
        if os.path.isfile(alt) and (alt == root_abs or alt.startswith(root_abs + os.sep)):
            return alt, root_abs
        return None, root_abs
    return target, root_abs


def _clean_symbol(p):
    """从混入自然语言的输入里提取首个「代码标识符」token。

    弱模型常把工具输入写成「seekTo 进行进度跳转」这种「符号 + 中文描述」的形式，
    直接当正则/检索词会匹配失败。这里在检测到中文时只取第一个 ASCII 标识符
    （如 seekTo）；纯 ASCII 输入（可能是合法正则）原样保留。
    """
    p = (p or "").strip()
    if not p:
        return p
    if re.search(r"[\u4e00-\u9fff]", p):
        m = re.search(r"[A-Za-z_]\w*", p)
        if m:
            return m.group(0)
    return p


def _clean_search_query(q):
    """语义检索 query 只"剥壳"、不抽符号：保留多词自然语言/中英混合的完整语义。

    _clean_symbol 会把「collision 碰撞检测逻辑」缩成单个标识符，那是给 grep 正则用的；
    语义向量检索需要完整 query（如「score combo high score save」是合法英文短句）。
    这里仅去掉弱模型常误带的 query:/path:/关键词: 前缀与成对围栏、引号。
    """
    q = (q or "").strip()
    q = re.sub(
        r"^(?:query|q|path|关键词|检索词|搜索词)\s*[:：]\s*",
        "",
        q,
        flags=re.IGNORECASE,
    ).strip()
    if len(q) >= 2 and q[0] == q[-1] and q[0] in "`'\"":
        q = q[1:-1].strip()
    return q


# 符号种类 -> 检索结果标签动词（function 按语言再区分 def/func）
_SYMBOL_KW = {
    "class": "class", "signal": "signal", "enum": "enum",
    "const": "const", "var": "var",
}


def search_code(query):
    """在已索引的源代码/配置中检索相关函数、类、配置片段。"""
    if not _get_code_root():
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录后再问代码相关问题。"
    query = _clean_search_query(query)
    collection = get_runtime("code_collection") or CODE_COLLECTION_NAME
    retrieved = get_retriever(collection=collection, top_k=TOP_K,
                               embedding_client=_get_emb()).invoke(query)
    docs = [item.page_content for item in retrieved]
    metas = [dict(item.metadata or {}) for item in retrieved]
    docs, metas = _dedup_docs(docs, metas)
    if not docs:
        return "代码库未找到相关内容，建议改用 grep 搜索关键词或 read_file 查看具体文件。"
    out = []
    for d, m in zip(docs, metas):
        src = m.get("source", "?")
        sym = m.get("symbol", "")
        lang = m.get("lang", "")
        kind = m.get("kind", "code")
        doc = m.get("doc", "")
        sl, el = m.get("start_line"), m.get("end_line")
        loc = f"{src}:L{sl}" + (f"-L{el}" if el and el != sl else "") if sl else src
        kw = _SYMBOL_KW.get(kind)
        verb = ("def" if lang == "python" else "func") if kind == "function" else kw
        if sym and verb:
            label = f"{loc} › {verb} {sym}"
        elif sym:
            label = f"{loc} › {sym}"
        else:
            label = loc
        if lang:
            label += f" ({lang})"
        shown = d if len(d) <= 600 else d[:600].rstrip() + "\n…（下略）"
        # 给片段逐行标行号（锚定 start_line），模型可直接引用精确行
        if sl:
            numbered = []
            for i, ln in enumerate(shown.split("\n")):
                if ln == "…（下略）":
                    numbered.append(ln)
                else:
                    numbered.append(f"{sl + i:>5}| {ln}")
            shown = "\n".join(numbered)
        if doc:
            shown = f"# 文档: {doc.splitlines()[0]}\n" + shown
        out.append(f"[{label}]\n{shown}")
    return "\n---\n".join(out)


def read_file(path):
    """读取代码库中的文件内容（path 为相对 code_root 的路径或文件名）。

    大文件默认只给前 4000 字；可在输入里附 `start: <1基行号>` 与可选 `end: <行号>`
    只看某个区间（如枚举/方法所在行段），格式：路径换行后接 start:/end: 两行。
    """
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    root_abs = os.path.normpath(root)
    start_line = end_line = None
    ms = re.search(r"(?:^|\n)\s*start\s*[:：]\s*(\d+)", path)
    me = re.search(r"(?:^|\n)\s*end\s*[:：]\s*(\d+)", path)
    cuts = []
    if ms:
        start_line = max(1, int(ms.group(1)))
        cuts.append(ms.start())
    if me:
        end_line = max(1, int(me.group(1)))
        cuts.append(me.start())
    if cuts:
        path = path[:min(cuts)]
    path = path.strip()
    if start_line and end_line and end_line < start_line:
        end_line = start_line + 200
    target = os.path.normpath(path if os.path.isabs(path) else os.path.join(root_abs, path))
    # 路径越界防护：只允许读取 code_root 目录内的文件
    if not (target == root_abs or target.startswith(root_abs + os.sep)):
        alt = os.path.normpath(os.path.join(root_abs, path.lstrip("./\\")))
        if os.path.isfile(alt) and (alt == root_abs or alt.startswith(root_abs + os.sep)):
            target = alt
        else:
            return f"拒绝访问：{path} 不在代码根目录内。"
    if not os.path.isfile(target):
        return f"文件不存在：{path}"
    try:
        with open(target, encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:  # noqa: BLE001
        return f"读取失败: {e}"
    # 记录已读，供 apply_edit 的"先读后写"护栏使用
    _READ_FILES.add(os.path.normcase(target))
    rel = os.path.relpath(target, root_abs)
    if start_line:
        all_lines = content.splitlines(keepends=True)
        if not end_line:
            end_line = start_line + 199  # 区间默认 200 行
        end_line = min(end_line, len(all_lines))
        picked = all_lines[start_line - 1:end_line]
        shown = "".join(f"{i}: {ln}" for i, ln in enumerate(picked, start=start_line))
        return f"=== {rel}（第 {start_line}-{end_line} 行，共 {len(all_lines)} 行）===\n{shown}"
    if len(content) > 4000:
        content = content[:4000] + "\n…（已截断，仅显示前 4000 字；需要后续段落请用 start:/end: 指定行号）"
    return f"=== {rel} ===\n{content}"


_RE_GREP_SCOPE_LINE = re.compile(r"^\s*path\s*[:：]\s*(.+?)\s*$", re.I)
_RE_GREP_SCOPE_INLINE = re.compile(r"\s*[,，]\s*path\s*[:：]\s*(.+?)\s*$", re.I)
_RE_GREP_PATTERN_KEY = re.compile(r"^\s*(?:pattern|regex|p)\s*[:：]\s*(.+)$", re.I)


def _strip_arg_quotes(text):
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        return text[1:-1].strip()
    return text


def read_external_file(path):
    """读取【项目外】白名单目录内的文件（受控越界读）。

    仅允许读取 DOCMIND_EXTERNAL_DIRS（分号分隔的绝对目录）之内的文件；其它路径一律拒绝。
    用于用户明确授权让 AI 访问代码库之外的资料（如另一个项目、下载目录里的文档）。
    输入 path 为文件绝对路径；返回文件内容（截断到 4000 字）。
    """
    if not external_access_high():
        return ("当前为安全模式（仅限项目内）。读取项目外文件需要在「模型设置」中"
                "切换为高权限模式（high）后方可执行。")
    dirs_raw = (os.getenv("DOCMIND_EXTERNAL_DIRS") or "").strip()
    if not dirs_raw:
        return ("未配置允许访问的外部目录。如需让 AI 读取项目外文件，请设置环境变量 "
                "DOCMIND_EXTERNAL_DIRS 为分号分隔的绝对路径白名单，例如 "
                "D:/OtherProject;D:/Downloads。未配置时出于安全默认拒绝越界读取。")
    allowed = [os.path.normpath(d) for d in re.split(r"[;；]", dirs_raw) if d.strip()]
    if not allowed:
        return "未配置允许访问的外部目录（DOCMIND_EXTERNAL_DIRS 为空）。"
    target = os.path.normpath(os.path.abspath(path))
    ok = any(target == d or target.startswith(d + os.sep) for d in allowed)
    if not ok:
        return f"拒绝访问：{path} 不在允许访问的外部目录白名单内（{', '.join(allowed)}）。"
    if not os.path.isfile(target):
        return f"文件不存在：{path}"
    try:
        with open(target, encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except Exception as e:  # noqa: BLE001
        return f"读取失败: {e}"
    if len(content) > 4000:
        content = content[:4000] + "\n…（已截断，仅显示前 4000 字）"
    _READ_EXTERNAL_FILES.add(os.path.normcase(target))
    return f"=== {target} ===\n{content}"


# --- 受控越界读写：仅在「高权限模式」+ DOCMIND_EXTERNAL_DIRS 白名单内生效 ---
# 安全模式下以下三个工具一律拒绝（见 _resolve_external_path 的第一道门禁）。
# 设计意图：让用户可授权 AI 访问其它项目并增删改查，但路径被钉死在白名单目录，
# 既不会误伤系统目录，也不会因 mode 误开而"无限制越界"。
_READ_EXTERNAL_FILES = set()  # 本次会话内已用 read_external_file 读过的文件（整体重写护栏）


def _resolve_external_path(path):
    """解析受控越界路径：返回 (target, err)。err 非空即拒绝原因。

    门禁链：① 必须处于高权限模式（external_access_high）；② 必须配置
    DOCMIND_EXTERNAL_DIRS 白名单；③ 目标必须落在白名单目录内。任一不满足返回 err。
    """
    if not external_access_high():
        return None, ("当前为安全模式（仅限项目内）。对其他项目增删改查需要在「模型设置」中"
                      "切换为高权限模式（high）后方可执行。")
    dirs_raw = (os.getenv("DOCMIND_EXTERNAL_DIRS") or "").strip()
    if not dirs_raw:
        return None, ("未配置允许越界的外部目录白名单（DOCMIND_EXTERNAL_DIRS 为空）。"
                      "高权限模式下也必须在环境变量中显式列出允许访问的目录，"
                      "例如 D:/OtherProject;D:/Downloads。")
    allowed = [os.path.normpath(d) for d in re.split(r"[;；]", dirs_raw) if d.strip()]
    target = os.path.normpath(os.path.abspath(path))
    ok = any(target == d or target.startswith(d + os.sep) for d in allowed)
    if not ok:
        return None, f"拒绝访问：{path} 不在允许越界的目录白名单内（{', '.join(allowed)}）。"
    return target, None


def create_external_file(arg):
    """在白名单目录内【新建】文件（不覆盖已有文件），受控越界写。格式同 create_file。

    门禁：高权限模式 + DOCMIND_EXTERNAL_DIRS 白名单。输入 path: <绝对路径>，
    new_text: <内容>。护栏：不覆盖已存在；200KB 上限；.py 语法校验；「人工确认」开启时暂存。
    """
    path, _, content = _parse_edit_input(arg)
    if not path:
        return "参数缺失：请提供 path: <文件绝对路径> 与 new_text: <文件内容>。"
    if content is None:
        return "参数缺失：请提供 new_text: <文件内容（可多行）>。"
    target, err = _resolve_external_path(path)
    if err:
        return err
    if os.path.exists(target):
        return f"文件已存在：{path}（create_external_file 不覆盖已有文件；修改请用 edit_external_file）。"
    if len(content.encode("utf-8", "ignore")) > _APPLY_MAX_BYTES:
        return f"拒绝写入：新文件大小超过上限（{_APPLY_MAX_BYTES // 1024}KB）。"
    ext = os.path.splitext(target)[1].lower()
    if ext == ".py":
        import tempfile
        import py_compile

        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(content)
                tmp = tf.name
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            return f"语法校验失败，已取消创建：\n{e.msg}"
        except Exception as e:  # noqa: BLE001
            return f"语法校验异常，已取消创建：{e}"
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
    if _edit_confirm_on():
        pid = stage_edit("create_file", target, path, None, content,
                         "新建外部文件：\n" + (content[:2000] + ("…" if len(content) > 2000 else "")))
        return f"待人工确认 #{pid}（未写入）。确认后才会真正创建文件。\n新建外部文件：{path}"
    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            return f"创建目录失败: {e}"
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:  # noqa: BLE001
        return f"写入失败: {e}"
    _READ_EXTERNAL_FILES.add(os.path.normcase(target))
    return f"已在白名单目录内创建 {path}（{len(content.encode('utf-8', 'ignore'))} 字节）。"


def edit_external_file(arg):
    """在白名单目录内修改【已存在】文件，受控越界写。格式同 apply_edit。

    门禁：高权限模式 + 白名单。两种用法：
      · 局部替换：path + old_text + new_text（唯一定位，匹配 0/>1 处拒绝）；
      · 整体重写：path + new_text（须先用 read_external_file 读过该文件确认内容）。
    护栏：200KB 上限；.py 语法校验失败回滚；「人工确认」开启时暂存。
    """
    path, old_text, new_text = _parse_edit_input(arg)
    if not path:
        return "参数缺失：请提供 path: <文件绝对路径> 与 new_text: <新内容>。"
    if new_text is None:
        return "参数缺失：请提供 new_text: <新内容>（局部替换还需 old_text: <旧片段>）。"
    target, err = _resolve_external_path(path)
    if err:
        return err
    if not os.path.isfile(target):
        return f"文件不存在：{path}（edit_external_file 只修改已存在文件）。"
    try:
        with open(target, encoding="utf-8", errors="ignore") as f:
            old_content = f.read()
    except Exception as e:  # noqa: BLE001
        return f"读取原文件失败: {e}"
    if old_text is not None:
        if old_text == "":
            return "old_text 为空，无法确定替换范围（整体重写请省略 old_text；局部替换请提供精确片段）。"
        cnt = old_content.count(old_text)
        if cnt == 0:
            return "安全限制：未找到 old_text 的匹配，可能内容已变化。请重新 read_external_file 确认当前内容后再改。"
        if cnt > 1:
            return f"安全限制：old_text 匹配到 {cnt} 处，存在歧义。请提供更精确的 old_text（含前后上下文）。"
        new_content = old_content.replace(old_text, new_text, 1)
    else:
        if os.path.normcase(target) not in _READ_EXTERNAL_FILES:
            return ("安全限制：整体重写前请先调用 read_external_file 读取该文件确认内容，"
                    "或改用 old_text 参数做局部安全替换。")
        new_content = new_text
    if len(new_content.encode("utf-8", "ignore")) > _APPLY_MAX_BYTES:
        return f"拒绝写入：新文件大小超过上限（{_APPLY_MAX_BYTES // 1024}KB）。"
    ext = os.path.splitext(target)[1].lower()
    if ext == ".py":
        import tempfile
        import py_compile

        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(new_content)
                tmp = tf.name
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            return f"语法校验失败，已取消写入（原文件未改动）：\n{e.msg}"
        except Exception as e:  # noqa: BLE001
            return f"语法校验异常，已取消写入（原文件未改动）：{e}"
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
    nbytes = len(new_content.encode("utf-8", "ignore"))
    summary = _diff_summary(old_content, new_content)
    if _edit_confirm_on():
        pid = stage_edit("apply_edit", target, path, old_content, new_content, summary)
        return f"待人工确认 #{pid}（未写入）。确认后才会真正修改文件。\n{summary}"
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:  # noqa: BLE001
        return f"写入失败: {e}"
    return f"已写入白名单目录内文件 {path}（{nbytes} 字节）。\n{summary}"


def delete_external_file(arg):
    """删除白名单目录内的【文件】（危险操作，受控越界删）。

    门禁：高权限模式 + 白名单。输入 path: <绝对路径> 且必须含 `confirm: yes`
    明确确认（防误删）。只删文件不删目录。「人工确认」开启时改为暂存待批准。
    """
    # delete 是单行命令式输入（path: 一行 + confirm: yes 一行），用独立解析避免
    # `confirm:` 被 _parse_edit_input 误并入 path 值（它只认 path/old_text/new_text）。
    m = re.search(r"(?im)^\s*path\s*:\s*(.+?)\s*$", arg or "")
    path = m.group(1).strip() if m else ""
    if not path:
        return "参数缺失：请提供 path: <文件绝对路径> 以及 confirm: yes。"
    target, err = _resolve_external_path(path)
    if err:
        return err
    if not re.search(r"(?im)^\s*confirm\s*:\s*yes\b", arg or ""):
        return "删除是危险操作：请在输入中加入 `confirm: yes` 以明确确认删除。"
    if os.path.isdir(target):
        return f"拒绝：{path} 是目录，delete_external_file 只删除单个文件。"
    if not os.path.isfile(target):
        return f"文件不存在：{path}"
    if _edit_confirm_on():
        pid = stage_edit("delete_external_file", target, path, None, "", f"删除外部文件：{path}")
        return f"待人工确认 #{pid}（未删除）。确认后才会真正删除文件：{path}"
    try:
        os.remove(target)
    except Exception as e:  # noqa: BLE001
        return f"删除失败: {e}"
    return f"已删除白名单目录内文件 {path}。"


def grep(pattern):
    """在代码库中按正则搜索文本/符号，返回匹配的文件路径与行号。

    可在输入末尾用 path: 限定搜索范围（相对 code_root 的文件或目录），避免全仓扫描：
      某正则
      path: app/src/main/java/.../A.java
    或一行内：某正则, path: app/src/main/java/...（目录）
    """
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    # 解析可选 path: 作用域（独立行优先），剩余文本才是正则
    scope_rel = None
    kept = []
    for i, ln in enumerate((pattern or "").splitlines()):
        ms = _RE_GREP_SCOPE_LINE.match(ln)
        if ms and i >= 1:
            scope_rel = _strip_arg_quotes(ms.group(1))
        else:
            kept.append(ln)
    pattern = "\n".join(kept).strip()
    mi = _RE_GREP_SCOPE_INLINE.search(pattern)
    if mi:
        scope_rel = _strip_arg_quotes(mi.group(1))
        pattern = pattern[:mi.start()].strip()
    mp = _RE_GREP_PATTERN_KEY.match(pattern)
    if mp and "\n" not in pattern:
        pattern = _strip_arg_quotes(mp.group(1))
    pattern = _clean_symbol(pattern)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        return f"正则错误: {e}"
    root_abs = os.path.normpath(root)

    def _resolve_scope(rel):
        target = os.path.normpath(os.path.join(root_abs, rel))
        if not (target == root_abs or target.startswith(root_abs + os.sep)):
            alt = os.path.normpath(os.path.join(root_abs, rel.lstrip("./\\")))
            if os.path.exists(alt) and (alt == root_abs or alt.startswith(root_abs + os.sep)):
                target = alt
            else:
                return None, f"拒绝访问：{rel} 不在代码根目录内。"
        if not os.path.exists(target):
            return None, f"路径不存在：{rel}"
        return target, None

    scope_abs = None
    if scope_rel:
        scope_abs, err = _resolve_scope(scope_rel)
        if err:
            return err

    def _iter_candidate_files():
        if scope_abs and os.path.isfile(scope_abs):
            if os.path.splitext(scope_abs)[1].lower() in _CODE_EXT:
                yield scope_abs
            return
        for dp, dns, fns in os.walk(scope_abs or root_abs):
            dns[:] = [d for d in dns if d not in _SKIP_DIRS]
            for fn in fns:
                if os.path.splitext(fn)[1].lower() not in _CODE_EXT:
                    continue
                yield os.path.join(dp, fn)

    hits = []
    for fp in _iter_candidate_files():
        if os.path.getsize(fp) > 2_000_000:
            continue
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f, 1):
                    if rx.search(line):
                        hits.append(f"{os.path.relpath(fp, root_abs)}:{i}: {line.rstrip()}")
                        if len(hits) >= 40:
                            break
        except Exception:  # noqa: BLE001
            pass
        if len(hits) >= 40:
            break
    if not hits:
        scope_note = f"（范围：{scope_rel}）" if scope_rel else ""
        return f"代码库中未匹配到：{pattern}{scope_note}"
    return "\n".join(hits[:40])


# ---------------------------------------------------------------------------
# 受控写工具 apply_edit：让 Agent 能按用户指令修改 code_root 内的已存在文件，
# 但绝不越界、绝不误删、绝不写坏 .py。护栏分四层：
#   1) 路径沙箱：复用 _resolve_in_root，越界直接拒（与 read_file 同逻辑）。
#   2) 先读后写：整体重写时必须已在本次会话 read_file 过该文件；
#      或提供 old_text 做"精确局部替换"（更安全，推荐）。
#   3) 体积上限：新文件 > 200KB 拒写。
#   4) .py 语法校验：写入前 py_compile 编译，失败回滚原文件并报告。
# ---------------------------------------------------------------------------
_APPLY_MAX_BYTES = 200 * 1024  # 单文件写入上限 200KB


def _parse_edit_input(inp):
    """解析 apply_edit 的 Action Input（多行 keyed 格式），返回 (path, old_text, new_text)。

    约定格式（字段顺序无所谓，old_text / new_text 的值可写在 key: 同行或下一行，且可多行）：
        path: <相对或绝对路径>
        old_text: <可选：要被替换的精确旧片段>
        new_text: <替换后的新内容（可多行）>
    解析策略：定位三个字段标记（行首 `key:`），每个字段的值 = 标记之后到下一个字段
    标记（或文本末尾）之间的内容。只去掉字段标记紧跟的那个换行分隔符；最后一个字段
    （通常是 new_text/文件内容）保留其尾部换行，不丢失文件内容。这样无论是同行值还是
    多行值都稳。
    """
    inp = (inp or "").lstrip("\n")
    keys = ["path", "old_text", "new_text"]
    spans = []
    for key in keys:
        for m in re.finditer(r"^\s*" + key + r"\s*:\s*", inp, re.M):
            spans.append((m.start(), m.end(), key))
    spans.sort()
    fields = {}
    for i, (s, e, key) in enumerate(spans):
        val_end = spans[i + 1][0] if i + 1 < len(spans) else len(inp)
        val = inp[e:val_end]
        # 去掉标记后紧跟的那个换行分隔符（key: 与值换行的情形）
        if val.startswith("\n"):
            val = val[1:]
        # 非最后一个字段：其尾部换行是字段间分隔符，应剥掉（避免 old_text 多带空行匹配失败）
        # 最后一个字段（通常是 new_text/文件内容）：保留尾部换行，不丢失文件内容
        if val_end < len(inp):
            val = val.rstrip("\n")
        fields[key] = val
    return fields.get("path"), fields.get("old_text"), fields.get("new_text")


def _diff_summary(a, b):
    """生成简短 unified-diff 摘要（最多 30 行变更），用于回显给 Agent 与用户。"""
    if a == b:
        return "（内容无变化）"
    import difflib

    a_lines = a.splitlines()
    b_lines = b.splitlines()
    diff = list(difflib.unified_diff(a_lines, b_lines, lineterm="", n=1))
    body = [d for d in diff if not d.startswith(("---", "+++"))]
    if len(body) > 30:
        body = body[:30] + [f"...（共 {len(body)} 行变更，已截断显示前 30 行）"]
    return "Diff:\n" + "\n".join(body)


# ---------------------------------------------------------------------------
# 待确认修改暂存：当「人工确认」模式开启时，apply_edit / create_file 不直接写盘，
# 而是校验通过后暂存到内存，返回 pending id + diff，由人工在界面上确认/拒绝。
# ---------------------------------------------------------------------------
_PENDING_EDITS = {}       # id -> {kind, target, rel, old_content, new_content, diff}
_PENDING_LOCK = threading.Lock()
_pending_seq = [0]


def _edit_confirm_on():
    return edit_confirm_enabled()


def stage_edit(kind, target, rel, old_content, new_content, diff):
    """暂存一次已校验但未落盘的修改，返回 pending id。"""
    with _PENDING_LOCK:
        _pending_seq[0] += 1
        pid = str(_pending_seq[0])
        _PENDING_EDITS[pid] = {
            "kind": kind,            # "apply_edit" | "create_file"
            "target": target,        # 绝对路径
            "rel": rel,
            "old_content": old_content,
            "new_content": new_content,
            "diff": diff,
        }
        return pid


def list_pending_edits():
    with _PENDING_LOCK:
        return [
            {
                "id": pid,
                "kind": e["kind"],
                "path": e["rel"],
                "diff": e["diff"],
                "size": len(e["new_content"].encode("utf-8", "ignore")),
            }
            for pid, e in _PENDING_EDITS.items()
        ]


def confirm_edit(pid):
    """落盘一次已暂存的修改；成功返回 (True, 摘要)，失败返回 (False, 原因)。

    若失败原因是「暂存内容已失效」（文件被改动/删除/已被创建），会同时丢弃该待确认项，
    避免界面残留一个永远无法确认的条目；仅「写入 I/O 错误」会保留以便重试。
    """
    with _PENDING_LOCK:
        e = _PENDING_EDITS.get(pid)
    if not e:
        return False, f"未找到待确认修改 #{pid}（可能已确认/拒绝或重启后失效）。"
    target, kind = e["target"], e["kind"]

    def _discard(reason):
        with _PENDING_LOCK:
            _PENDING_EDITS.pop(pid, None)
        return False, reason

    try:
        if kind == "create_file":
            if os.path.exists(target):
                return _discard(f"目标文件已存在：{e['rel']}（暂存后文件被创建，为避免覆盖已取消）。")
            parent = os.path.dirname(target)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            with open(target, "w", encoding="utf-8") as f:
                f.write(e["new_content"])
            _READ_FILES.add(os.path.normcase(target))
        elif kind == "delete_external_file":
            if not os.path.isfile(target):
                return _discard(f"目标文件已不存在：{e['rel']}（暂存后已被删除，已取消）。")
            os.remove(target)
        else:  # apply_edit
            if not os.path.isfile(target):
                return _discard(f"目标文件已不存在：{e['rel']}")
            # 防「暂存后被改动」：内容不一致则拒绝，避免覆盖他人在暂存期间的修改
            try:
                with open(target, encoding="utf-8", errors="ignore") as f:
                    cur = f.read()
            except Exception as ex:  # noqa: BLE001
                return False, f"读取目标失败: {ex}"
            if cur != e["old_content"]:
                return _discard("目标文件在暂存后被修改，内容已变化；请重新 read_file 后再改。")
            with open(target, "w", encoding="utf-8") as f:
                f.write(e["new_content"])
            _READ_FILES.add(os.path.normcase(target))
    except Exception as ex:  # noqa: BLE001
        return False, f"写入/删除失败: {ex}"
    with _PENDING_LOCK:
        _PENDING_EDITS.pop(pid, None)
    if kind == "delete_external_file":
        return True, f"已删除 {e['rel']}。"
    nbytes = len(e["new_content"].encode("utf-8", "ignore"))
    return True, f"已应用修改 {e['rel']}（{nbytes} 字节）。"


def reject_edit(pid):
    with _PENDING_LOCK:
        return bool(_PENDING_EDITS.pop(pid, None))


def clear_read_files():
    """清空「已读文件」记录（重置代码库时调用，避免旧项目的读记录跨项目残留）。"""
    _READ_FILES.clear()


def apply_edit(arg):
    """受控修改代码库中的【已存在】文件。详见 _parse_edit_input 的输入格式。

    两种用法：
      · 局部安全替换：提供 path + old_text（要匹配的精确旧片段）+ new_text；
        工具在文件中唯一匹配处替换，匹配 0 处或 >1 处都会拒绝以避免歧义/误改。
      · 整体重写：只提供 path + new_text（不带 old_text），但前提是你已用
        read_file 读取过该文件（确认过当前内容）。
    护栏：只能改 code_root 内已存在文件，不能新建、不能越界；写入后 .py 会做语法
    校验，失败自动回滚。返回：已写入 / 失败原因 / diff 摘要。
    """
    path, old_text, new_text = _parse_edit_input(arg)
    if not path:
        return "参数缺失：请提供 path: <文件路径> 与 new_text: <新内容>。"
    if new_text is None:
        return "参数缺失：请提供 new_text: <新内容>（若做局部替换，还需 old_text: <旧片段>）。"

    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"

    target, root_abs = _resolve_in_root(path)
    if target is None:
        return f"拒绝写入：{path} 不在代码根目录内（禁止越界写）。"
    if not _region_write_allowed(target):
        return "拒绝写入：该路径属于已配置分区，请使用 dev_region_edit 以确保分区边界和先读后写护栏。"
    if not os.path.isfile(target):
        return f"文件不存在：{path}（apply_edit 只修改已存在文件，不会新建文件）。"

    try:
        with open(target, encoding="utf-8", errors="ignore") as f:
            old_content = f.read()
    except Exception as e:  # noqa: BLE001
        return f"读取原文件失败: {e}"

    # 决定新内容 + 局部/整体模式
    if old_text is not None:
        if old_text == "":
            return "old_text 为空，无法确定替换范围（整体重写请省略 old_text；局部替换请提供精确片段）。"
        cnt = old_content.count(old_text)
        if cnt == 0:
            return "安全限制：未找到 old_text 的匹配，可能内容已变化。请重新 read_file 确认当前内容后再改。"
        if cnt > 1:
            return f"安全限制：old_text 在文件中匹配到 {cnt} 处，存在歧义。请提供更精确的 old_text（含前后上下文）以唯一定位。"
        new_content = old_content.replace(old_text, new_text, 1)
    else:
        # 整体重写：必须先 read_file 确认过当前内容（防 Agent 凭空改写）
        if os.path.normcase(target) not in _READ_FILES:
            return ("安全限制：整体重写前请先调用 read_file 读取该文件确认当前内容，"
                    "或改用 old_text 参数做局部安全替换。")
        new_content = new_text

    # 护栏：单文件体积上限
    if len(new_content.encode("utf-8", "ignore")) > _APPLY_MAX_BYTES:
        return f"拒绝写入：新文件大小超过上限（{_APPLY_MAX_BYTES // 1024}KB）。"

    # 护栏：.py 语法校验（失败回滚，原文件不动）
    ext = os.path.splitext(target)[1].lower()
    if ext == ".py":
        import tempfile
        import py_compile

        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(new_content)
                tmp = tf.name
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            return f"语法校验失败，已取消写入（原文件未改动）：\n{e.msg}"
        except Exception as e:  # noqa: BLE001
            return f"语法校验异常，已取消写入（原文件未改动）：{e}"
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)

    rel = os.path.relpath(target, root_abs)
    nbytes = len(new_content.encode("utf-8", "ignore"))
    summary = _diff_summary(old_content, new_content)

    # 「人工确认」模式：只暂存，不落盘，返回 diff 等人工批准
    if _edit_confirm_on():
        pid = stage_edit("apply_edit", target, rel, old_content, new_content, summary)
        return f"待人工确认 #{pid}（未写入）。确认后才会真正修改文件。\n{summary}"

    # 写回
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(new_content)
    except Exception as e:  # noqa: BLE001
        return f"写入失败: {e}"

    return f"已写入 {rel}（{nbytes} 字节，路径沙箱校验通过）。\n{summary}"


# ---------------------------------------------------------------------------
# 受控"新建文件"工具 create_file：让 Agent 能在 code_root 内新增模块/分区文件，
# 但依然受沙箱、体积、语法护栏约束，且绝不覆盖已有文件（覆盖请用 apply_edit）。
# 设计意图：配合 search_code/grep，让"加新功能"走"先确认无重复 → 再新建"的
# 受控路径，而不是凭空生成导致堆叠。
# ---------------------------------------------------------------------------
def _resolve_create_path(path):
    """解析新建文件的绝对路径；越界返回 (None, root_abs)。不要求文件已存在。"""
    root = _get_code_root()
    root_abs = os.path.normpath(root)
    target = os.path.normpath(path if os.path.isabs(path) else os.path.join(root_abs, path))
    if not (target == root_abs or target.startswith(root_abs + os.sep)):
        return None, root_abs
    return target, root_abs


def create_file(arg):
    """在代码库内【新建】一个文件（不能覆盖已有文件）。详见 _parse_edit_input 输入格式。

    输入：path: <相对或绝对路径>，new_text: <文件内容（可多行）>。
    护栏：① 路径沙箱（必须落在 code_root 内）；② 不覆盖已有文件（改已有用 apply_edit）；
    ③ 父目录不存在时允许自动创建一层（仍在 code_root 内，便于新建分区）；
    ④ 单文件 200KB 上限；⑤ .py 写入前 py_compile 语法校验，失败取消创建。
    返回：已创建 / 失败原因。
    """
    path, _, content = _parse_edit_input(arg)
    if not path:
        return "参数缺失：请提供 path: <文件路径> 与 new_text: <文件内容>。"
    if content is None:
        return "参数缺失：请提供 new_text: <文件内容（可多行）>。"
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"

    target, root_abs = _resolve_create_path(path)
    if target is None:
        return f"拒绝写入：{path} 不在代码根目录内（禁止越界写）。"
    if not _region_write_allowed(target):
        return "拒绝写入：该路径属于已配置分区，请使用 dev_region_edit 以确保分区边界和先读后写护栏。"
    if os.path.exists(target):
        return f"文件已存在：{path}（create_file 不覆盖已有文件；要修改请用 apply_edit）。"

    if len(content.encode("utf-8", "ignore")) > _APPLY_MAX_BYTES:
        return f"拒绝写入：新文件大小超过上限（{_APPLY_MAX_BYTES // 1024}KB）。"

    ext = os.path.splitext(target)[1].lower()
    if ext == ".py":
        import tempfile
        import py_compile

        tmp = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8") as tf:
                tf.write(content)
                tmp = tf.name
            py_compile.compile(tmp, doraise=True)
        except py_compile.PyCompileError as e:
            return f"语法校验失败，已取消创建：\n{e.msg}"
        except Exception as e:  # noqa: BLE001
            return f"语法校验异常，已取消创建：{e}"
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)

    rel = os.path.relpath(target, root_abs)
    nbytes = len(content.encode("utf-8", "ignore"))
    preview = content if len(content) <= 2000 else content[:2000] + "\n…（已截断，完整内容 %d 字节）" % nbytes

    # 「人工确认」模式：只暂存，不落盘
    if _edit_confirm_on():
        pid = stage_edit("create_file", target, rel, None, content, "新建文件：\n" + preview)
        return f"待人工确认 #{pid}（未写入）。确认后才会真正创建文件。\n新建文件：\n{preview}"

    parent = os.path.dirname(target)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            return f"创建目录失败: {e}"

    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:  # noqa: BLE001
        return f"写入失败: {e}"

    # 自己刚创建的文件，视为已"确认内容"，允许后续整体重写而无需再 read_file
    _READ_FILES.add(os.path.normcase(target))
    return f"已创建 {rel}（{nbytes} 字节，路径沙箱校验通过）。"


# ---------------------------------------------------------------------------
# 命令执行工具 run_command：在 code_root 内跑构建/测试/项目命令，返回输出。
# 护栏：cwd 锁 code_root；危险命令黑名单拦截；超时 12s（同 python_exec）；
# stdout+stderr 合并截断 1500 字。
# ---------------------------------------------------------------------------
# 命令护栏：结构化黑名单（按可执行词）+ 可选白名单（RUN_COMMAND_ALLOW）。
# 仅为启发式防护，非 OS 级沙箱；真正的隔离需外部容器/沙箱。
_BLOCKED_CMDS = {
    # 破坏性文件/系统命令
    "rm", "del", "erase", "rd", "rmdir", "format", "shutdown", "restart", "reboot",
    "mkfs", "fdisk", "diskpart", "dd", "chkdsk", "chown", "chmod", "cacls", "icacls",
    "shred", "wipe", "cipher",
    # 网络外联 / 下载（防数据外带）
    "curl", "wget", "invoke-webrequest", "iwr", "invoke-restmethod", "irm",
    "nc", "netcat", "ncat", "telnet", "ssh", "scp", "sftp", "ftp", "certutil",
    # 脚本解释器 / 子 shell / 系统操控（可执行任意命令，绕过黑名单）
    "powershell", "pwsh", "bash", "sh", "zsh", "cmd", "reg", "wmic", "mshta",
    "rundll32", "schtasks", "wsl", "taskkill", "net",
}
# 危险子串/参数模式（命令+参数级，比单一可执行词更细）
_DANGEROUS_PATTERNS = (
    "rm -rf", "rm -r ", "rm -fr", "del /s", "del /q", "rd /s", "rmdir /s",
    "format ", "shutdown", "mkfs", "dd if=", "> /dev/sd", "> /dev/null",
    "git clean", "git reset --hard", "git push -f", "git push --force",
    "chmod -r", "chmod 777", "chmod 666", ":(){", "fork bomb",
    "python -c", "py -c", "python3 -c", "node -e", "node --eval",
    "npm install -g", "npm i -g", "pip install", "rmdir /q",
)


def _first_command_token(low):
    """提取命令首个「可执行词」（去引号/前缀/环境变量），取 basename 用于黑/白名单匹配。"""
    s = low.strip()
    s = re.sub(r"^(?:cmd(?:\.exe)?\s+/[ck]\s+)", "", s)    # cmd /c ...
    s = re.sub(r"^(?:[a-z_][a-z0-9_]*=[^ ]*\s+)+", "", s)   # VAR=value 前缀
    s = re.sub(r"^call\s+", "", s)
    m = re.match(r'"?([^"\s]+)"?', s)
    if not m:
        return ""
    tok = os.path.basename(m.group(1)).lower()
    return tok[:-4] if tok.endswith(".exe") else tok


# git 写操作子命令：必须走分区审批门禁（dev_commit / dev_commit_all / dev_rollback），
# 不能经 run_command 直接执行——否则 Agent 可绕过审批、变更集台账与回滚链，
# push 还会把代码外带，与黑名单"防数据外带"的初衷矛盾。只读子命令（status/log/diff/show 等）放行。
_GIT_OPTS_WITH_VALUE = {
    "-C", "-c", "--git-dir", "--work-tree", "--namespace",
    "--exec-path", "--super-prefix", "--list-submodules",
}
_GIT_WRITE_SUBCMDS = {
    "commit", "push", "merge", "rebase", "revert", "reset", "clean",
    "checkout", "switch", "apply", "cherry-pick", "stash", "am",
    "update-ref", "worktree",
}


def _git_subcommand(low):
    """从 `git ... <sub>` 命令中解析真正的子命令，跳过 -C <path> 等带值选项。

    识别不了（如自定义 alias git co）时返回 ''，由调用方按默认策略处理。
    """
    toks = re.sub(r"\s+", " ", low).strip().split(" ")
    if not toks or _first_command_token(low) != "git":
        return ""
    i = 1
    while i < len(toks):
        t = toks[i]
        if t in _GIT_OPTS_WITH_VALUE:
            i += 2
            continue
        if t.startswith("-"):
            i += 1
            continue
        return t
    return ""


def _cmd_is_blocked(cmd):
    """返回 (True, 原因) 表示应拦截；否则 (False, '')。"""
    low = re.sub(r"\s+", " ", cmd.lower()).strip()
    for pat in _DANGEROUS_PATTERNS:
        if pat in low:
            return True, pat
    first = _first_command_token(low)
    # git 写操作门禁优先于可选白名单：即使用户把 git 加进 RUN_COMMAND_ALLOW 也不放行
    if first == "git":
        sub = _git_subcommand(low)
        if sub in _GIT_WRITE_SUBCMDS:
            return True, (f"git {sub}（git 写操作受分区审批门禁保护，"
                          "请改用 dev_commit / dev_commit_all / dev_rollback）")
    if first in _BLOCKED_CMDS:
        return True, first
    allow = (get_runtime("run_command_allow") or os.getenv("RUN_COMMAND_ALLOW", "")).strip()
    if allow:
        allowed = {a.strip().lower() for a in allow.split(",") if a.strip()}
        if first not in allowed:
            return True, f"{first}（不在白名单内）"
    return False, ""


def run_command(cmd):
    """在代码库根目录内执行 shell 命令（如 pytest / npm run build），返回合并输出（截断 1500 字，超时 12s）。

    用于跑构建、跑测试、执行项目内命令来验证改动或查看结果。命令在 code_root 内执行。
    护栏：结构化黑名单（破坏性命令 / 网络外联 / 脚本解释器）+ 可选白名单（RUN_COMMAND_ALLOW）。
    说明：这是启发式防护，非 OS 级沙箱；真正的隔离需外部容器/沙箱。
    """
    cmd = (cmd or "").strip().strip("'\"")
    if not cmd:
        return "未提供命令。"
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    blocked, why = _cmd_is_blocked(cmd)
    if blocked:
        return f"拒绝执行：命令被安全策略拦截（命中「{why}」）。"
    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=os.path.normpath(root),
            timeout=12, capture_output=True, text=True,
        )
    except subprocess.TimeoutExpired:
        return "命令执行超时（>12s），可能被死循环或长构建阻塞；如需更长超时请分步执行。"
    except Exception as e:  # noqa: BLE001
        return f"执行失败: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    head = f"[exit code {proc.returncode}]\n"
    if not out.strip():
        return head + "（命令已执行，无输出）"
    combined = head + out
    return combined[:1500] + ("…" if len(combined) > 1500 else "")


def init_regions_tool(arg):
    """初始化「分区开发」结构：在代码库根目录建 assets/values/bugs/behaviors 四个独立子目录，
    各 git init 独立仓库，并生成 DEV_INDEX.md 与 DOCMIND_RULES.md（分区契约，会被注入 Agent 系统提示）。
    用于游戏等分工开发，防止代码堆叠。输入留空即可。"""
    from regions import init_regions
    ok, msg = init_regions()
    return msg


# ---------------------------------------------------------------------------
# 分区开发工具层（2.0）：分区内受控读写 / 分区校验 / 跨区安全搬移 / 提交与变更集回滚。
# 所有路径都限定在对应分区子目录内，越区写被拦截（强化"防堆叠/防混乱"）。
# 写操作复用 apply_edit / create_file 的全部护栏（先读后写、.py 语法校验、200KB 上限、人工确认）。
# ---------------------------------------------------------------------------
def _parse_keyed(arg, keys):
    """通用 keyed 解析：从多行文本里提取指定字段（region/path/old_text/new_text/...）。
    字段值可同行或换行续写、可多行；用于分区工具输入解析，逻辑与 _parse_edit_input 一致。"""
    arg = (arg or "").lstrip("\n")
    spans = []
    for key in keys:
        for m in re.finditer(r"^\s*" + re.escape(key) + r"\s*:\s*", arg, re.M):
            spans.append((m.start(), m.end(), key))
    spans.sort()
    fields = {}
    for i, (s, e, key) in enumerate(spans):
        val_end = spans[i + 1][0] if i + 1 < len(spans) else len(arg)
        val = arg[e:val_end]
        if val.startswith("\n"):
            val = val[1:]
        if val_end < len(arg):
            val = val.rstrip("\n")
        fields[key] = val
    return fields


def _require_regions():
    """返回 (root, rmap) 或 (None, error_msg)。rmap = {key: region_meta}。"""
    root = _get_code_root()
    if not root:
        return None, "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    try:
        from regions import get_region_map
        rmap = get_region_map(root)
    except Exception as e:  # noqa: BLE001
        return None, f"读取分区配置失败: {e}"
    if not rmap:
        return None, "尚未初始化分区（regions.json 为空）。请先调用 init_regions 初始化分区开发结构。"
    return (root, rmap), None


def _resolve_region_path(root, rmap, region_key, path):
    """把 path 解析到某分区目录内的绝对路径；越界返回 (None, reason)。
    允许 path 以分区目录开头（如 values/balance.json）或纯文件名（如 balance.json）。"""
    if region_key not in rmap:
        return None, f"未知分区：{region_key}（可选分区：{', '.join(rmap.keys())}）"
    region_abs = os.path.normpath(os.path.join(root, rmap[region_key]["dir"]))
    p = (path or "").strip().strip("'\"")
    if os.path.isabs(p):
        rel = os.path.relpath(os.path.normpath(p), root)
    else:
        rel = p
    rel = rel.replace("\\", "/")
    rdir = rmap[region_key]["dir"]
    if rel == rdir:
        rel = ""
    elif rel.startswith(rdir + "/"):
        rel = rel[len(rdir) + 1:]
    target = os.path.normpath(os.path.join(region_abs, rel)) if rel else region_abs
    if target != region_abs and not target.startswith(region_abs + os.sep):
        return None, f"拒绝写入：{path} 不在分区 {rdir}/ 内（禁止越区写）。"
    return target, None


def _emit_edit_arg(abs_path, old_text, new_text):
    """拼装给 apply_edit / create_file 的多行输入（复用其护栏：先读后写/.py 校验/人工确认/越区防护）。"""
    parts = ["path: " + abs_path]
    if old_text is not None:
        parts.append("old_text: " + old_text)
    parts.append("new_text: " + new_text)
    return "\n".join(parts)


def dev_list_regions(arg):
    """列出已配置分区的 key/名称/目录/依赖/导出/脏状态，供 Agent 选用正确分区。输入留空即可。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    from regions import list_regions
    rows = list_regions(root)
    lines = [f"已配置 {len(rows)} 个分区（code_root={root}）："]
    for r in rows:
        deps = ", ".join(r["depends_on"]) or "无"
        exports = ", ".join(r["exports"]) or "无"
        dirty = "（有未提交改动）" if r["dirty"] else ""
        lines.append(f"- {r['key']}｜{r['name']}（{r['dir']}/）：{r['desc']}；依赖：{deps}；导出：{exports}{dirty}")
    return "\n".join(lines)


def dev_region_read(arg):
    """读取某分区内的文件（分区作用域，越区读被拒）。
    输入：region: <分区key> 换行 path: <分区内相对路径>。
    读取后该文件可被 dev_region_edit 整体重写（满足先读后写护栏）。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg, ["region", "path"])
    region, path = (f.get("region") or "").strip(), (f.get("path") or "").strip()
    if not region or not path:
        return "参数缺失：请提供 region: <分区key> 与 path: <分区内相对路径>。"
    target, reason = _resolve_region_path(root, rmap, region, path)
    if target is None:
        return reason
    return read_file(target)


def dev_region_edit(arg):
    """受控修改/新建某分区内的文件（分区作用域，越区写被拒；复用 apply_edit/create_file 的全部护栏）。
    两种用法：① 局部安全替换——提供 region、path、old_text（精确旧片段）、new_text；
    ② 整体重写——提供 region、path、new_text（省略 old_text），前提是你已用 dev_region_read 读过该文件。
    若路径文件已存在则按修改处理，不存在则按新建处理。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg, ["region", "path", "old_text", "new_text"])
    region = (f.get("region") or "").strip()
    path = (f.get("path") or "").strip()
    old_text = f.get("old_text")
    new_text = f.get("new_text")
    if not region or not path:
        return "参数缺失：请提供 region: <分区key> 与 path: <分区内相对路径>。"
    if new_text is None:
        return "参数缺失：请提供 new_text: <新内容>（局部替换还需 old_text: <精确旧片段>）。"
    target, reason = _resolve_region_path(root, rmap, region, path)
    if target is None:
        return reason
    lock = _region_lock(region)
    with lock:
        set_runtime("region_edit_context", True)
        try:
            if os.path.isfile(target):
                return apply_edit(_emit_edit_arg(target, old_text, new_text))
            return create_file(_emit_edit_arg(target, None, new_text))
        finally:
            set_runtime("region_edit_context", False)


def _run_region_cmd(region_dir_abs, cmd):
    """在分区目录内执行校验命令（受安全黑名单约束，超时 30s，输出截断 1500 字）。"""
    cmd = (cmd or "").strip().strip("'\"")
    if not cmd:
        return "未提供校验命令。"
    blocked, why = _cmd_is_blocked(cmd)
    if blocked:
        return f"拒绝执行：校验命令被安全策略拦截（命中「{why}」）。"
    try:
        proc = subprocess.run(cmd, shell=True, cwd=region_dir_abs, timeout=30,
                              capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return "校验命令执行超时（>30s）。"
    except Exception as e:  # noqa: BLE001
        return f"执行失败: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    head = f"[exit code {proc.returncode}]\n"
    return head + (out[:1500] + ("…" if len(out) > 1500 else ""))


def dev_region_verify(arg):
    """校验单个分区：若该分区配置了 verify 命令则在分区目录内执行；否则检查其导出接口文件是否齐全。
    输入：region: <分区key>。返回命令执行情况或契约自检结果。
    说明：verify 命令建议写为脚本文件（如 `pytest tests/`）而非 `python -c ...`（后者会被安全策略拦截）。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg, ["region"])
    region = (f.get("region") or "").strip()
    if not region:
        return "参数缺失：请提供 region: <分区key>。"
    if region not in rmap:
        return f"未知分区：{region}（可选分区：{', '.join(rmap.keys())}）"
    meta = rmap[region]
    region_abs = os.path.normpath(os.path.join(root, meta["dir"]))
    verify_cmd = (meta.get("verify") or "").strip()
    if verify_cmd:
        from regions import run_verify
        ok, output = run_verify(region_abs, verify_cmd)
        if ok is not None or output is not None:
            return f"运行 {meta['name']} 的内置校验（{verify_cmd}）：\n{output}"
        return f"运行 {meta['name']} 的 verify 命令：`{verify_cmd}`\n" + _run_region_cmd(region_abs, verify_cmd)
    exports = meta.get("exports") or []
    if not exports:
        return f"{meta['name']} 未配置 verify 命令，也无导出接口需校验，跳过（OK）。"
    miss = [ex for ex in exports if not os.path.isfile(os.path.join(region_abs, ex))]
    if miss:
        return f"{meta['name']} 导出接口缺失：{', '.join(miss)}（契约校验失败）。"
    return f"{meta['name']} 导出接口齐全（{', '.join(exports)}），契约自检通过（OK）。"


def dev_refactor(arg):
    """跨分区安全搬移：把某分区内的文件移动到另一分区（受依赖方向约束，不破坏 git 跟踪）。
    输入：
      src_region: <源分区key>
      src_path: <源文件在源分区内的相对路径>
      dst_region: <目标分区key>
      dst_path: <目标文件在目标分区内的相对路径>
    规则：目标文件不能已存在（不覆盖）；搬移后从源分区 git 仓库移除源文件（保留历史）。
    依赖方向：仅允许移动到 src 依赖的分区（dst ∈ src.depends_on）或前后无关的分区；
    禁止把代码挪进「已依赖 src」的分区（sr ∈ dst.depends_on），避免循环耦合 / 倒置分层。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg, ["src_region", "src_path", "dst_region", "dst_path"])
    sr, sp = (f.get("src_region") or "").strip(), (f.get("src_path") or "").strip()
    dr, dp = (f.get("dst_region") or "").strip(), (f.get("dst_path") or "").strip()
    if not (sr and sp and dr and dp):
        return "参数缺失：请提供 src_region/src_path/dst_region/dst_path。"
    if sr not in rmap or dr not in rmap:
        return f"未知分区：src={sr} dst={dr}（可选：{', '.join(rmap.keys())}）"
    src_deps = set(rmap[sr].get("depends_on") or [])
    # 依赖方向校验：禁止把 src 的代码挪进「已依赖 src」的分区（倒置分层 → 潜在循环耦合）
    if dr != sr and dr not in src_deps and sr in set(rmap[dr].get("depends_on") or []):
        return (f"拒绝搬移：{sr} → {dr} 会形成反向依赖（{dr} 已依赖 {sr}），"
                f"把 {sr} 的代码挪进 {dr} 会破坏依赖方向、可能引发循环耦合。"
                f"请改放到 {sr} 依赖的分区之一（{', '.join(src_deps) or '无'}），或做解耦重组。")
    src_target, reason = _resolve_region_path(root, rmap, sr, sp)
    if src_target is None:
        return reason
    if not os.path.isfile(src_target):
        return f"源文件不存在：{sp}（在分区 {sr} 内）。"
    dst_target, reason = _resolve_region_path(root, rmap, dr, dp)
    if dst_target is None:
        return reason
    if os.path.exists(dst_target):
        return f"目标文件已存在：{dp}（在分区 {dr} 内），dev_refactor 不覆盖，请先处理目标。"
    try:
        with open(src_target, encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
    except Exception as e:  # noqa: BLE001
        return f"读取源文件失败: {e}"
    create_res = create_file(_emit_edit_arg(dst_target, None, content))
    # 「人工确认」模式：仅暂存、未落盘 → 不要动源文件，等用户确认后再搬
    if "待人工确认" in create_res:
        return (f"目标已暂存、待人工确认，源文件未改动。请在界面确认写入后重新调用本工具，"
                f"届时本工具会从 {sr} 移除源文件。\n{create_res}")
    if not create_res.startswith("已创建"):
        return f"目标创建失败，已中止搬移：{create_res}"
    # 真正创建成功 → 从源分区 git 移除源文件（保留历史）；未跟踪则直接删除
    src_region_abs = os.path.normpath(os.path.join(root, rmap[sr]["dir"]))
    if os.path.isdir(os.path.join(src_region_abs, ".git")):
        rel_src = os.path.relpath(src_target, src_region_abs)
        proc = subprocess.run(
            ["git", "rm", "-q", rel_src], cwd=src_region_abs,
            capture_output=True, text=True,
        )
        if proc.returncode != 0 and "not found" in (proc.stdout or proc.stderr or "") \
                and os.path.isfile(src_target):
            try:
                os.remove(src_target)
            except Exception:
                pass
    elif os.path.isfile(src_target):
        try:
            os.remove(src_target)
        except Exception as e:  # noqa: BLE001
            return f"目标已创建，但删除源文件失败：{e}（请手动清理 {src_target}）"
    return f"已安全搬移 {sr}/{sp} → {dr}/{dp}（目标已创建，源文件从 {sr} 移除）。\n{create_res}"


def dev_commit(arg):
    """提交单个分区的改动（该分区独立 git 仓库内 commit）。
    输入：region: <分区key> 换行 message: <提交说明>。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg, ["region", "message"])
    region = (f.get("region") or "").strip()
    message = (f.get("message") or "docmind: update").strip() or "docmind: update"
    if not region:
        return "参数缺失：请提供 region: <分区key>。"
    from regions import commit_region
    ok, out = commit_region(root, region, message)
    return ("已提交" if ok else "提交失败") + f" 分区 {region}：" + out


def dev_verify_contracts(arg):
    """校验全部分区的契约：依赖方向无环、被依赖分区导出文件存在、依赖目标存在。
    输入留空即可。建议在大幅改动分区前后调用。返回 ok/错误列表/依赖图。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    from regions import verify_contracts
    r = verify_contracts(root)
    if r["ok"]:
        return "契约校验通过（依赖方向无环、依赖目标与导出接口均存在）。\n依赖图：" + json.dumps(r["graph"], ensure_ascii=False)
    return "契约校验失败：\n- " + "\n- ".join(r["errors"]) + "\n依赖图：" + json.dumps(r["graph"], ensure_ascii=False)


def dev_rebuild_index(arg):
    """依据当前分区配置重算 DEV_INDEX.md（分区目录/文件数/依赖变动后调用）。输入留空即可。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    from regions import rebuild_dev_index
    ok, msg = rebuild_dev_index(root)
    return msg


def dev_commit_all(arg):
    """把所有分区的改动各提交一次，并记进 dev_changesets.jsonl 作为一次绑定变更集（可整体回滚）。
    输入：message: <本次功能改动说明>。返回变更集 id 与各分区提交哈希。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    f = _parse_keyed(arg, ["message"])
    message = (f.get("message") or "docmind: update").strip() or "docmind: update"
    from regions import commit_all
    ok, info = commit_all(root, message)
    if not ok:
        return "变更集提交失败：\n" + "\n".join(f"- {k}: {v}" for k, v in (info.get("errors") or {}).items())
    cs_id = info.get("id", "")
    commits = info.get("commits", {})
    if not commits:
        return info.get("message", "没有可提交的分区改动。")
    lines = [f"已创建变更集 {cs_id}（message={message}）："]
    for k, v in commits.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines) + f"\n（回滚请调用 dev_rollback_changeset，changeset={cs_id}）"


def dev_list_changesets(arg):
    """列出已记录的跨区变更集（dev_changesets.jsonl）。输入留空即可。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    from regions import list_changesets
    cs = list_changesets(root)
    if not cs:
        return "暂无已记录的变更集。用过 dev_commit_all 后会在此列出。"
    lines = [f"共 {len(cs)} 个变更集："]
    for c in cs:
        lines.append(f"- id={c.get('id')} message={c.get('message','')} 分区={list((c.get('commits') or {}).keys())}")
    return "\n".join(lines)


def dev_rollback_changeset(arg):
    """整体回滚某变更集：对每个分区 revert 其记录的 commit（生成新提交撤销改动）。
    输入：changeset: <变更集id> 或 id: <变更集id>。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    f = _parse_keyed(arg, ["changeset", "id"])
    cs_id = (f.get("changeset") or f.get("id") or "").strip()
    if not cs_id:
        return "参数缺失：请提供 changeset: <变更集id>（先用 dev_list_changesets 查看）。"
    from regions import rollback_changeset
    ok, detail = rollback_changeset(root, cs_id)
    if not ok:
        return detail
    return f"已回滚变更集 {cs_id}：\n- " + "\n- ".join(detail)


# ---------------------------------------------------------------------------
# Agent 研判分区工具：勘察目录 → 获取基线建议 → 落地自定义方案 / 增补单分区。
# 默认 8 个分区仅作「初始建议」，真实分区由 Agent 依据代码库判断后应用。
# ---------------------------------------------------------------------------
def list_dir(arg):
    """浏览代码库内的目录结构（限定 code_root，供 Agent 研判代码库组织方式）。
    输入：可选 path: <相对 code_root 的目录，默认根目录>。返回该目录下子项（目录/文件）及大小/类型。
    这是 Agent 研判分区前的「勘察」工具：看清顶层有哪些模块/资源目录，再决定分区方案。"""
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    f = _parse_keyed(arg or "", ["path"])
    rel = (f.get("path") or "").strip().strip("'\"")
    if not rel:
        # 弱模型常按 list_dir(behaviors/) 裸参数调用（内联括号参数剥引号后无 path: 前缀）。
        # 整段输入若不含 key: 字段标记，就当作相对目录路径，避免静默回退根目录导致空转。
        bare = (arg or "").strip().strip("'\"")
        if bare and not re.search(r"^\s*[A-Za-z_]\w*\s*:", bare, re.M):
            rel = bare
    target, root_abs = _resolve_in_root(rel if rel else root)
    if target is None:
        return f"拒绝访问：{rel} 不在代码根目录内。"
    if not os.path.isdir(target):
        return f"不是目录：{rel or '(根目录)'}（请用 list_dir 浏览目录，不要传文件路径）。"
    try:
        entries = sorted(os.listdir(target))
    except Exception as e:  # noqa: BLE001
        return f"读取目录失败: {e}"
    rows = []
    for name in entries:
        p = os.path.join(target, name)
        if os.path.isdir(p):
            try:
                n = sum(1 for _ in os.scandir(p))
            except Exception:
                n = 0
            rows.append(f"[DIR ] {name}/  ({n} 项)")
        else:
            try:
                sz = os.path.getsize(p)
            except Exception:
                sz = 0
            rows.append(f"[FILE] {name}  ({sz} 字节)")
    rel_disp = os.path.relpath(target, root_abs)
    rel_disp = "(代码库根)" if rel_disp in (".", "") else rel_disp
    header = f"目录 {rel_disp} 共 {len(rows)} 项："
    return header + "\n" + "\n".join(rows) if rows else header + "（空）"


def dev_propose_regions(arg):
    """依据真实代码库结构，由 Agent 研判分区方案（默认 8 区仅作初始建议）。输入留空即可。
    返回：结构分析 + 建议分区清单（每区带 detected 证据与 included 建议）+ 代码库特有的可独立模块 + 中文结论。
    用法：先用 list_dir 勘察 → 调用本工具获取基线方案 → 据 detected/included 增删分区 → 用 dev_apply_regions 落地。"""
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    try:
        from regions import propose_regions
        res = propose_regions(root)
    except Exception as e:  # noqa: BLE001
        return f"研判分区失败: {e}"
    if not res.get("ok"):
        return res.get("error", "研判分区失败。")
    a = res["analysis"]
    lines = [res["summary"], ""]
    lines.append(f"结构分析：顶层目录 = {', '.join(a['top_level_dirs']) or '(无)'}")
    c = a["counts"]
    lines.append(f"资源计数：图像 {c['images']} / 音频 {c['audio']} / 模型 {c['models']} / 数据表 {c['data']} / 代码 {c['code']}")
    lines.append("")
    lines.append("建议分区（included=建议启用，detected=代码库检出信号）：")
    for r in res["proposed"]:
        flag = "✅启用" if r["included"] else "➖可选"
        det = "（已检出）" if r["detected"] else "（未检出）"
        ev = ("；证据：" + "、".join(r["evidence"])) if r["evidence"] else ""
        lines.append(f"- {r['key']}｜{r['name']}（{r['dir']}/） {flag}{det}：{r['reason']}{ev}")
    if res["custom_suggestions"]:
        lines.append("")
        lines.append("代码库特有的可独立分区（custom_suggestions，可据实际增删）：")
        for r in res["custom_suggestions"]:
            lines.append(f"- {r['key']}｜{r['name']}（{r['dir']}/）：{r['desc']}")
    lines.append("")
    lines.append("落地方式：把最终分区清单（JSON 数组，每项含 key/dir/name/depends_on 等）交给 dev_apply_regions 应用；"
                 "或仅追加一个分区用 dev_add_region。")
    return "\n".join(lines)


_APPLY_REGIONS_USAGE = (
    "参数缺失：请提供分区清单。可传 JSON 数组（或 {\"regions\":[...]} 对象），"
    "例如 regions: [{\"key\":\"values\",\"dir\":\"values\",\"name\":\"数值区\"}, ...]。"
)


def _parse_regions_payload(arg):
    """从工具入参里解析出分区清单。返回 (regions_list, err)。

    弱模型 / 原生 function-calling 给的入参格式很多样，这里全部兼容，避免因解析失败
    反复重试同一工具（这会耗尽上下文）：
      ① 整个 arg 就是 JSON：`{"regions":[...]}` 或裸数组 `[...]`（原生 tool_call 最常见）；
      ② `regions: <JSON>` 的 keyed 文本；
      ③ 上面都失败时用 ast.literal_eval 兜底 Python repr（单引号/True/None）形式。
    """
    whole = (arg or "").strip()
    if not whole:
        return None, _APPLY_REGIONS_USAGE
    keyed = (_parse_keyed(whole, ["regions"]).get("regions") or "").strip()
    # 先试 keyed 的值（`regions: [...]`），再试整段（裸 JSON），两者去重保序
    candidates = [c for c in (keyed, whole) if c]
    data, last_err = None, ""
    for cand in candidates:
        try:
            data = json.loads(cand)
            break
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
    if data is None:
        import ast
        for cand in candidates:
            try:
                data = ast.literal_eval(cand)
                break
            except Exception:  # noqa: BLE001
                continue
    if data is None:
        return None, (f"regions 不是合法 JSON：{last_err}。请传入 JSON 数组"
                      "（可用 dev_propose_regions 的产出再裁减）。")
    if isinstance(data, dict) and isinstance(data.get("regions"), list):
        data = data["regions"]
    if not isinstance(data, list):
        return None, "regions 格式应为 JSON 数组，或 {\"regions\":[...]} 对象。"
    return data, ""


def dev_apply_regions(arg):
    """应用 Agent 研判后的自定义分区方案：把给定分区清单写入 regions.json 并初始化（建目录/每区 git/导出桩/规则）。
    输入：regions: <JSON 数组，或 {"regions":[...]} 对象>；也可直接给 JSON 数组 / 对象的原文。
    每个分区至少含 key 与 dir；可选 name/desc/depends_on/exports/verify。
    这是把「Agent 判断的分区」落地的关键一步；应用后 Agent 写操作即被约束到这些分区内。"""
    root = _get_code_root()
    if not root:
        return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    regions_list, err = _parse_regions_payload(arg)
    if regions_list is None:
        return err
    from regions import init_regions
    ok, msg = init_regions(root, regions_list)
    if not ok:
        return "应用分区方案失败：" + msg
    # 重新注入分区契约，使 Agent 立即按新分区约束工作
    try:
        from ingest import load_project_rules
        rules = load_project_rules(os.path.abspath(root))
        set_runtime("project_rules", rules)
    except Exception:  # noqa: BLE001
        pass
    return "已应用 Agent 研判的分区方案。" + msg


def dev_add_region(arg):
    """向现有分区配置追加（或覆盖同名）一个分区，并立即初始化它（建目录/每区 git/规则）。
    输入：key: <分区key> 换行 dir: <目录> 换行 name: <中文名> 换行 [desc:] [access:] [depends_on:]（逗号分隔） [exports:]（逗号分隔）。
    用于 Agent 研判后按需增补单个分区，无需重传整个方案。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    f = _parse_keyed(arg or "", ["key", "dir", "name", "desc", "access", "depends_on", "exports"])
    key = (f.get("key") or "").strip()
    d = (f.get("dir") or "").strip()
    name = (f.get("name") or "").strip()
    if not key or not d:
        return "参数缺失：请提供 key: <分区key> 与 dir: <目录>（建议再给 name: <中文名>）。"
    from regions import load_region_config, init_regions
    cur = load_region_config(root)
    new_region = {
        "key": key,
        "dir": d.strip("/\\").replace("\\", "/"),
        "name": name or key,
        "desc": (f.get("desc") or "").strip(),
        "access": (f.get("access") or "").strip(),
        "depends_on": [x.strip() for x in (f.get("depends_on") or "").split(",") if x.strip()],
        "exports": [x.strip() for x in (f.get("exports") or "").split(",") if x.strip()],
        "verify": "",
    }
    cur = [r for r in cur if (r.get("key") or "").strip() != key]  # 覆盖同名
    cur.append(new_region)
    ok, msg = init_regions(root, cur)
    if not ok:
        return "新增/更新分区失败：" + msg
    try:
        from ingest import load_project_rules
        rules = load_project_rules(os.path.abspath(root))
        set_runtime("project_rules", rules)
    except Exception:  # noqa: BLE001
        pass
    return f"已新增/更新分区 {key}（{d}/）并写入 regions.json。" + msg


def dev_approve(arg):
    """审批敏感操作（提交/回滚/应用分区方案/装配 MCP）前必须调用：记录一次审批，30 分钟内该操作放行。
    输入：action: <commit_region|commit_all|rollback_changeset|apply_regions|mcp_server|mcp_capability> 换行 target: <对象>
    target 精确匹配、不是通配符：commit_region 传分区 key（逐区审批，不能用 *）、
    rollback_changeset 传变更集 id、commit_all / apply_regions 固定传 *；
    mcp_server 传 dev_mcp_add/dev_mcp_remove 阻断结构里给出的完整 target（add 的 target 已绑定命令/URL，必须原样照抄）；
    mcp_capability 传连接器 key。
    在调用 dev_commit / dev_commit_all / dev_rollback_changeset / dev_apply_regions / dev_add_region /
    dev_mcp_add / dev_mcp_remove / dev_mcp_decide 之前先调用本工具完成审批。
    若这些工具返回 blocked / approval_required，先调用本工具再重试，不要绕过。"""
    f = _parse_keyed(arg or "", ["action", "target"])
    action = (f.get("action") or "").strip()
    target = (f.get("target") or "*").strip() or "*"
    if not action:
        return "参数缺失：请提供 action: <操作名>（如 commit_all / mcp_server / mcp_capability）。"
    # MCP 审批只依赖代码库根，不要求初始化分区；分区类操作维持原前置。
    if action in ("mcp_server", "mcp_capability"):
        root = _get_code_root()
        if not root:
            return "尚未配置代码库根目录，请先用 /api/ingest_code 指定代码目录。"
    else:
        res, err = _require_regions()
        if res is None:
            return err
        root, _ = res
    from game_workbench import approval
    approval(root, action, "agent", approved=True, target=target)
    return f"已审批 {action}(target={target})，30 分钟内该操作放行。现在可执行对应的 dev_* 工具。"


def dev_approval_status(arg):
    """查询某敏感操作当前是否已通过审批。输入：action: <操作名> 换行 target: <对象>(默认 *)
    返回 approved: true/false。用于决定是否需要先调用 dev_approve。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, _ = res
    f = _parse_keyed(arg or "", ["action", "target"])
    action = (f.get("action") or "").strip()
    target = (f.get("target") or "*").strip() or "*"
    if not action:
        return "参数缺失：请提供 action: <操作名>。"
    from game_workbench import approval_status
    st = approval_status(root, action, target)
    return f"操作 {action}(target={target}) 审批状态：{'已通过' if st['approved'] else '未通过（需先调用 dev_approve）'}（有效期 {st['ttl_seconds'] // 60} 分钟）。"


def _tool_install_request(arg):
    text = str(arg or "").strip()
    if text.startswith("{"):
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}
    fields = _parse_keyed(text, ["manager", "package", "name", "version", "fallback_tools", "fallback"])
    if isinstance(fields.get("fallback_tools"), str):
        fields["fallback_tools"] = [x.strip() for x in fields["fallback_tools"].split(",") if x.strip()]
    if isinstance(fields.get("fallback"), str):
        fields["fallback"] = [x.strip() for x in fields["fallback"].split(",") if x.strip()]
    return fields


def dev_install_tool(arg):
    """在项目内隔离安装缺失工具；必须先通过 install_tool 审批。"""
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return json.dumps({"ok": False, "error": "未配置代码库"}, ensure_ascii=False)
    from agent_runtime.tool_install import ToolInstallManager, ToolInstallError
    request = _tool_install_request(arg)
    try:
        manager = ToolInstallManager(root)
        plan = manager.plan(request)
    except (ToolInstallError, TypeError, ValueError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)
    from game_workbench import require_approval
    gate = require_approval(root, "install_tool", plan["approval_target"])
    if gate:
        return json.dumps({**gate, "plan": plan,
                           "alternatives": plan.get("alternatives", [])}, ensure_ascii=False)
    result = manager.install(request, approved=True)
    return json.dumps(result, ensure_ascii=False)[:6000]


def dev_tool_install_audit(arg):
    """查看项目内工具安装审计记录；不执行安装。"""
    root = get_runtime("code_root") or CODE_ROOT
    if not root:
        return json.dumps({"ok": False, "error": "未配置代码库"}, ensure_ascii=False)
    from agent_runtime.tool_install import ToolInstallManager
    fields = _parse_keyed(str(arg or ""), ["limit"])
    try:
        limit = int(fields.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    return json.dumps({"ok": True, "items": ToolInstallManager(root).audit(limit)},
                      ensure_ascii=False)


def _region_file(root, rmap, key, rel_path):
    """Resolve a file inside a configured region and reject symlink/path escapes."""
    if key not in rmap:
        return None, f"未知分区：{key}"
    base = os.path.realpath(os.path.join(root, rmap[key]["dir"]))
    rel = (rel_path or "").strip().strip("'\"").replace("\\", "/")
    if not rel or os.path.isabs(rel) or any(p in ("", ".", "..") for p in rel.split("/")):
        return None, "素材路径必须是分区内的相对路径，禁止绝对路径和 ..。"
    target = os.path.realpath(os.path.join(base, rel))
    if target != base and not target.startswith(base + os.sep):
        return None, "拒绝访问：路径不在目标分区内。"
    return target, None


def dev_asset_get(arg):
    """通过素材区接口取得素材引用；其它分区只能拿到素材区内的路径/元数据。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg or "", ["asset_id", "path", "consumer_region"])
    asset_id = (f.get("asset_id") or f.get("path") or "").strip()
    consumer = (f.get("consumer_region") or "").strip()
    if not asset_id:
        return "参数缺失：请提供 asset_id 或 path。"
    if consumer and consumer not in rmap:
        return f"未知调用分区：{consumer}"
    assets_dir = os.path.realpath(os.path.join(root, rmap.get("assets", {}).get("dir", "assets")))
    manifest = os.path.join(assets_dir, "manifest.json")
    rel = asset_id.replace("\\", "/").strip("/")
    # 支持 manifest 中的 id -> path 映射，也支持直接传相对路径。
    if os.path.isfile(manifest):
        try:
            with open(manifest, encoding="utf-8") as fh:
                data = json.load(fh)
            entries = data.get("assets", data) if isinstance(data, dict) else data
            if isinstance(entries, dict) and asset_id in entries:
                entry = entries[asset_id]
                rel = entry.get("path", "") if isinstance(entry, dict) else str(entry)
            elif isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("id") == asset_id:
                        rel = entry.get("path", "")
                        break
        except (OSError, ValueError):
            pass
    target, reason = _region_file(root, rmap, "assets", rel)
    if target is None:
        return reason
    if not os.path.isfile(target):
        return f"素材不存在：{rel}"
    return json.dumps({
        "ok": True, "asset_id": asset_id, "path": os.path.relpath(target, root).replace("\\", "/"),
        "consumer_region": consumer or None, "size": os.path.getsize(target),
        "extension": os.path.splitext(target)[1].lower(),
    }, ensure_ascii=False)


def dev_asset_register(arg):
    """注册素材到 assets/manifest.json；不复制文件，只建立稳定 ID。"""
    res, err = _require_regions()
    if res is None: return err
    root, rmap = res
    f = _parse_keyed(arg or "", ["asset_id", "path", "type", "license", "tags"])
    aid, rel = (f.get("asset_id") or "").strip(), (f.get("path") or "").strip()
    if not aid or not rel: return "参数缺失：请提供 asset_id 和 path。"
    target, reason = _region_file(root, rmap, "assets", rel)
    if target is None: return reason
    if not os.path.isfile(target): return f"素材不存在：{rel}"
    manifest = os.path.join(root, rmap.get("assets", {}).get("dir", "assets"), "manifest.json")
    try:
        with open(manifest, encoding="utf-8") as fh: data = json.load(fh)
    except (OSError, ValueError): data = {}
    if not isinstance(data, dict): data = {}
    data.setdefault("assets", {})[aid] = {"path": rel.replace("\\", "/"), "type": f.get("type") or "unknown", "license": f.get("license") or "unknown", "tags": [x.strip() for x in (f.get("tags") or "").split(",") if x.strip()]}
    with open(manifest, "w", encoding="utf-8") as fh: json.dump(data, fh, ensure_ascii=False, indent=2)
    return json.dumps({"ok": True, "asset_id": aid, "path": rel}, ensure_ascii=False)


def dev_capture_bug(arg):
    """把异常堆栈/复现信息归档到 bugs 分区，返回可追踪的 Bug ID。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    f = _parse_keyed(arg or "", ["error", "exception", "traceback", "source_region", "reproduction", "severity", "title"])
    message = (f.get("error") or f.get("exception") or "未知异常").strip()
    source = (f.get("source_region") or "").strip()
    if source and source not in rmap:
        return f"未知来源分区：{source}"
    bug_id = "BUG-" + __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + __import__("uuid").uuid4().hex[:6]
    bugs_dir = os.path.realpath(os.path.join(root, rmap.get("bugs", {}).get("dir", "bugs")))
    os.makedirs(bugs_dir, exist_ok=True)
    record = {
        "id": bug_id, "title": (f.get("title") or message[:120]).strip(),
        "severity": (f.get("severity") or "error").strip(), "source_region": source or None,
        "error": message, "traceback": f.get("traceback") or "",
        "reproduction": f.get("reproduction") or "", "created_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "status": "open",
    }
    path = os.path.join(bugs_dir, bug_id + ".json")
    with open(path, "x", encoding="utf-8") as fh:
        json.dump(record, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return json.dumps({"ok": True, "bug_id": bug_id, "path": os.path.relpath(path, root).replace("\\", "/")}, ensure_ascii=False)


def dev_list_bugs(arg=""):
    """列出 bugs 分区中的结构化异常记录。"""
    res, err = _require_regions()
    if res is None:
        return err
    root, rmap = res
    base = os.path.join(root, rmap.get("bugs", {}).get("dir", "bugs"))
    rows = []
    if os.path.isdir(base):
        for name in sorted(os.listdir(base), reverse=True):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(base, name), encoding="utf-8") as fh:
                    rows.append(json.load(fh))
            except (OSError, ValueError):
                continue
    return json.dumps({"ok": True, "bugs": rows[:200]}, ensure_ascii=False)


def dev_update_bug(arg):
    """更新 Bug 状态（open/investigating/fixed/ignored）。"""
    res, err = _require_regions()
    if res is None: return err
    root, rmap = res
    f = _parse_keyed(arg or "", ["bug_id", "status"])
    bid, status = (f.get("bug_id") or "").strip(), (f.get("status") or "").strip().lower()
    if not bid or status not in {"open", "investigating", "fixed", "ignored"}:
        return "参数错误：bug_id 必填，status 必须是 open/investigating/fixed/ignored。"
    path = os.path.join(root, rmap.get("bugs", {}).get("dir", "bugs"), bid + ".json")
    if not os.path.isfile(path): return f"未找到 Bug：{bid}"
    with open(path, encoding="utf-8") as fh: record = json.load(fh)
    record["status"] = status
    record["updated_at"] = __import__("datetime").datetime.now().isoformat(timespec="seconds")
    with open(path, "w", encoding="utf-8") as fh: json.dump(record, fh, ensure_ascii=False, indent=2); fh.write("\n")
    return json.dumps({"ok": True, "bug": record}, ensure_ascii=False)


def game_validate_data(arg=""):
    root = _get_code_root()
    from game_workbench import validate_data
    return json.dumps(validate_data(root), ensure_ascii=False) if root else "尚未配置代码库。"


def game_release_check(arg=""):
    root = _get_code_root()
    from game_workbench import release_check
    return json.dumps(release_check(root), ensure_ascii=False) if root else "尚未配置代码库。"


def game_upsert_task(arg):
    root = _get_code_root()
    if not root: return "尚未配置代码库。"
    from game_workbench import upsert_task
    f = _parse_keyed(arg or "", ["id", "title", "description", "region", "priority", "status", "owner", "files"])
    f["files"] = [x.strip() for x in (f.get("files") or "").split(",") if x.strip()]
    return json.dumps(upsert_task(root, f), ensure_ascii=False)

def game_simulate(arg):
    from game_workbench import simulate_growth
    f=_parse_keyed(arg or "",["levels","base","growth","model","k"])
    return json.dumps(simulate_growth(int(f.get("levels") or 50),float(f.get("base") or 100),float(f.get("growth") or 1.08),(f.get("model") or "geometric"),(float(f.get("k")) if f.get("k") not in (None,"") else None)),ensure_ascii=False)
def game_impact(arg):
    from game_workbench import impact_analysis
    f=_parse_keyed(arg or "",["query"]); return json.dumps(impact_analysis(_get_code_root(),f.get("query") or ""),ensure_ascii=False)
def game_playtest(arg):
    from game_workbench import playtest
    f=_parse_keyed(arg or "",["command","timeout"]); return json.dumps(playtest(_get_code_root(),f.get("command") or "",int(f.get("timeout") or 30)),ensure_ascii=False)


# ---------------------------------------------------------------------------
# game_screenshot：运行画面截图观察
#
# 优先级：已启用连接器的截图类 MCP 工具 → 当前项目嵌入引擎窗口 → 系统前台窗口。
# 截图是【观察素材】不是代码事实；无头/非 Windows/无窗口时返回明确文字失败，
# Agent 应改用运行日志、playtest 事件等证据，不允许臆测画面。
# ---------------------------------------------------------------------------
_SCREENSHOT_TOOL_KEYWORDS = ("screenshot", "capture_screen", "screen_capture",
                             "takescreenshot", "截图", "截屏", "抓屏", "画面捕获")
_SCREENSHOT_TOOL_BLOCKERS = ("video", "record", "录制", "录像")


def _is_screenshot_connector_tool(spec):
    blob = ((spec.get("name") or "") + " " + (spec.get("description") or "")).lower()
    if any(bad in blob for bad in _SCREENSHOT_TOOL_BLOCKERS):
        return False
    return any(k in blob for k in _SCREENSHOT_TOOL_KEYWORDS)


def _try_mcp_screenshot(root):
    """best-effort 调已启用连接器的截图类工具；任何失败/无图返回 None 降级。

    list_tools 可能冷启动 stdio 连接器（各带超时），多个连接器串行探测时
    用总时限封顶（DOCMIND_MCP_SHOT_PROBE_S，默认 8s）：连接器之间与截图调用
    均受剩余预算约束；极端情况下单次 stdio 冷启动本身可能超出预算，超时即
    回落本地抓窗，不影响截图主流程。
    """
    import time as _time
    try:
        probe_budget = float(os.getenv("DOCMIND_MCP_SHOT_PROBE_S", "8"))
    except (TypeError, ValueError):
        probe_budget = 8.0
    deadline = _time.monotonic() + max(1.0, probe_budget)
    try:
        connectors = mcp_client.connector_directory(root) or []
    except Exception:
        return None
    for conn in connectors:
        if not conn.get("enabled"):
            continue
        key = conn.get("key")
        if not key:
            continue
        remaining = deadline - _time.monotonic()
        if remaining <= 1.0:
            break
        try:
            listing = mcp_client.list_tools(root, key) or {}
            shot = next((t for t in (listing.get("tools") or [])
                         if _is_screenshot_connector_tool(t)), None)
            if not shot:
                continue
            if _time.monotonic() >= deadline:
                break
            # 单次截图调用也夹在剩余探测预算内（下限 1s），避免挂死连接器
            # 把整个截图动作拖到数分钟才回落本地抓窗。
            call_timeout = max(1.0, min(float(mcp_client.CALL_TIMEOUT),
                                        deadline - _time.monotonic()))
            resp = mcp_client.call_tool_with_fallback(
                root, key, shot["name"], {},
                task_hint="截取当前引擎运行画面截图",
                timeout=call_timeout)
        except Exception:
            continue
        if not resp.get("ok") or not resp.get("images"):
            continue
        first = resp["images"][0] or {}
        try:
            raw = base64.b64decode(first.get("data") or "", validate=False)
        except Exception:
            continue
        encoded = _encode_observation_image(raw)
        if encoded:
            return encoded, f"mcp:{key}/{shot['name']}"
    return None


def game_screenshot(arg=""):
    """截取当前运行画面作为视觉观察。输入可选 `target: embedded|foreground`（默认 embedded）。

    优先用已启用引擎连接器提供的截图能力；否则抓取工作台内嵌的引擎窗口，
    再不行抓系统前台窗口。返回图片保存路径（.docmind/screenshots/ 下的 JPEG）
    与图片观察内容。截图只反映某一瞬间的画面，不是代码事实；无窗口/无头环境会明确失败，
    此时请改用运行日志、受控 playtest 输出等证据。
    """
    fields = _parse_keyed(arg or "", ["target"])
    target = (fields.get("target") or "embedded").strip().lower()
    if target not in ("embedded", "foreground"):
        target = "embedded"
    root = _get_code_root()

    if root:
        out_dir = os.path.join(root, ".docmind", "screenshots")
    else:
        out_dir = os.path.join(STATE_ROOT, "screenshots")

    if target == "embedded":
        hit = _try_mcp_screenshot(root) if root else None
        if hit:
            encoded, source = hit
            try:
                import screen_capture
                tag = source.replace("mcp:", "").replace("/", "-")
                saved_path = screen_capture.save_encoded_jpeg(encoded, out_dir, tag=tag)
            except Exception:  # noqa: BLE001
                saved_path = None
            if not saved_path:
                return "截图失败：连接器画面已返回但截图文件保存失败。请改用运行日志或受控 playtest 输出判断。"
            return ToolResult(
                ok=True,
                text=(f"截图完成（来源：连接器 {source}），已保存：{saved_path}\n"
                      "截图是观察素材，不是代码事实；请结合代码与日志复核。"),
                data={"images": [encoded], "image_sources": [saved_path]})

    try:
        import screen_capture
    except Exception as exc:  # noqa: BLE001
        return f"截图失败：{type(exc).__name__}: {exc}（当前环境不支持画面捕获，请改用运行日志或受控 playtest 输出判断）"

    # 嵌入窗登记表按 project_id（prj-<sha1>）分桶，不是文件路径：
    # 传 root 会永不命中，多宿主时可能错截到别的项目窗口。
    project_pid = None
    try:
        from projects import current_project_id
        project_pid = current_project_id() or None
    except Exception:  # noqa: BLE001
        project_pid = None
    frame = None
    if target == "embedded":
        frame = screen_capture.grab_embedded(project_pid)
    if frame is None:
        frame = screen_capture.grab_foreground()
        source_kind = "foreground"
    else:
        source_kind = "embedded"
    if frame is None:
        return ("截图失败：未检测到可抓取的运行窗口（可能未运行、处于无头环境或非 Windows）。"
                "请先启动并嵌入引擎，或改用运行日志、受控 playtest 事件等证据，不要假设画面内容。")
    raw, width, height, _hwnd = frame
    saved = screen_capture.encode_and_save(raw, width, height, out_dir)
    if saved is None:
        return "截图失败：画面编码或保存失败。请改用运行日志或受控 playtest 输出判断。"
    encoded, path, size = saved
    return ToolResult(
        ok=True,
        text=(f"截图完成（来源：{source_kind} 窗口，原始 {width}x{height}，"
              f"输出 {size[0]}x{size[1]}）：{path}\n"
              "截图是观察素材，不是代码事实；请结合代码与运行日志复核。"),
        data={"images": [encoded], "image_sources": [path]})


# ---------------------------------------------------------------------------
# start_workflow：对话内发起【跨窗口持久开发工作流】
#
# 与 delegate / orchestrate 的边界：
# - delegate：一个相对独立的子任务，一轮内取回结论；
# - orchestrate：一轮内当场并行调度任务图并合成，会话结束即消散；
# - start_workflow：多阶段/多角色/含副作用阶段/需要人工方案门+审批门+跨窗口
#   恢复时才升级。只创建状态并停在方案选择门，绝不直接执行有副作用任务。
# ---------------------------------------------------------------------------
_BOOL_WORDS = {"1", "true", "yes", "on", "是", "要", "联网", "开"}


def start_workflow(arg=""):
    """发起一个跨窗口持久、带人工门的开发工作流（通用开发/游戏/EDA 等领域）。

    输入（keyed 多行，也可直接把整段目标作为入参）：
      goal: 完整目标（必填；未写字段名时整段入参即目标）
      kind: generic|game|eda（缺省 generic 通用开发；game=游戏开发；eda=原理图/PCB 电子设计）
      web:  true|false（缺省继承本轮会话联网开关）

    工具只负责创建工作流：它停在「方案选择门」，用户在对话中选择方案、
    确认任务 DAG 并审批后，多个子代理才会开始执行；本工具不会替用户
    执行任何有副作用任务，也不能跳过人工审批。无代码库根目录时明确失败且不落任何记录。
    """
    fields = _parse_keyed(arg or "", ["goal", "request", "kind", "web", "web_enabled"])
    goal = (fields.get("goal") or fields.get("request") or "").strip()
    if not goal:
        goal = (arg or "").strip()
    if not goal:
        return ("start_workflow 失败：缺少目标。请在 goal: 后写清要推进的完整目标"
                "（多阶段、可验收），或直接把目标整段作为入参。")

    kind_raw = (fields.get("kind") or "").strip().lower()
    from agent_runtime.workflow_profiles import get_profile, normalize_kind
    kind = normalize_kind(kind_raw) if kind_raw else "generic"
    profile = get_profile(kind)

    web_raw = fields.get("web")
    if web_raw is None:
        web_raw = fields.get("web_enabled")
    if web_raw is not None and str(web_raw).strip():
        web_enabled = str(web_raw).strip().lower() in _BOOL_WORDS
    else:
        web_enabled = _session_web_enabled.get()

    root = _get_code_root()
    if not root:
        return ("start_workflow 失败：当前没有配置项目代码库根目录，无法创建可跨窗口"
                "恢复的开发工作流。请先在工作台打开/索引项目后再发起；本次未创建任何工作流记录。")
    if _WORKFLOW_LAUNCHER is None:
        return ("start_workflow 失败：工作流启动通道未初始化（工作台路由未注册 launcher）。"
                "请改由 AI 运行台的「开发工作流」面板手动发起。")

    try:
        workflow = _WORKFLOW_LAUNCHER(goal, kind=kind, web_enabled=bool(web_enabled))
    except Exception as exc:  # noqa: BLE001 - 工具观察必须给出明确失败而不是抛断回合
        return f"start_workflow 失败：{type(exc).__name__}: {exc}。请改由 AI 运行台手动发起。"
    if not isinstance(workflow, dict) or not workflow.get("workflow_id"):
        return "start_workflow 失败：启动器未返回有效工作流状态，请改由 AI 运行台手动发起。"

    wid = workflow.get("workflow_id")
    status = workflow.get("status") or "awaiting_choice"
    _notify_workflow_started(workflow)
    lines = [
        f"已创建开发工作流（领域：{profile.display_name}；状态：{status}）。",
        "工作流当前停在【方案选择门】：尚未执行任何任务，工具不会替你执行有副作用的操作。",
        "请在对话中选择方案、确认任务 DAG 并审批后才会开始执行；",
        "审批前可修改任务与依赖，执行失败会按策略重规划，进度可跨窗口恢复。",
    ]
    options = workflow.get("options") or []
    if options:
        lines.append("可选方案：")
        for opt in options:
            if not isinstance(opt, dict):
                continue
            lines.append("· %s：%s — %s" % (
                opt.get("id", ""), opt.get("title", ""), opt.get("summary", "")))
    lines.append(f"工作流卡片已在对话中展开（ID：{wid}），等待用户选择方案。")
    return "\n".join(lines)


# ===========================================================================
# 写后自验证工具 self_verify（Phase 1/2「写后自验证闭环」收尾门）
# 详见 docs/agent-self-verify-memory.md。Agent 在 apply_edit / create_file 等写操作
# 成功后自动调用本工具对改动做轻量校验；失败信息回填模型、触发 ReAct 自修
# （复用 agent.py 既有 _FAILURE_MARKERS / _TOOL_FAIL_LIMIT / _TOTAL_FAIL_LIMIT 护栏）。
# 校验策略按改动文件类型分 scope（auto 模式自动选）：
#   backend  (.py)            → py_compile 每个改动文件 + 命中则跑对应 tests/test_<module>.py
#   frontend (.ts/.vue)       → npm run typecheck（严禁 build，并发铁律）
#   scene   (场景子系统文件)  → 自动跑 verify_scene_canvas.py（无 GUI，进程内 FastAPI 自检）
#   engine  (引擎嵌入模块)    → 需真 Godot GUI，默认仅 scope 显式指定 / scope:all /
#                              开 DOCMIND_SELF_VERIFY_ENGINE=1 时运行 verify_engine_embed.py，
#                              否则自动策略里降级为 skip（避免每次写都拉起 Godot 打断并发写入者）
# 任何异常一律降级为「跳过该 scope」，绝不因校验器自身故障阻断问答主流程。
# 入参与文本协议 Action Input 同形：单行 "scope: auto" + 多行 "files: a.py\nfiles: b.py"
# （前端自验证也可用 "files: frontend/src/x.ts"）。
# ===========================================================================

def _parse_self_verify_arg(arg):
    """把 self_verify 的入参字符串解析为 (scope, files)。"""
    arg = (arg or "").strip()
    scope = "auto"
    files_parts = []
    has_scope = False
    if not arg:
        return scope, None
    for line in arg.splitlines():
        s = line.strip()
        low = s.lower()
        if low.startswith("scope:"):
            scope = (s[len("scope:"):].strip() or "auto")
            has_scope = True
        elif low.startswith("files:"):
            rest = s[len("files:"):].strip()
            if rest:
                files_parts.append(rest)
    files = None
    if files_parts:
        files = [x.strip() for part in files_parts for x in re.split(r"[,\n]", part) if x.strip()]
    # 纯文本（无 scope:/files: 关键字）→ 整段按文件列表处理
    if files is None and not has_scope:
        cand = [x.strip() for x in re.split(r"[,\n]", arg) if x.strip()]
        if cand:
            files = cand
    return scope, files


def _sv_changed_via_git(root):
    """用 git diff 自动发现 code_root 内最近改动文件（staged + unstaged），返回绝对路径列表。"""
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "HEAD", "--", root],
            capture_output=True, text=True, timeout=15, cwd=root,
        )
        files = [os.path.abspath(os.path.join(root, l.strip())) for l in (out.stdout or "").splitlines() if l.strip()]
        out2 = subprocess.run(
            ["git", "diff", "--name-only", "--cached", "--", root],
            capture_output=True, text=True, timeout=15, cwd=root,
        )
        for l in (out2.stdout or "").splitlines():
            if l.strip():
                ap = os.path.abspath(os.path.join(root, l.strip()))
                if ap not in files:
                    files.append(ap)
        return files
    except Exception:  # noqa: BLE001 —— git 不可用/失败一律退化为空（由调用方决定）
        return []


def _sv_classify(targets):
    """把文件列表分到各 scope 的桶里，返回 (py, frontend, scene, engine) 绝对路径列表。"""
    import re as _re
    py, fe, scene, engine = [], [], [], []
    for t in targets:
        p = str(t)
        low = p.lower()
        if low.endswith(".py"):
            py.append(p)
        if low.endswith((".ts", ".tsx", ".vue", ".js")) or "/frontend/" in low:
            fe.append(p)
        if _re.search(r"scene", low):
            scene.append(p)
        if "engine_embed" in low or ("embed" in low and "desktop" in low) or low.endswith("desktop_bridge.py"):
            engine.append(p)
    return py, fe, scene, engine


def _sv_verify_backend(py_files, root, result):
    """后端校验：py_compile 每个改动 .py；命中则跑对应单测（有界到改动模块）。"""
    import py_compile
    for f in py_files:
        # 待校验文件不存在 → 记为失败（Agent 让校验一个不存在的文件，说明写/路径出错），
        # 不能降级成 skip 假通过（否则 self_verify 永远 passed:true，失去校验意义）。
        if not os.path.isfile(f):
            result["failures"].append({
                "scope": "backend", "file": f,
                "error": "待校验文件不存在：" + f,
            })
            continue
        try:
            py_compile.compile(f, doraise=True)
            result["ran"].append("py_compile:" + (os.path.relpath(f, root) if root else f))
        except py_compile.PyCompileError as e:
            result["failures"].append({
                "scope": "backend", "file": f,
                "error": "语法校验失败：" + str(getattr(e, "msg", e)),
            })
            continue
        # 命中单测：只跑确实存在且与改动模块对应的 tests/test_<module>.py，
        # 避免改一处就跑全仓 1000+ 用例（全量回归由 CI 门负责）。
        base = os.path.splitext(os.path.basename(f))[0]
        test_path = os.path.join(root or os.getcwd(), "tests", "test_" + base + ".py")
        if not os.path.isfile(test_path):
            continue
        try:
            r = subprocess.run(
                [sys.executable, "-B", "-m", "unittest", "tests.test_" + base, "-v"],
                capture_output=True, text=True, timeout=120, cwd=root or os.getcwd(),
            )
            if r.returncode != 0:
                tail = (r.stdout or "")[-1200:] + (r.stderr or "")[-1200:]
                result["failures"].append({
                    "scope": "backend", "file": f,
                    "error": "单测 tests.test_%s 未通过：\n%s" % (base, tail),
                })
            else:
                result["ran"].append("unittest:tests.test_" + base)
        except subprocess.TimeoutExpired:
            result["failures"].append({
                "scope": "backend", "file": f,
                "error": "单测 tests.test_%s 超时（>120s）未结束。" % base,
            })
        except Exception as e:  # noqa: BLE001 —— 测试运行器自身故障：降级为跳过该模块
            result["ran"].append("unittest:skip:tests.test_%s（%s）" % (base, type(e).__name__))


def _sv_verify_frontend(fe_files, root, result):
    """前端校验：仅 npm run typecheck（严禁 build，并发铁律）。"""
    import shutil
    for cand in ("frontend", "web", "ui"):
        d = os.path.join(root, cand) if root else cand
        if os.path.isdir(d):
            fe_dir = d
            break
    else:
        result["ran"].append("frontend:skip（未找到前端目录）")
        return
    pkg = os.path.join(fe_dir, "package.json")
    if not os.path.isfile(pkg):
        result["ran"].append("frontend:skip（无 package.json）")
        return
    try:
        with open(pkg, encoding="utf-8") as f:
            scripts = (json.load(f).get("scripts") or {})
    except Exception:  # noqa: BLE001
        scripts = {}
    if "typecheck" not in scripts:
        result["ran"].append("frontend:skip（无 typecheck 脚本）")
        return
    npm = os.getenv("DOCMIND_NPM_BIN") or shutil.which("npm")
    if not npm:
        result["ran"].append("frontend:skip（未找到 npm）")
        return
    try:
        r = subprocess.run([npm, "run", "typecheck"], capture_output=True, text=True,
                           timeout=180, cwd=fe_dir)
        if r.returncode != 0:
            tail = (r.stdout or "")[-1200:] + (r.stderr or "")[-1200:]
            result["failures"].append({
                "scope": "frontend", "file": fe_dir,
                "error": "typecheck 未通过：\n" + tail,
            })
        else:
            result["ran"].append("frontend:typecheck")
    except subprocess.TimeoutExpired:
        result["failures"].append({"scope": "frontend", "file": fe_dir,
                                   "error": "typecheck 超时（>180s）。"})
    except Exception as e:  # noqa: BLE001
        result["ran"].append("frontend:skip（%s）" % type(e).__name__)


def _sv_verify_script(script_name, kind, root, result):
    """运行 verify_* 脚本（scene/engine，仅 scope 显式指定时调用，需 display）。"""
    if root is None:
        result["ran"].append(kind + ":skip（无 code_root）")
        return
    script = os.path.join(root, script_name)
    if not os.path.isfile(script):
        result["ran"].append(kind + ":skip（无 " + script_name + "）")
        return
    try:
        r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                          timeout=180, cwd=root)
        if r.returncode != 0:
            tail = (r.stdout or "")[-1000:] + (r.stderr or "")[-1000:]
            result["failures"].append({
                "scope": kind, "file": script,
                "error": "%s 未通过（exit=%d）：\n%s" % (script_name, r.returncode, tail),
            })
        else:
            result["ran"].append(kind + ":" + script_name)
    except subprocess.TimeoutExpired:
        result["failures"].append({"scope": kind, "file": script,
                                   "error": script_name + " 超时（>180s）。"})
    except Exception as e:  # noqa: BLE001
        result["ran"].append(kind + ":skip（%s）" % type(e).__name__)


def self_verify(arg=""):
    """对代码库最近改动做轻量自验证，返回结构化 JSON 字符串（与文本协议 Action Input 同形）。

    入参（单行/多行 key: value）：
      scope: auto(默认, 按改动文件自动选) / backend / frontend / scene / engine / all / skip
      files: 显式指定待校验文件（相对/绝对路径，可多行或逗号分隔）；缺省时用 git diff 自动发现
    返回 JSON：{"scope","ran":[...],"passed":bool,"failures":[...],"note":""}
      status 区分 passed/failed/skipped/partial；无检查或跳过不视为通过。
    """
    scope, files = _parse_self_verify_arg(arg)
    from agent_runtime.verification import finalize_verification
    root = _get_code_root()
    result = {"scope": scope, "ran": [], "passed": True, "failures": [], "note": ""}

    if scope == "skip":
        result["note"] = "已跳过自验证（scope=skip）。"
        return json.dumps(finalize_verification(result), ensure_ascii=False)

    # 1) 决定待校验文件集合
    if files:
        targets = []
        for f in files:
            f = str(f)
            # 相对路径按 code_root 解析（Agent 收尾门传的是 root 相对路径），
            # 避免按 cwd 解析导致文件"看起来不存在"被误判为跳过。
            if root and not os.path.isabs(f):
                f = os.path.join(root, f)
            targets.append(os.path.abspath(f))
    else:
        targets = _sv_changed_via_git(root) if root else []
    if not targets:
        result["note"] = "未发现可校验的改动（无 git 改动或显式文件）。"
        return json.dumps(finalize_verification(result), ensure_ascii=False)

    # Resolve symlinks before checks: verification must not read/compile outside the project.
    safe_targets = []
    for target in targets:
        try:
            real_root = os.path.realpath(root) if root else None
            real_target = os.path.realpath(target)
            inside = bool(real_root and os.path.commonpath([real_root, real_target]) == real_root)
        except (ValueError, OSError):
            inside = False
        if inside:
            safe_targets.append(real_target)
        else:
            result["failures"].append({"scope": "permission", "file": target,
                                       "error": "校验目标超出当前项目范围"})
    targets = safe_targets

    py_files, fe_files, scene_files, engine_files = _sv_classify(targets)

    # 2) 选 scope：auto 按改动文件类型自动选策略。
    #    backend(.py) / frontend(.ts/.vue) / scene(场景子系统，无 GUI 可自动) 始终纳入；
    #    engine(引擎嵌入) 需真 Godot GUI 且会拉起引擎进程，默认不自动跑（避免每次写都启
    #    Godot 打断并发写入者）——仅当用户显式开 DOCMIND_SELF_VERIFY_ENGINE=1 才纳入自动，
    #    或经 scope:engine / scope:all 显式触发。其余情况优雅降级为 skip（见 _sv_verify_script）。
    if scope == "auto":
        scopes = []
        if py_files:
            scopes.append("backend")
        if fe_files:
            scopes.append("frontend")
        if scene_files:
            scopes.append("scene")
        if engine_files and os.getenv("DOCMIND_SELF_VERIFY_ENGINE") == "1":
            scopes.append("engine")
        elif engine_files:
            result.setdefault("skipped", []).append("engine:skip（自动引擎检查未启用）")
            result["note"] = ((result.get("note") or "") +
                              "（engine 域自检需真 Godot 且默认关闭，已跳过；"
                              "显式 scope:engine 或开 DOCMIND_SELF_VERIFY_ENGINE=1 可启用）")
    elif scope == "all":
        scopes = ["backend", "frontend", "scene", "engine"]
    else:
        scopes = [scope]

    for sc in scopes:
        try:
            if sc == "backend":
                _sv_verify_backend(py_files, root, result)
            elif sc == "frontend":
                _sv_verify_frontend(fe_files, root, result)
            elif sc == "scene":
                _sv_verify_script("verify_scene_canvas.py", "scene", root, result)
            elif sc == "engine":
                _sv_verify_script("verify_engine_embed.py", "engine", root, result)
            else:
                result["ran"].append("skip:未知 scope=" + str(sc))
        except Exception as e:  # noqa: BLE001 —— 单个 scope 校验器故障绝不阻断整体
            result["ran"].append("%s:skip（校验器异常 %s）" % (sc, type(e).__name__))

    if not result["ran"]:
        result["note"] = result["note"] or "无可执行的校验（改动文件类型无对应校验器）。"
    return json.dumps(finalize_verification(result), ensure_ascii=False)


def recall_experience(arg=""):
    """召回与当前任务相似的历史经验（跨会话经验记忆，Phase 3 建议性上下文）。

    入参（纯文本）：自然语言问题描述，如"改 Vue 组件后 typecheck 报错"；留空则退化为通用召回。
    返回结构化 JSON：{query, count, items:[{outcome, action_summary, decision, lesson, confidence, stale}]}。

    经验永远是「建议」，不强制覆盖当前真实证据（检索到的代码 / 校验结果优先级更高）；
    陈旧经验置信度已被打折，低置信不参与决策。任何故障静默降级为空结果。
    """
    try:
        from experience import recall_similar
        try:
            from projects import current_project_id
            project_id = current_project_id()
        except Exception:  # noqa: BLE001
            project_id = "default"
        q = (arg or "").strip() or "通用改动经验"
        hits = recall_similar(project_id, q, k=5)
        items = [{
            "outcome": h["metadata"].get("outcome"),
            "action_summary": h["metadata"].get("action_summary"),
            "decision": h["metadata"].get("decision"),
            "lesson": h["metadata"].get("lesson"),
            "confidence": h["confidence"],
            "stale": h["stale"],
        } for h in hits]
        return json.dumps({"query": q, "count": len(items), "items": items},
                          ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        return json.dumps({"query": arg, "count": 0, "items": [],
                           "note": "经验召回不可用（%s）" % type(e).__name__},
                          ensure_ascii=False)


TOOLS = {
    "web_research": {"description": "联网研究：先搜索，再读取多个公开网页正文（默认最多 5 个，可配置），返回来源和证据。适合教程、GitHub、引擎文档和需要最新资料的问题；样本不足时可用不同主题再次调用。输入研究主题。", "func": web_research},
    "web_fetch": {"description": "读取公开网页正文并返回来源、标题和清理后的文本。输入完整 http/https URL。联网研究时先 web_search，再对关键来源调用。", "func": web_fetch},
    "web_subtitles": {"description": "读取公开 B 站视频字幕。输入包含 BV 号或 av 号的完整视频 URL；没有公开字幕、需要登录或被风控时返回明确原因。", "func": web_subtitles},
    "dev_mcp_call": {"description": "调用已启用的 MCP 游戏引擎连接器。输入 key: 服务器key、name: 工具名、arguments: JSON；可选 fallback_keys 或 hint 触发有界故障转移。先用 dev_list_connectors 看清可用连接器、用 dev_route_connector 按任务语义挑 top 作为 key、用 dev_list_connector_tools 确认 name 与参数；外部连接器需已启用并遵守审批。涉及写入时传 side_effect: true，默认不会跨连接器重试；只有明确传 allow_side_effect_fallback: true 才允许。", "func": dev_mcp_call},
    "dev_list_connectors": {"description": "列出已配置 MCP 连接器（key/label/engine/transport/启用状态/能力标签/适用说明），供 Agent 自主挑选最合适的引擎连接器。输入留空。", "func": dev_list_connectors},
    "dev_route_connector": {"description": "按任务语义挑选最合适的【已启用】连接器：输入 hint（任务描述，如 'Godot 里打开 Main 场景并运行'），返回排序候选与匹配理由（top.key 即 dev_mcp_call 的 key）。某连接器不可用或调用失败时，用它重新挑选其它已启用连接器。", "func": dev_route_connector},
    "dev_list_connector_tools": {"description": "列出某连接器暴露的工具（name/description/input_schema），确定 dev_mcp_call 的 name 与参数。输入 key: <连接器key>；仅对打算调用的连接器使用（godot 等 stdio 需先建立会话）。", "func": dev_list_connector_tools},
    "dev_mcp_search": {"description": "自助装配 MCP 第 1 步：按能力关键词（如 kicad/pcb/数据库/github）搜索离线安全连接目录，返回能力清单、安装步骤与可回填的连接模板。本工具不联网；查最新第三方 MCP 时先 web_search/web_fetch 核对官方 command/URL。输入能力关键词。", "func": dev_mcp_search},
    "dev_mcp_add": {"description": "自助装配 MCP 第 2 步（敏感，需先 dev_approve(action: mcp_server)）：新增/更新连接器配置。多行输入 key/label/transport(stdio|http)，stdio 给 command+args（或 args_json），http 给 url。未审批时返回含 action/target 的 blocked，按 hint 审批后用相同参数重试。成功后调用 dev_mcp_probe。", "func": dev_mcp_add},
    "dev_mcp_probe": {"description": "自助装配 MCP 第 3 步：探活已装配连接器（MCP initialize + tools/list），返回工具数量与名称。输入连接器 key；stdio 首次冷启动可能较慢。失败时按 error 修正参数后重新 dev_mcp_add。", "func": dev_mcp_probe},
    "dev_mcp_discover": {"description": "自助装配 MCP 第 4 步：读取连接器工具并生成【待审批】能力候选（不会自动启用路由）。输入连接器 key。向用户说明候选能力并获确认后，dev_approve(action: mcp_capability, target: key) 再 dev_mcp_decide(decision: approve)。", "func": dev_mcp_discover},
    "dev_mcp_decide": {"description": "自助装配 MCP 第 5 步：批准/拒绝能力候选（approve 需先 dev_approve(action: mcp_capability, target: key)）。多行输入 decision: approve|reject 与 key: <连接器key>。批准后 Agent 才能经 dev_route_connector/dev_mcp_call 自动调用该连接器。", "func": dev_mcp_decide},
    "dev_mcp_remove": {"description": "移除自定义 MCP 连接器（内置预设则禁用），敏感操作需先 dev_approve(action: mcp_server, target: key)。输入连接器 key；会先关闭活动会话。", "func": dev_mcp_remove},
    "dev_mcp_discover_from_need": {"description": "从自然语言需求发现可装配的 MCP 连接器候选（不写盘、不自动启用）。输入需求描述（如『我需要查高铁票的 MCP』），可选多行 web_enabled: true 开启联网。流程：离线精选索引 →（联网时）GitHub 域限定搜索取仓库 README 解析官方命令，候选过 R1-R9 信任闸门。返回后须向用户展示候选，逐条 dev_mcp_add（审批）落盘，再 dev_mcp_probe 探活、dev_mcp_discover 生成能力候选。", "func": dev_mcp_discover_from_need},
    "dev_skill_create": {"description": "起草用户技能（待审批，不会自动启用）。skill 是*可执行行为*，必须经用户确认才激活。多行输入 name/description/body；写入 SKILLS_DIR/.pending/<name>/SKILL.md（草稿不生效）。返回完整正文，代理须向用户完整展示并取得明确同意后，再 dev_skill_approve 激活。", "func": dev_skill_create},
    "dev_skill_approve": {"description": "激活待审批技能：把 .pending/<name>/SKILL.md 移到 SKILLS_DIR 并 reload 生效。仅当用户已明确确认该技能正文安全时调用。输入 name: <技能名>。", "func": dev_skill_approve},
    "dev_skill_reject": {"description": "丢弃待审批技能草稿（不激活、不保留）。输入 name: <技能名>。", "func": dev_skill_reject},
    "search_knowledge": {
        "description": "在已上传的知识库中检索相关文档片段。输入应为检索关键词或问题。",
        "func": search_knowledge,
    },
    "search_assets": {
        "description": "在精选游戏素材目录（Kenney CC0 等）中按关键词/类型/许可筛选素材，适合回答'找素材/美术资源/角色精灵/tileset/UI/音效'类问题。",
        "func": search_assets,
    },
    "calculate": {
        "description": "对数学表达式求值，例如 '23*45+12'；也支持比较运算，例如 '9.9 > 9.11'（结果为「成立/不成立」）。仅支持数字、+ - * / % ** //、括号与 > < >= <= == !=。",
        "func": calculate,
    },
    "web_search": {
        "description": "当知识库不足或需要时效性/外部信息时，联网搜索（DuckDuckGo/百度/Bing 自动故障转移，无需 Key）。GitHub 与 B 站站点限定会优先走专用 API，API 不可用时回退通用搜索；结果附可解释的可信度排序参考，不代表事实已证实。",
        "func": web_search,
    },
    "dev_http_request": {
        "description": "调用你自己的外部业务 API（REST/JSON）。受 EXTERNAL_API_ALLOWLIST 域名白名单约束（防止 SSRF），未配置白名单则拒绝。输入（多行 key: value）：url: <完整URL> 换行 method: <GET/POST/...默认GET> 换行 可选 timeout: <秒> 换行 可选 headers: <单行JSON对象> 换行 可选 body: <请求体，可多行>。返回 HTTP 状态码 + 响应头 + 截断响应体。",
        "func": dev_http_request,
    },
    "python_exec": {
        "description": "在受限子进程中执行 Python 代码并返回输出（超时 12s）。适合数值计算、数据处理、文本变换、小规模绘图数据生成等'让 agent 真正动手'的任务。输入为完整 Python 代码。",
        "func": python_exec,
    },
    "create_artifact": {
        "description": "创建并校验 Word、PDF、PowerPoint 或 Excel 文件，源码和桌面分发版都可用。输入必须是 JSON 对象：format 为 docx/pdf/pptx/xlsx，filename 为文件名，title/subtitle 为标题；docx/pdf 使用 sections（每项可含 heading/level/paragraphs/bullets/table）；pptx 使用 slides（title/bullets）；xlsx 使用 sheets（name/headers/rows）。文件写入当前项目 artifacts 目录，返回实际路径和校验结果。调用前先用 dev_use_skill 读取对应技能。",
        "func": create_artifact,
    },
    "gen_video_prompt": {
        "description": "按 MiniMax H3 的三段结构（integrated_multimodal_description / overall_soundscape / non_diegetic_music）把一段创意描述生成为结构化视频提示词，可直接粘贴进 ComfyUI 的 MiniMaxH3ImageToVideo 节点。输入为自然语言创意（主体/场景/动作/氛围）。",
        "func": gen_video_prompt,
    },
    "search_code": {
        "description": "在已索引的源代码/配置中检索相关函数、类、配置片段。回答'某功能在哪实现/某函数做什么/某配置怎么写'等关于代码库的问题。输入为搜索关键词。",
        "func": search_code,
    },
    "read_file": {
        "description": "读取代码库中的某个文件内容（path 为相对代码根目录的路径或文件名）。需要看完整文件、或某文件细节时用。返回文件内容（截断到 4000 字）。",
        "func": read_file,
    },
    "read_external_file": {
        "description": "读取【项目之外】白名单目录内的文件（受控越界读）。仅允许读取 DOCMIND_EXTERNAL_DIRS（分号分隔的绝对目录）之内的文件，越界拒绝；用于用户授权 AI 访问仓库外的资料（如其它项目、下载目录文档）。输入为文件绝对路径。未配置白名单时提示如何开启。注意：只有在「高权限模式」下越界读才被放行；安全模式下本工具会拒绝。",
        "func": read_external_file,
    },
    "create_external_file": {
        "description": "在白名单目录内【新建】文件（受控越界写，不覆盖已有文件）。仅在「高权限模式」+ 已配置 DOCMIND_EXTERNAL_DIRS 白名单时生效；安全模式或被列出白名单外一律拒绝。用于给其他项目新增文件/模块。输入同 create_file：第一行 path: <绝对路径>，最后 new_text: <文件内容（可多行）>。受单文件 200KB 上限与 .py 语法校验约束；「人工确认」开启时只暂存不落盘。",
        "func": create_external_file,
    },
    "edit_external_file": {
        "description": "在白名单目录内修改【已存在】文件（受控越界写）。仅在「高权限模式」+ 白名单内生效。两种用法：① 局部替换——path + old_text（精确旧片段）+ new_text；② 整体重写——path + new_text（须先用 read_external_file 读过该文件）。受 200KB 上限与 .py 语法校验约束；「人工确认」开启时暂存。",
        "func": edit_external_file,
    },
    "delete_external_file": {
        "description": "删除白名单目录内的【单个文件】（危险操作，受控越界删）。仅在「高权限模式」+ 白名单内生效。输入需含 path: <绝对路径> 与 confirm: yes 明确确认；只删文件不删目录；「人工确认」开启时改为暂存待批准。用于清理其它项目的无用文件。",
        "func": delete_external_file,
    },
    "grep": {
        "description": "在代码库中按正则表达式搜索文本/符号，返回匹配的文件路径与行号。定位某段代码、某变量、某错误出现位置时用。输入为正则表达式。",
        "func": grep,
    },
    "apply_edit": {
        "description": "受控修改代码库中【已存在】的文件（不能新建、不能越界写）。两种用法：① 局部安全替换——提供 path、old_text（要被替换的【精确】旧片段）、new_text（替换后内容），工具在文件中唯一匹配处替换；② 整体重写——只提供 path 与 new_text（省略 old_text），但前提是你已用 read_file 读取过该文件。修改前请先 read_file 确认当前内容；.py 写入后会做语法校验，不通过自动回滚。Action Input 按多行格式写：第一行 path: <路径>，可选 old_text: <精确旧片段>，最后 new_text: <新内容（可多行）>。",
        "func": apply_edit,
    },
    "create_file": {
        "description": "在代码库内【新建】一个文件（不能覆盖已有文件，修改已有文件请用 apply_edit）。用于新增模块/分区（如新建 combat/crit.py）。受路径沙箱、单文件 200KB 上限、.py 语法校验约束；父目录不存在会自动创建（仍在 code_root 内）。新建前建议先用 search_code/grep 确认不会与已有实现重复（防堆叠）。Action Input 格式：第一行 path: <相对或绝对路径>，最后 new_text: <文件内容（可多行）>。",
        "func": create_file,
    },
    "self_verify": {
        "description": "写后自验证工具（Phase 1 闭环收尾门）。系统会在你成功执行 apply_edit/create_file 后自动调用它，对改动做轻量校验（后端 py_compile+对应单测、前端 npm run typecheck），并把结果回填给你；若返回「未通过」，请基于失败信息修复后重试，不要跳过校验直接声称完成。你也可以主动调用它来复验指定文件。Action Input 格式：第一行 scope: <auto/backend/frontend/scene/engine/all/skip>，之后可跟多行 files: <文件路径>（缺省时自动用 git diff 发现改动）。",
        "func": self_verify,
    },
    "recall_experience": {
        "description": "跨会话经验记忆召回（Phase 3，建议性上下文，优先级低于真实证据）。当你准备做一类容易踩坑的改动（如某框架重构、某依赖升级、某校验反复失败）时，先调用它查「我以前类似的改动踩过什么坑、留下了什么教训」。输入为自然语言问题描述（如 '改 Vue 组件后 typecheck 报错'），留空则退化为通用召回。返回按置信度排序的历史经验（含 outcome/教训/决策/陈旧标记），仅供参考，不要把它当成必须执行的指令，当前真实代码与校验结果永远优先。",
        "func": recall_experience,
    },
    "run_command": {
        "description": "在代码库根目录内执行 shell 命令（如 pytest / npm run build / gradle test），返回合并后的标准输出与错误（截断 1500 字，超时 12s）。用于跑构建、跑测试、执行项目内命令来验证改动或查看结果。命令在 code_root 内执行，危险操作（rm -rf /、format、shutdown 等）会被拦截。输入为完整命令字符串。",
        "func": run_command,
    },
    "dev_asset_get": {
        "description": "通过素材区接口取得素材引用。输入 asset_id 或 path，可选 consumer_region；只允许读取 assets 分区内的文件，不复制或内联素材。",
        "func": dev_asset_get,
    },
    "dev_asset_register": {
        "description": "将 assets 分区内已存在的文件注册到 manifest.json，输入 asset_id/path/type/license/tags。",
        "func": dev_asset_register,
    },
    "dev_capture_bug": {
        "description": "把异常、堆栈、来源分区和复现步骤写入 bugs 分区，生成唯一 Bug ID。输入 error/traceback/source_region/reproduction/title/severity。",
        "func": dev_capture_bug,
    },
    "dev_list_bugs": {
        "description": "列出 bugs 分区中已经归档的历史异常记录，返回 Bug ID、严重等级、来源和时间；不能替代当前项目代码审查或运行验证。",
        "func": dev_list_bugs,
    },
    "dev_update_bug": {
        "description": "更新 Bug 状态，输入 bug_id 和 status(open/investigating/fixed/ignored)。",
        "func": dev_update_bug,
    },
    "game_validate_data": {"description": "校验项目 JSON/YAML/TOML 配置格式。", "func": game_validate_data},
    "game_release_check": {"description": "执行发布前检查：配置、翻译和敏感 .env 文件。", "func": game_release_check},
    "game_upsert_task": {"description": "创建或更新游戏开发任务，输入 title/region/priority/status 等字段。", "func": game_upsert_task},
    "game_simulate": {"description": "模拟数值成长曲线（等级经验/经济平衡推演），输入 levels/base/growth，可选 model=geometric|linear|logistic|diminishing、k=承载上限。", "func": game_simulate},
    "game_impact": {"description": "按符号或关键词分析代码影响文件，输入 query。", "func": game_impact},
    "game_playtest": {"description": "在项目根目录运行 Playtest 命令，输入 command/timeout。", "func": game_playtest},
    "game_screenshot": {"description": "截取当前引擎运行画面作为视觉观察：可选输入 target: embedded|foreground（默认 embedded，嵌入窗口不可用时自动改抓前台窗口）。优先使用已启用引擎连接器的截图能力，其次抓取工作台内嵌窗口；JPEG 保存到项目 .docmind/screenshots/ 并回传图片。截图只是某一瞬间的观察，不是代码事实；无窗口/无头环境会明确失败，那时改用运行日志或受控 playtest 证据，不要臆测画面。", "func": game_screenshot},
    "start_workflow": {"description": (
        "当目标是【长链路开发流程】时升级为跨窗口持久开发工作流：满足多阶段/多角色协作、"
        "含写码或命令等副作用阶段、需要人工方案门与审批门、或可能跨窗口中断恢复之一即应使用"
        "（通用开发、游戏、EDA 等任意领域，不局限于游戏）。输入 keyed 多行："
        "goal: 完整可验收目标（也可不写字段名、整段作为目标）；"
        "kind: generic|game|eda（缺省 generic 通用开发；game=游戏开发；eda=原理图/PCB 电子设计）；"
        "web: true|false（缺省继承本轮联网开关）。"
        "它只创建工作流并停在【方案选择门】：工作流卡片会直接在对话中展开，必须由用户在对话内选择方案、"
        "确认任务 DAG 并审批后，多个子代理才开始执行；本工具不直接执行任何有副作用任务，人工门不可跳过。"
        "边界：单个独立子任务用 delegate；一轮内当场并行出结果、无需持久化与人工门用 orchestrate；"
        "需要持久化状态、人工方案/审批门、失败重规划与跨窗口恢复才用 start_workflow。子代理不得调用本工具。"
    ), "func": start_workflow},
    "init_regions": {
        "description": "初始化「分区开发」结构：在代码库根目录建 assets/（素材区）、values/（数值区）、bugs/（bug 区）、behaviors/（角色行为区）等独立子目录（具体分区以 regions.json 为准），每个目录 git init 独立仓库，并生成 DEV_INDEX.md、DOCMIND_RULES.md（分区契约，会被注入 Agent 系统提示，强制越区写被拦截）。用于游戏等分工开发，防止代码堆叠与混乱。输入留空即可；需先配置代码库根目录（/api/ingest_code）。",
        "func": init_regions_tool,
    },
    "dev_list_regions": {
        "description": "列出已配置分区的 key/名称/目录/依赖/导出/脏状态，供你选用正确分区。输入留空即可。在调用 dev_region_read/edit/verify/commit/refactor 前先调用它确认分区 key。",
        "func": dev_list_regions,
    },
    "dev_region_read": {
        "description": "读取某分区内的文件（分区作用域，越区读被拒）。输入：第一行 region: <分区key>，第二行 path: <分区内相对路径>。读取后该文件可被 dev_region_edit 整体重写（满足先读后写护栏）。",
        "func": dev_region_read,
    },
    "dev_region_edit": {
        "description": "受控修改/新建某分区内的文件（分区作用域，越区写被拒；复用 apply_edit/create_file 的全部护栏：先读后写、.py 语法校验、200KB 上限、人工确认）。输入格式：第一行 region: <分区key>，第二行 path: <分区内相对路径>，可选 old_text: <精确旧片段>，最后 new_text: <新内容（可多行）>。提供 old_text 做局部安全替换；省略 old_text 且已 dev_region_read 过该文件则整体重写；文件不存在则按新建处理。",
        "func": dev_region_edit,
    },
    "dev_region_verify": {
        "description": "校验单个分区：若该分区配置了 verify 命令（regions.json 的 verify 字段）则在分区目录内执行；否则检查其导出接口文件是否齐全。输入：region: <分区key>。verify 命令建议写为脚本（如 `pytest tests/`），不要写 `python -c ...`（会被安全策略拦截）。",
        "func": dev_region_verify,
    },
    "dev_refactor": {
        "description": "跨分区安全搬移：把某分区内的文件移动到另一分区（受依赖方向约束，不破坏 git 跟踪）。输入：src_region: <源key>、src_path: <源相对路径>、dst_region: <目标key>、dst_path: <目标相对路径>。目标不能已存在（不覆盖）；搬移后从源分区 git 移除源文件。禁止把代码挪进「已依赖源分区」的分区（避免循环耦合/倒置分层）。",
        "func": dev_refactor,
    },
    "dev_commit": {
        "description": "提交单个分区的改动（该分区独立 git 仓库内 commit）。输入：region: <分区key> 换行 message: <提交说明>。",
        "func": dev_commit,
    },
    "dev_verify_contracts": {
        "description": "校验全部分区的契约：依赖方向无环、被依赖分区导出文件存在、依赖目标存在。输入留空即可。建议在大幅改动分区前后调用，确认架构约束未被破坏。",
        "func": dev_verify_contracts,
    },
    "dev_rebuild_index": {
        "description": "依据当前分区配置重算 DEV_INDEX.md（分区目录/文件数/依赖变动后调用）。输入留空即可。",
        "func": dev_rebuild_index,
    },
    "dev_commit_all": {
        "description": "把所有分区的改动各提交一次，并记进 dev_changesets.jsonl 作为一次绑定变更集（可整体回滚）。输入：message: <本次功能改动说明>。返回变更集 id 与各分区提交哈希。",
        "func": dev_commit_all,
    },
    "dev_list_changesets": {
        "description": "列出已记录的跨区变更集（dev_changesets.jsonl）。输入留空即可。返回各变更集 id/message/涉及分区，供 dev_rollback_changeset 选用。",
        "func": dev_list_changesets,
    },
    "dev_rollback_changeset": {
        "description": "整体回滚某变更集：对每个分区 revert 其记录的 commit（生成新提交撤销改动）。输入：changeset: <变更集id> 或 id: <变更集id>。先用 dev_list_changesets 查看 id。",
        "func": dev_rollback_changeset,
    },
    "list_dir": {
        "description": "浏览代码库内的目录结构（限定 code_root，供 Agent 研判代码库组织方式）。输入：目录相对路径，可直接写 behaviors/ 或 behaviors（也兼容 path: behaviors/）；留空列根目录。返回子项（目录/文件）及大小/类型。在研判分区方案前先用它勘察顶层有哪些模块/资源目录。",
        "func": list_dir,
    },
    "dev_propose_regions": {
        "description": "依据真实代码库结构，由你研判分区方案（默认 8 个分区仅作初始建议，实际分区由你判断）。输入留空即可。返回结构分析 + 建议分区清单（每区带 detected 证据与 included 启用建议）+ 代码库特有的可独立模块 + 中文结论。先用 list_dir 勘察，再调用它拿基线，据 detected/included 增删分区，最后用 dev_apply_regions 落地。",
        "func": dev_propose_regions,
    },
    "dev_apply_regions": {
        "description": "应用你研判后的自定义分区方案：把给定分区清单写入 regions.json 并初始化（建目录/每区 git/导出桩/规则），此后写操作被约束在这些分区内。输入：regions: <JSON 数组，或 {\"regions\":[...]} 对象>；每项至少含 key 与 dir，可选 name/desc/depends_on/exports/verify。可由 dev_propose_regions 的产出裁减得到。",
        "func": dev_apply_regions,
    },
    "dev_add_region": {
        "description": "向现有分区配置追加（或覆盖同名）一个分区并立即初始化（建目录/每区 git/规则）。输入：key: <分区key> 换行 dir: <目录> 换行 name: <中文名> 换行 [desc:] [access:] [depends_on:]（逗号分隔） [exports:]（逗号分隔）。用于研判后按需增补单个分区，无需重传整个方案。",
        "func": dev_add_region,
    },
    "dev_approve": {
        "description": "审批敏感操作前必须调用：记录一次审批，30 分钟内该操作放行。输入：action: <commit_region|commit_all|rollback_changeset|apply_regions> 换行 target: <对象>。target 精确匹配、不是通配符：commit_region 传分区 key（不能用 *）、rollback_changeset 传变更集 id、commit_all/apply_regions 固定传 *。在调用 dev_commit/dev_commit_all/dev_rollback_changeset/dev_apply_regions/dev_add_region 之前先调用本工具；若它们返回 blocked/approval_required，先调用本工具再重试，不要绕过。",
        "func": dev_approve,
    },
    "dev_approval_status": {
        "description": "查询某敏感操作当前是否已通过审批。输入：action: <操作名> 换行 target: <对象>(默认 *)。返回已通过/未通过，用于决定是否需要先调用 dev_approve。",
        "func": dev_approval_status,
    },
    "dev_install_tool": {
        "description": "缺失工具的隔离安装：输入 manager: python|node 换行 package: 包名 换行 version: 可选版本 换行 fallback_tools: 失败时可替代的内置工具。只安装到项目 .docmind/tool_envs，必须先用 dev_approve 审批 action=install_tool、target=<manager>:<package[==version]>；安装后会验证实际版本和沙箱路径，失败会返回替代方案和审计记录。",
        "func": dev_install_tool,
    },
    "dev_tool_install_audit": {
        "description": "查看项目内工具安装审计记录。输入可选 limit: 50；不会执行安装。",
        "func": dev_tool_install_audit,
    },
}

# Convert the built-in registry once at import time.  Extensions and tests may
# still add legacy dict entries later; consumers call ``coerce_tool_spec`` at
# their boundary so those remain compatible.
upgrade_registry(TOOLS)


def tool_schemas(names=None, registry=None):
    """把 TOOLS 注册表导出为 OpenAI 风格函数 schema（原生 function-calling 用）。

    所有工具统一暴露单个 `input` 字符串参数，与文本协议的 `Action Input` **同形**，
    因此原生通道与文本通道共用同一套入参归一化（`_normalize_tool_arg`）与全部护栏。
    `names` 可限定子集（子代理的工具白名单就用它）。
    """
    out = []
    source = registry if registry is not None else TOOLS
    for name, raw_meta in source.items():
        if names and name not in names:
            continue
        meta = coerce_tool_spec(name, raw_meta)
        desc = (meta.description or "").strip()
        parameters = dict(meta.input_schema)
        properties = dict(parameters.get("properties") or {})
        properties.setdefault("input", {
            "type": "string",
            "description": "兼容旧调用的原始 Action Input；与结构化字段二选一。",
        })
        parameters["properties"] = properties
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": desc[:1024],
                "parameters": parameters,
            },
        })
    return out
