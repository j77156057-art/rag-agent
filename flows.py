"""阶段 3b｜AI 工具流编排器（后端）。

把「常用动作链」沉淀为可保存、可复用的「流水线模板」，并支持服务端逐步执行。
本模块只做两件事：

1) **流程定义校验 + 读写**（纯逻辑，显式收 ``root``，可脱离服务单测）：
   - 节点动作必须在**受控动作白名单**（``_CONTROLLED_ACTIONS``）内；
   - 节点参数只允许该动作声明的安全字段（分区 key / 分区内相对路径 / 变更集 id /
     短消息 / 文本内容），路径类字段做目录穿越防护；
   - 流程读写 ``<root>/.docmind/flows/*.json``。

2) **单步受控执行**（``execute_step``）：只调用**既有受控能力**——
   分区内改码（复用 tools.dev_region_edit 的沙箱/先读后写/语法校验护栏）、
   分区校验（regions.run_verify）、契约校验（regions.verify_contracts）、
   变更集提交/回滚（regions.commit_all / rollback_changeset）、Web 导出（web_export）。
   **不新增任何绕过分区约束的写路径，不改 agent 内核，不直连 agent。**

隐私边界：执行过程会写 agent_trace（3a 画布可见），但**只记元数据**（动作名 / 参数字数 /
耗时 / 成败），绝不把问题原文、代码或文件路径正文塞进 trace——与 agent_trace 既有原则一致。

设计约定（对齐 regions.py / workbench_fs.py）：纯逻辑函数显式收 ``root``；
``APIRouter`` 只负责从运行时取 code_root、做 HTTP 映射。
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from config import get_runtime, CODE_ROOT

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
FLOWS_SUBDIR = os.path.join(".docmind", "flows")   # 相对代码库根目录
_MAX_FLOWS = 200          # 单项目流程模板数量上限
_MAX_NODES = 40           # 单流程节点数上限
_MAX_TEXT = 200_000       # 文本内容（new_text / old_text）字符上限
_MAX_MSG = 200            # 短消息（message）字符上限
_MAX_PATH = 512           # 分区内相对路径长度上限

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")           # 流程 id / 节点 id
_REGION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{1,60}$")   # 分区 key
_CHANGESET_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")    # 变更集 id

# 字段类型 -> 校验（见 _validate_field_value）
_KIND_REGION = "region"
_KIND_RELPATH = "relpath"
_KIND_CHANGESET = "changeset"
_KIND_MESSAGE = "message"
_KIND_TEXT = "text"


# ---------------------------------------------------------------------------
# 受控动作白名单
# 每个动作声明：label（中文名）/ glyph（节点图标字）/ cat（类别，供前端配色）/ mutating（是否写盘）
# / summary（说明）/ fields（允许的参数，白名单之外一律拒绝）。
# 动作名与 tools.py 的受控工具名一致，便于 trace / 3a 画布复用同一套中文映射。
# ---------------------------------------------------------------------------
def _field(name, label, kind, required=False, placeholder=""):
    return {"name": name, "label": label, "kind": kind,
            "required": bool(required), "placeholder": placeholder}


_CONTROLLED_ACTIONS = {
    "dev_list_regions": {
        "label": "列出分区", "glyph": "区", "cat": "region", "mutating": False,
        "summary": "读取全部已配置分区（key / 名称 / 目录 / 依赖 / 脏状态），用于定位。",
        "fields": [],
    },
    "dev_list_changesets": {
        "label": "变更集列表", "glyph": "集", "cat": "region", "mutating": False,
        "summary": "列出已记录的跨区变更集，便于选择回滚目标。",
        "fields": [],
    },
    "dev_verify_contracts": {
        "label": "契约校验", "glyph": "契", "cat": "region", "mutating": False,
        "summary": "校验全部分区的依赖方向无环、导出接口齐全。",
        "fields": [],
    },
    "dev_region_verify": {
        "label": "分区校验", "glyph": "运", "cat": "run", "mutating": False,
        "summary": "在指定分区目录内执行其 verify 命令（或校验导出接口）。",
        "fields": [_field("region", "分区 key", _KIND_REGION, required=True, placeholder="values")],
    },
    "dev_region_read": {
        "label": "读取分区文件", "glyph": "读", "cat": "retrieve", "mutating": False,
        "summary": "读取某分区内的文件（分区作用域，越区读被拒）。",
        "fields": [
            _field("region", "分区 key", _KIND_REGION, required=True, placeholder="values"),
            _field("path", "分区内相对路径", _KIND_RELPATH, required=True, placeholder="balance.json"),
        ],
    },
    "dev_region_edit": {
        "label": "分区内改码", "glyph": "写", "cat": "write", "mutating": True,
        "summary": "受控修改/新建分区内文件（复用先读后写、越区拒绝、.py 语法校验护栏）。",
        "fields": [
            _field("region", "分区 key", _KIND_REGION, required=True, placeholder="values"),
            _field("path", "分区内相对路径", _KIND_RELPATH, required=True, placeholder="balance.json"),
            _field("new_text", "新内容", _KIND_TEXT, required=True, placeholder="替换后的完整片段…"),
            _field("old_text", "精确旧片段（局部替换）", _KIND_TEXT, required=False, placeholder="要被替换的原文…"),
        ],
    },
    "dev_commit_all": {
        "label": "提交变更集", "glyph": "提", "cat": "region", "mutating": True,
        "summary": "把所有分区的改动各提交一次并绑定为一个可整体回滚的变更集。",
        "fields": [_field("message", "提交说明", _KIND_MESSAGE, required=False, placeholder="docmind: …")],
    },
    "dev_rollback_changeset": {
        "label": "回滚变更集", "glyph": "滚", "cat": "region", "mutating": True,
        "summary": "整体回滚某变更集（对每个分区 revert 其提交）。changeset 留空表示「本次运行刚创建的那个」。",
        "fields": [_field("changeset", "变更集 id", _KIND_CHANGESET, required=False, placeholder="留空=本次运行创建的")],
    },
    "engine_web_export": {
        "label": "Web 导出试玩", "glyph": "玩", "cat": "run", "mutating": False,
        "summary": "把 Godot 项目导出为 Web 产物供 iframe 试玩（复用既有导出能力）。",
        "fields": [],
    },
}


def action_catalog():
    """返回受控动作清单（供前端渲染节点面板；前端也可自带一份镜像）。"""
    out = []
    for key, spec in _CONTROLLED_ACTIONS.items():
        out.append({
            "action": key, "label": spec["label"], "glyph": spec["glyph"],
            "cat": spec["cat"], "mutating": spec["mutating"], "summary": spec["summary"],
            "fields": [dict(f) for f in spec["fields"]],
        })
    return out


# ---------------------------------------------------------------------------
# 路径 / 字段校验
# ---------------------------------------------------------------------------
def _safe_root(root):
    """返回规范化后的绝对根目录；非法（空 / 不存在）返回 None（不抛异常，便于纯逻辑测试）。"""
    root = (root or "").strip()
    if not root or not os.path.isdir(root):
        return None
    return os.path.abspath(root)


def _validate_relpath(p):
    """校验「分区内相对路径」：拒绝绝对路径/盘符/URL/``..`` 穿越/控制字符。返回错误串或 ''。

    控制字符（``\\x00``-``\\x1f``、``\\x7f``，含换行 ``\\n`` / 回车 ``\\r`` / 制表 ``\\t``）
    必须拒绝：path 会被拼进 tools 的 keyed 输入（``path: <值>``），换行会让后续 ``region:``
    之类出现在「行首」，被 `_parse_keyed` 误当成新字段——即用一个合法 region 通过校验、
    却让实际执行写入另一个 region（白名单前置旁路）。
    """
    if not isinstance(p, str) or not p.strip():
        return "路径不能为空。"
    # 控制字符在**原始串**上检查（先于 strip）：杜绝「嵌入换行」造成的 keyed 字段注入。
    if any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in p):
        return "路径包含控制字符（换行 / 回车 / 制表等），已拒绝。"
    s = p.replace("\\", "/").strip()
    if len(s) > _MAX_PATH:
        return f"路径过长（>{_MAX_PATH}）。"
    if s.startswith("/") or "://" in s or (len(s) >= 2 and s[1] == ":"):
        return "只允许分区内的相对路径，拒绝绝对路径/盘符/URL。"
    if s.startswith("~"):
        return "路径不能以 ~ 开头。"
    parts = s.split("/")
    if any(part == ".." for part in parts):
        return "路径越界（包含 ..），已拒绝。"
    return ""


def _validate_field_value(field, value):
    """按字段 kind 校验字符串值；返回错误串或 ''。"""
    kind = field["kind"]
    if kind == _KIND_REGION:
        if not _REGION_KEY_RE.match(value):
            return "分区 key 非法（仅允许字母/数字/下划线/连字符）。"
        return ""
    if kind == _KIND_RELPATH:
        return _validate_relpath(value)
    if kind == _KIND_CHANGESET:
        if not _CHANGESET_RE.match(value):
            return "变更集 id 非法（仅允许字母/数字/下划线/连字符）。"
        return ""
    if kind == _KIND_MESSAGE:
        if len(value) > _MAX_MSG:
            return f"内容过长（>{_MAX_MSG}）。"
        return ""
    if kind == _KIND_TEXT:
        if len(value) > _MAX_TEXT:
            return f"内容过长（>{_MAX_TEXT} 字符）。"
        return ""
    return f"未知字段类型：{kind}"


def _coerce_str(value):
    """把标量参数归一为字符串；容器类型返回 None（判为非法）。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return None


def validate_step(action, params):
    """校验单个步骤（动作 + 参数）。返回 {ok, params(清洗后), error}。"""
    action = (action or "").strip()
    if action not in _CONTROLLED_ACTIONS:
        return {"ok": False, "params": {}, "error": f"动作「{action or '(空)'}」不在受控动作白名单内。"}
    spec = _CONTROLLED_ACTIONS[action]
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return {"ok": False, "params": {}, "error": "参数必须是对象（键值对）。"}
    allowed = {f["name"]: f for f in spec["fields"]}
    cleaned = {}
    for key, raw in params.items():
        f = allowed.get(key)
        if f is None:
            return {"ok": False, "params": {}, "error": f"参数「{key}」不是该动作允许的字段。"}
        if raw is None:
            continue
        val = _coerce_str(raw)
        if val is None:
            return {"ok": False, "params": {}, "error": f"参数「{key}」类型非法，必须是字符串。"}
        err = _validate_field_value(f, val)
        if err:
            return {"ok": False, "params": {}, "error": f"参数「{key}」：{err}"}
        cleaned[key] = val
    for name, f in allowed.items():
        if f.get("required") and name not in cleaned:
            return {"ok": False, "params": {}, "error": f"缺少必填参数「{name}」（{f['label']}）。"}
    return {"ok": True, "params": cleaned, "error": ""}


# ---------------------------------------------------------------------------
# 流程定义校验 / 读写（纯逻辑，显式收 root）
# ---------------------------------------------------------------------------
def validate_flow_definition(definition):
    """校验并归一化一个流程定义。返回 {ok, errors[], flow}。

    非法时 flow=None；合法时 flow 已补全 id / name / created_at（后者由 save_flow 落）。
    """
    errors = []
    if not isinstance(definition, dict):
        return {"ok": False, "errors": ["流程定义必须是对象。"], "flow": None}

    name = str(definition.get("name") or "").strip()[:80] or "未命名流程"
    desc = str(definition.get("desc") or "").strip()[:300]
    raw_id = str(definition.get("id") or "").strip()
    if raw_id and not _ID_RE.match(raw_id):
        errors.append("流程 id 含非法字符（仅允许字母/数字/下划线/连字符）。")
    flow_id = raw_id if raw_id and _ID_RE.match(raw_id) else ("flow-" + uuid.uuid4().hex[:10])

    nodes_in = definition.get("nodes")
    if not isinstance(nodes_in, list) or not nodes_in:
        errors.append("流程至少需要一个节点。")
        nodes_in = []
    if len(nodes_in) > _MAX_NODES:
        errors.append(f"节点数超过上限（{_MAX_NODES}）。")
        nodes_in = nodes_in[:_MAX_NODES]

    nodes, seen_ids = [], set()
    for i, n in enumerate(nodes_in):
        if not isinstance(n, dict):
            errors.append(f"第 {i + 1} 个节点不是对象。")
            continue
        nid = str(n.get("id") or "").strip()
        if not nid or not _ID_RE.match(nid):
            errors.append(f"第 {i + 1} 个节点 id 非法（仅允许字母/数字/下划线/连字符）。")
            continue
        if nid in seen_ids:
            errors.append(f"节点 id 重复：{nid}")
            continue
        action = str(n.get("action") or "").strip()
        if action not in _CONTROLLED_ACTIONS:
            errors.append(f"节点 {nid} 的动作「{action or '(空)'}」不在受控动作白名单内。")
            continue
        spec = _CONTROLLED_ACTIONS[action]
        label = str(n.get("label") or "").strip()[:40] or spec["label"]
        v = validate_step(action, n.get("params") or {})
        if not v["ok"]:
            errors.append(f"节点 {nid}（{spec['label']}）参数非法：{v['error']}")
            continue
        node = {"id": nid, "action": action, "label": label, "params": v["params"]}
        x, y = n.get("x"), n.get("y")
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            node["x"], node["y"] = float(x), float(y)
        nodes.append(node)
        seen_ids.add(nid)

    edges_in = definition.get("edges")
    edges, seen_pairs = [], set()
    if isinstance(edges_in, list):
        for e in edges_in[: _MAX_NODES * 2]:
            if not isinstance(e, dict):
                continue
            s = str(e.get("source") or "").strip()
            t = str(e.get("target") or "").strip()
            if s in seen_ids and t in seen_ids and s != t and (s, t) not in seen_pairs:
                edges.append({"source": s, "target": t})
                seen_pairs.add((s, t))

    flow = {"id": flow_id, "name": name, "desc": desc, "nodes": nodes, "edges": edges}
    if errors:
        return {"ok": False, "errors": errors, "flow": None}
    return {"ok": True, "errors": [], "flow": flow}


def _flows_dir(root_abs):
    return os.path.join(root_abs, FLOWS_SUBDIR)


def load_flows(root):
    """列出全部流程定义（按文件名排序）。目录不存在返回空列表；损坏文件跳过。"""
    root_abs = _safe_root(root)
    if not root_abs:
        return []
    d = _flows_dir(root_abs)
    if not os.path.isdir(d):
        return []
    out = []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    for name in names:
        if not name.endswith(".json"):
            continue
        p = os.path.join(d, name)
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("id"):
            out.append(data)
    return out


def load_flow(root, flow_id):
    """读取单个流程定义；不存在/非法返回 None。"""
    root_abs = _safe_root(root)
    flow_id = (flow_id or "").strip()
    if not root_abs or not _ID_RE.match(flow_id):
        return None
    p = os.path.join(_flows_dir(root_abs), flow_id + ".json")
    if not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("id") == flow_id else None


def save_flow(root, definition):
    """校验并原子写入一个流程定义。返回 {ok, flow?, errors[], error?}。"""
    root_abs = _safe_root(root)
    if not root_abs:
        return {"ok": False, "errors": ["未配置代码库根目录或目录不存在。"],
                "error": "未配置代码库根目录或目录不存在。"}
    v = validate_flow_definition(definition)
    if not v["ok"]:
        return {"ok": False, "errors": v["errors"], "error": "；".join(v["errors"])}
    flow = v["flow"]
    d = _flows_dir(root_abs)
    try:
        os.makedirs(d, exist_ok=True)
    except OSError as e:
        return {"ok": False, "errors": [f"创建流程目录失败：{e}"], "error": f"创建流程目录失败：{e}"}
    target = os.path.join(d, flow["id"] + ".json")
    existed = os.path.isfile(target)
    if not existed:
        try:
            count = sum(1 for n in os.listdir(d) if n.endswith(".json") and os.path.isfile(os.path.join(d, n)))
        except OSError:
            count = 0
        if count >= _MAX_FLOWS:
            return {"ok": False, "errors": [f"流程数量已达上限（{_MAX_FLOWS}）。"],
                    "error": f"流程数量已达上限（{_MAX_FLOWS}）。"}
    now = datetime.now().isoformat(timespec="seconds")
    created_at = now
    if existed:
        try:
            with open(target, encoding="utf-8") as f:
                old = json.load(f)
            if isinstance(old, dict) and old.get("created_at"):
                created_at = old["created_at"]
        except (OSError, ValueError):
            pass
    flow["created_at"] = created_at
    flow["updated_at"] = now
    tmp = target + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(flow, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except OSError as e:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return {"ok": False, "errors": [f"写入流程失败：{e}"], "error": f"写入流程失败：{e}"}
    return {"ok": True, "flow": flow, "errors": []}


def delete_flow(root, flow_id):
    """删除一个流程定义。返回 {ok, deleted?, error?}。"""
    root_abs = _safe_root(root)
    if not root_abs:
        return {"ok": False, "error": "未配置代码库根目录或目录不存在。"}
    flow_id = (flow_id or "").strip()
    if not _ID_RE.match(flow_id):
        return {"ok": False, "error": "流程 id 非法。"}
    p = os.path.join(_flows_dir(root_abs), flow_id + ".json")
    if not os.path.isfile(p):
        return {"ok": False, "error": "流程不存在。"}
    try:
        os.remove(p)
    except OSError as e:
        return {"ok": False, "error": f"删除失败：{e}"}
    return {"ok": True, "deleted": flow_id}


# ---------------------------------------------------------------------------
# 受控执行端点（只复用既有受控能力，不新增写路径）
# 端点签名统一为 fn(root, params) -> {ok, output, error?, detail?}
# ---------------------------------------------------------------------------
def _build_edit_arg(region, path, new_text, old_text=None):
    """拼装 tools.dev_region_edit 的 keyed 输入（复用其分区/沙箱/语法护栏）。"""
    parts = [f"region: {region}", f"path: {path}"]
    if old_text is not None:
        parts.append(f"old_text: {old_text}")
    parts.append(f"new_text: {new_text}")
    return "\n".join(parts)


_EDIT_RESERVED = ("region", "path", "old_text", "new_text")


def _has_reserved_line(text):
    """内容中是否出现与 keyed 字段同名的行首标记（会导致解析截断）。"""
    for fname in _EDIT_RESERVED:
        if re.search(r"(?m)^\s*" + fname + r"\s*:", text or ""):
            return fname
    return ""


def _ok(output, detail=None):
    return {"ok": True, "output": output, "detail": detail, "error": ""}


def _fail(error, output="", detail=None):
    return {"ok": False, "output": output or error, "detail": detail, "error": error}


def _ep_dev_list_regions(root, params):
    from regions import list_regions
    rows = list_regions(root)
    lines = [f"共 {len(rows)} 个分区："]
    for r in rows:
        dirty = "（有未提交改动）" if r.get("dirty") else ""
        lines.append(f"- {r['key']}｜{r['name']}（{r['dir']}/）：{r['desc']}{dirty}")
    return _ok("\n".join(lines), {"regions": rows})


def _ep_dev_list_changesets(root, params):
    from regions import list_changesets
    cs = list_changesets(root)
    if not cs:
        return _ok("暂无已记录的变更集。", {"changesets": []})
    lines = [f"共 {len(cs)} 个变更集："]
    for c in cs:
        lines.append(f"- id={c.get('id')} message={c.get('message', '')} 分区={list((c.get('commits') or {}).keys())}")
    return _ok("\n".join(lines), {"changesets": cs})


def _ep_dev_verify_contracts(root, params):
    from regions import verify_contracts
    r = verify_contracts(root)
    ok = bool(r.get("ok"))
    if ok:
        return _ok("契约校验通过（依赖方向无环、导出接口齐全）。", {"graph": r.get("graph") or {}})
    return _fail("契约校验失败：\n- " + "\n- ".join(r.get("errors") or []),
                 detail={"errors": r.get("errors") or [], "graph": r.get("graph") or {}})


def _ep_dev_region_verify(root, params):
    from regions import get_region_map, run_verify
    region = params["region"]
    rmap = get_region_map(root)
    if region not in rmap:
        return _fail(f"未知分区：{region}（可选：{', '.join(rmap.keys())}）")
    meta = rmap[region]
    region_abs = os.path.normpath(os.path.join(root, meta["dir"]))
    if not os.path.isdir(region_abs):
        return _fail(f"{meta['name']} 目录尚未创建。")
    verify_cmd = (meta.get("verify") or "").strip()
    if verify_cmd:
        ok, output = run_verify(region_abs, verify_cmd)
        if ok is not None or output is not None:
            head = f"运行 {meta['name']} 的内置校验（{verify_cmd}）：\n"
            return (_ok(head + str(output)) if ok else _fail(head + str(output)))
        # 自定义命令：复用分区命令执行器（带安全黑名单）
        from tools import _run_region_cmd
        out = _run_region_cmd(region_abs, verify_cmd)
        good = "[exit code 0]" in (out or "")
        head = f"运行 {meta['name']} 的 verify 命令 `{verify_cmd}`：\n"
        return (_ok(head + out) if good else _fail(head + out))
    exports = meta.get("exports") or []
    if not exports:
        return _ok(f"{meta['name']} 未配置 verify 命令与导出接口，跳过（OK）。")
    miss = [ex for ex in exports if not os.path.isfile(os.path.join(region_abs, ex))]
    if miss:
        return _fail(f"{meta['name']} 导出接口缺失：{', '.join(miss)}", detail={"missing": miss})
    return _ok(f"{meta['name']} 导出接口齐全（{', '.join(exports)}），契约自检通过。")


def _ep_dev_region_read(root, params):
    from regions import get_region_map
    from tools import _resolve_region_path
    rmap = get_region_map(root)
    target, reason = _resolve_region_path(root, rmap, params["region"], params["path"])
    if target is None:
        return _fail(reason)
    if not os.path.isfile(target):
        return _fail(f"文件不存在：{params['path']}（在分区 {params['region']} 内）。")
    try:
        with open(target, encoding="utf-8", errors="ignore") as f:
            content = f.read()
    except OSError as e:
        return _fail(f"读取失败：{e}")
    return _ok(content, {"region": params["region"], "path": params["path"], "chars": len(content)})


def _ep_dev_region_edit(root, params):
    # 纵深防御：tools 的 keyed 输入以行首 "key:" 划分字段；region/path 与内容若含同名字段
    # 行会被 `_parse_keyed` 误解析（region 已被标识符白名单挡住，path 由 _validate_relpath
    # 挡控制字符，这里再显式拦一次 path / 内容，确保「用合法 region 校验、却写到别的 region」
    # 的旁路彻底关闭）。region 值本身已由 validate_step 保证是安全标识符。
    for label, value in (("path", params.get("path")),
                         ("new_text", params.get("new_text")),
                         ("old_text", params.get("old_text"))):
        if value is None:
            continue
        bad = _has_reserved_line(value)
        if bad:
            return _fail(f"{label} 含保留字段标记行「{bad}:」，无法安全解析；请调整该行。")
    import tools
    arg = _build_edit_arg(params["region"], params["path"], params["new_text"], params.get("old_text"))
    out = tools.dev_region_edit(arg)
    ok = ("已写入" in out) or ("已创建" in out)
    if ok:
        return _ok(out)
    return _fail(out)


def _ep_dev_commit_all(root, params):
    from regions import commit_all
    message = (params.get("message") or "docmind: 流程编排提交").strip() or "docmind: 流程编排提交"
    ok, info = commit_all(root, message)
    info = info if isinstance(info, dict) else {}
    if not ok:
        errs = info.get("errors") or {}
        if errs:
            out = "变更集提交失败：\n" + "\n".join(f"- {k}: {v}" for k, v in errs.items())
        else:
            out = "变更集提交失败。"
        return _fail(out, detail={"commits": info.get("commits") or {}})
    cs_id = info.get("id") or ""
    if not cs_id:
        return _ok(info.get("message", "没有可提交的分区改动。"), {"changeset": None, "commits": {}})
    return _ok(f"已创建变更集 {cs_id}（message={message}）。",
               {"changeset": cs_id, "commits": info.get("commits") or {}})


def _ep_dev_rollback_changeset(root, params):
    from regions import rollback_changeset
    cs_id = (params.get("changeset") or "").strip()
    if not cs_id:
        return _fail("缺少变更集 id（changeset 为空）。若要用本次运行刚创建的变更集，请在运行器里绑定。")
    ok, detail = rollback_changeset(root, cs_id)
    if not ok:
        text = detail if isinstance(detail, str) else "\n- ".join(str(x) for x in (detail or []))
        return _fail(text)
    return _ok(f"已回滚变更集 {cs_id}。", {"changeset": cs_id})


def _ep_engine_web_export(root, params):
    from game_workbench import engine_config, _resolve_engine_executable
    import web_export
    cfg = engine_config(root) or {}
    if str(cfg.get("engine") or "godot").lower() != "godot":
        return _fail("当前引擎不是 Godot，Web 导出试玩不可用。")
    exe = _resolve_engine_executable("godot", cfg.get("executable", "godot"))
    if not exe:
        return _fail("未找到 Godot 可执行文件，无法导出 Web 产物。")
    res = web_export.export_web(root, exe, 300) or {}
    ok = bool(res.get("ok"))
    if ok:
        return _ok(f"Web 导出完成：{res.get('url') or ''}",
                   {k: res.get(k) for k in ("url", "token", "elapsed", "html_injected", "preset_added")})
    return _fail(res.get("error") or res.get("message") or "Web 导出失败。",
                 detail={"templates": res.get("templates")})


_ENDPOINTS = {
    "dev_list_regions": _ep_dev_list_regions,
    "dev_list_changesets": _ep_dev_list_changesets,
    "dev_verify_contracts": _ep_dev_verify_contracts,
    "dev_region_verify": _ep_dev_region_verify,
    "dev_region_read": _ep_dev_region_read,
    "dev_region_edit": _ep_dev_region_edit,
    "dev_commit_all": _ep_dev_commit_all,
    "dev_rollback_changeset": _ep_dev_rollback_changeset,
    "engine_web_export": _ep_engine_web_export,
}


def execute_step(root, action, params, endpoints=None):
    """执行单个受控步骤。

    endpoints 可注入（默认用内置受控端点）——测试可传假端点，避免真实写盘。
    返回 {ok, action, status, output, detail, error, latency_ms}。
    """
    t0 = time.monotonic()
    action = (action or "").strip()
    if action not in _CONTROLLED_ACTIONS:
        return {"ok": False, "action": action, "status": "fail",
                "error": f"动作「{action or '(空)'}」不在受控动作白名单内。",
                "output": "", "detail": None, "latency_ms": 0}
    v = validate_step(action, params)
    if not v["ok"]:
        return {"ok": False, "action": action, "status": "fail", "error": v["error"],
                "output": "", "detail": None, "latency_ms": int((time.monotonic() - t0) * 1000)}
    eps = _ENDPOINTS if endpoints is None else endpoints
    fn = eps.get(action)
    if fn is None:
        return {"ok": False, "action": action, "status": "fail",
                "error": f"动作 {action} 未注册执行端点。",
                "output": "", "detail": None, "latency_ms": int((time.monotonic() - t0) * 1000)}
    try:
        res = fn(root, v["params"]) or {}
    except Exception as e:  # noqa: BLE001  端点异常一律转成步骤失败，不搞挂整条流水线
        return {"ok": False, "action": action, "status": "fail",
                "error": f"{type(e).__name__}: {e}", "output": "",
                "detail": None, "latency_ms": int((time.monotonic() - t0) * 1000)}
    ok = bool(res.get("ok"))
    output = str(res.get("output") or "")
    return {
        "ok": ok, "action": action, "status": "ok" if ok else "fail",
        "output": output, "detail": res.get("detail"),
        "error": "" if ok else (res.get("error") or output or "步骤执行失败。"),
        "latency_ms": int((time.monotonic() - t0) * 1000),
    }


# ---------------------------------------------------------------------------
# trace：把流程执行的每一步写进 agent_trace（3a 画布可见）。
# 隐私：只记元数据（动作名 / 参数字数 / 返回字数 / 耗时 / 成败），不记正文、路径、代码。
# 一个「运行」= 一个 trace 回合（turn），其 steps 即流水线各步；失败或标 finish 时落盘。
# ---------------------------------------------------------------------------
_RUNS: dict = {}
_RUNS_LOCK = threading.Lock()
_RUN_TTL_S = 3600
_RUN_MAX = 200


def _step_meta(action, ok, latency_ms, arg_chars, obs_chars):
    return {"action": action, "arg_chars": int(arg_chars), "latency_ms": int(latency_ms),
            "obs_chars": int(obs_chars), "ok": bool(ok)}


def _classify_error(error):
    """把错误原文降级为纯元数据（分类标签 + 字符数）——方案 §9.5 隐私边界。

    trace 记录**只能**落元数据：绝不写原文 / 文件路径 / 代码片段。此函数读取
    内存中的错误文本做粗分类，返回的字典只含固定标签与长度，不含任何原始字符。
    """
    if not error:
        return ""
    e = str(error)
    low = e.lower()
    if "syntaxerror" in low or "syntax" in low or "语法" in e or "语法错误" in e:
        kind = "syntax_error"
    elif "not found" in low or "no such" in low or "不存在" in e or "找不到" in e:
        kind = "not_found"
    elif ("拒绝" in e or "越界" in e or "穿越" in e or "不允许" in e
          or "forbidden" in low or "denied" in low or "not allowed" in low):
        kind = "rejected"
    elif "超时" in e or "timeout" in low:
        kind = "timeout"
    elif "traceback" in low or "exception" in low:
        kind = "exception"
    else:
        kind = "error"
    return {"error_kind": kind, "chars": len(e)}


def build_trace_record(turn_id, session_id, model, steps, outcome, error, elapsed_ms):
    """构造一条 trace 记录（形状与 agent_trace.Turn.to_record 对齐，只含元数据）。"""
    return {
        "turn_id": turn_id,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "session_id": session_id or "flow",
        "provider": "flow",
        "model": model or "流程",
        "route": "flow",
        "question_chars": 0,
        "messages_hash": None,
        "messages_count": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_cny": 0.0,
        "llm_calls": 0,
        "llm_ms": 0,
        "steps": steps,
        "actions": [s["action"] for s in steps],
        "n_steps": len(steps),
        "reflections": 0,
        "outcome": outcome,
        "finish_reason": outcome,
        "final_chars": 0,
        "aborted": False,
        # 隐私：error 只落「分类 + 长度」元数据，绝不写原文 / 路径 / 代码（方案 §9.5）
        "error": _classify_error(error),
        "elapsed_ms": int(elapsed_ms),
    }


def _purge_runs_locked(now):
    for k in [k for k, v in _RUNS.items() if now - v["t"] > _RUN_TTL_S]:
        _RUNS.pop(k, None)
    while len(_RUNS) > _RUN_MAX:
        oldest = min(_RUNS.items(), key=lambda kv: kv[1]["t"])[0]
        _RUNS.pop(oldest, None)


def trace_step(*, run_id, session_id, model, action, ok, latency_ms, arg_chars, obs_chars,
               finish, error=""):
    """把一步写进 agent_trace。

    - run_id 为空：作为「独立回合」（单步重跑/临时执行）即时落盘一条 trace。
    - run_id 非空：累积为一个回合的多步；当 ``finish`` 为真或本步失败时落盘。
    返回是否落盘。任何异常都静默降级（埋点不影响主流程）。
    """
    import agent_trace
    now = time.time()
    meta = _step_meta(action, ok, latency_ms, arg_chars, obs_chars)
    try:
        if not run_id:
            meta["i"] = 0
            rec = build_trace_record("flow-" + uuid.uuid4().hex[:10], session_id, model,
                                     [meta], "completed" if ok else "error", error, latency_ms)
            return bool(agent_trace.record(rec))
        rec = None
        with _RUNS_LOCK:
            _purge_runs_locked(now)
            run = _RUNS.get(run_id)
            if run is None:
                run = {"t": now, "t0": now,
                       "session_id": session_id or ("flow:" + run_id),
                       "model": model or "流程",
                       "turn_id": "flow-" + run_id,
                       "steps": []}
                _RUNS[run_id] = run
            run["t"] = now
            meta["i"] = len(run["steps"])
            run["steps"].append(meta)
            if finish or not ok:
                rec = build_trace_record(run["turn_id"], run["session_id"], run["model"],
                                         list(run["steps"]),
                                         "completed" if ok else "error", error,
                                         (now - run["t0"]) * 1000)
                _RUNS.pop(run_id, None)
        if rec is not None:
            return bool(agent_trace.record(rec))
        return False
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------------------
# HTTP 层（APIRouter 只做映射；纯逻辑都在上面）
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/flows", tags=["flows"])


def _runtime_root():
    root = (get_runtime("code_root") or CODE_ROOT or "").strip()
    return os.path.abspath(root) if root and os.path.isdir(root) else ""


class FlowSaveReq(BaseModel):
    id: str = ""
    name: str = ""
    desc: str = ""
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)


class FlowDeleteReq(BaseModel):
    id: str


class RunStepReq(BaseModel):
    action: str
    params: dict = Field(default_factory=dict)
    run_id: str = ""
    flow_id: str = ""
    flow_name: str = ""
    node_id: str = ""
    finish: bool = False


@router.get("")
def flows_list_ep():
    """列出当前项目的流程模板。"""
    root = _runtime_root()
    if not root:
        return {"ok": True, "flows": []}
    return {"ok": True, "flows": load_flows(root)}


@router.post("")
def flows_save_ep(req: FlowSaveReq):
    """保存（新建或更新）一个流程模板。"""
    root = _runtime_root()
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录或目录不存在。"}, status_code=400)
    res = save_flow(root, req.model_dump())
    if not res.get("ok"):
        return JSONResponse({"ok": False, "error": res.get("error") or "流程校验失败。",
                             "errors": res.get("errors") or []}, status_code=400)
    return {"ok": True, "flow": res["flow"]}


@router.post("/delete")
def flows_delete_ep(req: FlowDeleteReq):
    """删除一个流程模板。"""
    root = _runtime_root()
    if not root:
        return JSONResponse({"ok": False, "error": "未配置代码库根目录或目录不存在。"}, status_code=400)
    res = delete_flow(root, req.id)
    if not res.get("ok"):
        return JSONResponse(res, status_code=404)
    return res


@router.post("/run-step")
def flows_run_step_ep(req: RunStepReq):
    """服务端执行单个受控步骤，并把结果写进 agent_trace（只记元数据）。"""
    root = _runtime_root()
    if not root:
        return JSONResponse({"ok": False, "status": "fail", "action": req.action,
                             "error": "未配置代码库根目录或目录不存在。"}, status_code=400)
    res = execute_step(root, req.action, req.params)
    try:
        arg_chars = len(json.dumps(req.params or {}, ensure_ascii=False))
        obs_chars = len(res.get("output") or "")
        session_id = ("flow:" + req.flow_id) if req.flow_id else (("flow:" + req.run_id) if req.run_id else "flow")
        res["trace_written"] = trace_step(
            run_id=req.run_id, session_id=session_id, model=req.flow_name,
            action=res.get("action") or req.action, ok=bool(res.get("ok")),
            latency_ms=res.get("latency_ms") or 0, arg_chars=arg_chars, obs_chars=obs_chars,
            finish=req.finish, error=res.get("error") or "")
    except Exception:  # noqa: BLE001  埋点失败不影响步骤结果
        res["trace_written"] = False
    return res
