"""开发工作台文件系统接口（P0 任务 2）。

设计原则（对齐既有分区治理哲学）：
- 所有路径都是「相对 code_root 的正斜杠相对路径」；统一在沙箱内 realpath 解析，
  拒绝绝对路径/盘符/``..`` 越界/符号链接逃逸。
- 复用 regions 模块的分区配置与 ``git`` 调用；分区契约文件（regions.json /
  DOCMIND_RULES.md / DEV_INDEX.md / dev_changesets.jsonl）与任何 ``.git`` 内路径禁写禁删，
  改分区必须走既有审批流。
- 写操作护栏：扩展名白名单、512KB 单文件上限、.py 用 compile() 进程内语法校验
  （frozen 环境无 python.exe，不能走 py_compile 子进程，与 regions._builtin_py_verify 一致）、
  保存用 mtime 乐观锁防覆盖、未跟踪文件删除需显式 force。
- 纯逻辑函数全部显式接收 ``root`` 参数（不依赖运行时），便于在临时目录里做单元测试；
  APIRouter 只负责从运行时取 code_root、把 FsError 转成 HTTP 响应。
"""
from __future__ import annotations

import os
import shutil
import time
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from config import get_runtime, CODE_ROOT
from ingest import _CODE_EXT, _SKIP_DIRS
from regions import load_region_config, _git

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
WB_MAX_FILE_BYTES = 512 * 1024  # 单文件读写上限
TREE_MAX_NODES = 2000           # 目录树节点硬上限，防止巨型工程拖垮前端
_STATUS_TTL = 5.0               # git 状态快照缓存秒数

# 根目录下受保护的契约/配置文件（改分区结构必须走既有审批流）
PROTECTED_ROOT_FILES = {
    "regions.json",
    "DOCMIND_RULES.md",
    "DEV_INDEX.md",
    "dev_changesets.jsonl",
}
# 任意层级受保护目录
PROTECTED_PARTS = {".git", ".docmind_backups", ".chroma"}

# 可在工作台编辑的扩展名 -> 语言标识（CodeMirror 按此选语言包/降级）
WB_LANG = {
    ".py": "python", ".js": "javascript", ".jsx": "jsx", ".ts": "typescript",
    ".tsx": "tsx", ".json": "json", ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
    ".md": "markdown", ".txt": "text", ".css": "css", ".html": "html",
    ".cs": "csharp", ".gd": "gdscript", ".gdshader": "glsl", ".lua": "lua",
    ".cfg": "ini", ".ini": "ini", ".csv": "csv",
    ".tscn": "ini", ".tres": "ini", ".res": "ini",
    ".sh": "shell", ".java": "java", ".go": "go", ".rs": "rust", ".c": "c",
    ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".rb": "ruby", ".php": "php",
    ".kt": "kotlin", ".swift": "swift", ".scala": "scala",
}
WB_EDIT_EXTS = set(WB_LANG) | set(_CODE_EXT)


class FsError(Exception):
    """业务错误：携带 HTTP 状态码与可选 extra（前端按 extra 做分支 UI）。"""

    def __init__(self, status: int, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


# ---------------------------------------------------------------------------
# 沙箱与路径
# ---------------------------------------------------------------------------
def _norm_rel(rel) -> str:
    """把输入路径归一为正斜杠相对路径；非法/越界输入直接拒绝。"""
    if rel is None or str(rel).strip() == "":
        raise FsError(400, "缺少 path 参数。")
    s = str(rel).replace("\\", "/").strip()
    if "\x00" in s:
        raise FsError(400, "路径包含非法字符。")
    if s.startswith("/") or "://" in s or (len(s) >= 2 and s[1] == ":"):
        raise FsError(403, "只允许使用代码库内的相对路径，拒绝绝对路径/盘符/URL。")
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if not parts:
        raise FsError(400, "path 不能为空。")
    if any(p == ".." for p in parts):
        raise FsError(403, "路径越界（包含 ..），已拒绝。")
    return "/".join(parts)


def _require_root(root) -> str:
    if not root or not os.path.isdir(str(root)):
        raise FsError(
            400,
            "未配置代码库根目录或目录不存在，请先在问答页索引代码目录。",
        )
    return os.path.abspath(str(root))


def _resolve(root, rel, *, must_exist=False, for_write=False):
    """归一 + realpath 解析；返回 (绝对路径, 归一相对路径)。"""
    root_abs = _require_root(root)
    rel_n = _norm_rel(rel)
    parts = rel_n.split("/")
    target = os.path.realpath(os.path.join(root_abs, *parts))
    # realpath 后再做一次包含判断，挡住符号链接/junction 逃逸
    if not (target == root_abs or target.startswith(root_abs + os.sep)):
        raise FsError(403, "路径越出代码库根目录，已拒绝。")
    if must_exist and not os.path.exists(target):
        raise FsError(404, f"文件或目录不存在：{rel_n}")
    if for_write:
        _ensure_writable(rel_n)
    return target, rel_n


def _ensure_writable(rel_n: str):
    """写操作保护：契约文件 / .git / 备份目录禁写。"""
    parts = rel_n.split("/")
    if parts[0] in PROTECTED_ROOT_FILES and len(parts) == 1:
        raise FsError(403, f"{rel_n} 是分区契约文件，请通过分区管理功能修改，工作台禁止直接写。")
    bad = [p for p in parts if p in PROTECTED_PARTS]
    if bad:
        raise FsError(403, f"受保护目录 {bad[0]}/ 禁止在工作台中修改。")


def _lang_of(name: str) -> str:
    return WB_LANG.get(os.path.splitext(name)[1].lower(), "text")


def _region_dirs(root) -> dict:
    """region 目录名(相对) -> meta。"""
    out = {}
    try:
        for meta in load_region_config(root):
            out[meta["dir"].replace("\\", "/").strip("/")] = meta
    except Exception:  # noqa: BLE001
        pass
    return out


def _region_of(rel_n: str, rdirs: dict):
    top = rel_n.split("/", 1)[0]
    return rdirs.get(top)


def _regions_enabled(root) -> bool:
    return os.path.isfile(os.path.join(root, "regions.json"))


# ---------------------------------------------------------------------------
# git 快照（tree / 删除护栏共用，5 秒缓存）
# ---------------------------------------------------------------------------
_status_cache: dict = {}


def _git_snapshot(root):
    """返回 {repo_abs: {"tracked": set(posix rel), "dirty": set(posix rel)}}。

    每个分区目录是一个独立 git 仓库；根目录本身也可能是仓库（兼容普通项目）。
    tracked 来自 git ls-files；dirty 来自 git status --porcelain -z（-z 保证
    CJK 路径不被 core.quotePath 转义）。
    """
    now = time.time()
    cached = _status_cache.get(root)
    if cached and now - cached[0] < _STATUS_TTL:
        return cached[1]

    repos = []
    if os.path.isdir(os.path.join(root, ".git")):
        repos.append(root)
    for d in _region_dirs(root):
        rd = os.path.join(root, d)
        if os.path.isdir(os.path.join(rd, ".git")):
            repos.append(rd)

    snap: dict = {}
    for repo in repos:
        tracked: set = set()
        dirty: set = set()
        ok, out = _git(["ls-files"], cwd=repo)
        if ok:
            tracked = {ln.replace("\\", "/") for ln in out.splitlines() if ln}
        ok, out = _git(["status", "--porcelain", "-z", "--untracked-files=all"], cwd=repo)
        if ok and out:
            tokens = out.split("\x00")
            i = 0
            while i < len(tokens):
                rec = tokens[i]
                if not rec:
                    i += 1
                    continue
                status, path = rec[:2], rec[3:]
                p = path.replace("\\", "/")
                if status.strip():
                    dirty.add(p)
                # R/C 记录后面跟第二个 NUL 路径（目标路径），跳过对齐
                if status[0] in ("R", "C") and i + 1 < len(tokens):
                    i += 1
                i += 1
        snap[os.path.realpath(repo)] = {"tracked": tracked, "dirty": dirty}
    _status_cache[root] = (now, snap)
    return snap


def _find_repo(target: str, snap: dict):
    """找包含 target 的（最深）git 仓库；返回 (repo_abs, state) 或 (None, None)。"""
    cur = os.path.realpath(target)
    root_repo = None
    while True:
        if cur in snap:
            return cur, snap[cur]
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None, None


def _git_fields(target: str, rel_n: str, snap: dict):
    """计算节点的 tracked / dirty 字段（不在任何仓库则均为 None）。"""
    repo, state = _find_repo(os.path.dirname(target) if os.path.isfile(target) else target, snap)
    if not repo:
        return None, None
    in_repo = os.path.relpath(target, repo).replace("\\", "/")
    return (in_repo in state["tracked"], in_repo in state["dirty"])


def invalidate_status(root=None):
    """写操作后清缓存（测试与保存/删除/改名后调用）。"""
    if root is None:
        _status_cache.clear()
    else:
        _status_cache.pop(os.path.abspath(str(root)), None)


# ---------------------------------------------------------------------------
# 1) tree
# ---------------------------------------------------------------------------
def build_tree(root, depth: int = 4) -> dict:
    root_abs = _require_root(root)
    rdirs = _region_dirs(root_abs)
    enabled = _regions_enabled(root_abs)
    snap = _git_snapshot(root_abs) if (enabled or os.path.isdir(os.path.join(root_abs, ".git"))) else {}

    counter = {"n": 0, "truncated": False}

    def make_node(dir_abs: str, name: str, rel_n: str, level: int):
        counter["n"] += 1
        if counter["n"] > TREE_MAX_NODES:
            counter["truncated"] = True
            return None
        is_dir = os.path.isdir(dir_abs)
        rmeta = _region_of(rel_n, rdirs) if rel_n else None
        node = {
            "path": rel_n,
            "name": name,
            "type": "dir" if is_dir else "file",
            "region": rmeta["key"] if rmeta else None,
            "region_name": rmeta["name"] if rmeta else None,
            "lang": None if is_dir else _lang_of(name),
            "writable": is_dir or os.path.splitext(name)[1].lower() in WB_EDIT_EXTS,
            "children": [],
        }
        if not is_dir:
            node["size"] = os.path.getsize(dir_abs)
            try:
                tracked, dirty = _git_fields(dir_abs, rel_n, snap)
            except Exception:  # noqa: BLE001
                tracked, dirty = None, None
            node["tracked"] = tracked
            node["dirty"] = dirty
        return node

    def walk(dir_abs: str, rel_prefix: str, level: int):
        if depth >= 0 and level >= depth:
            return []
        try:
            names = sorted(os.listdir(dir_abs), key=lambda n: (not os.path.isdir(os.path.join(dir_abs, n)), n.lower()))
        except OSError:
            return []
        nodes = []
        for name in names:
            if name in _SKIP_DIRS and os.path.isdir(os.path.join(dir_abs, name)):
                continue
            rel_n = f"{rel_prefix}/{name}" if rel_prefix else name
            child_abs = os.path.join(dir_abs, name)
            node = make_node(child_abs, name, rel_n, level + 1)
            if node is None:
                return nodes
            if node["type"] == "dir":
                node["children"] = walk(child_abs, rel_n, level + 1)
                if counter["truncated"]:
                    nodes.append(node)
                    return nodes
            nodes.append(node)
        return nodes

    nodes = walk(root_abs, "", 0)
    return {
        "ok": True,
        "code_root": root_abs,
        "regions_enabled": enabled,
        "regions": [
            {"key": m["key"], "name": m["name"], "dir": m["dir"], "desc": m.get("desc", "")}
            for m in rdirs.values()
        ],
        "nodes": nodes,
        "truncated": counter["truncated"],
    }


# ---------------------------------------------------------------------------
# 2) file（读全文）
# ---------------------------------------------------------------------------
def read_full(root, rel) -> dict:
    target, rel_n = _resolve(root, rel, must_exist=True)
    if os.path.isdir(target):
        raise FsError(400, f"{rel_n} 是目录，不是文件。")
    size = os.path.getsize(target)
    if size > WB_MAX_FILE_BYTES:
        raise FsError(413, f"文件过大（{size // 1024}KB），工作台编辑上限 {WB_MAX_FILE_BYTES // 1024}KB。")
    ext = os.path.splitext(target)[1].lower()
    if ext not in WB_EDIT_EXTS:
        raise FsError(415, f"暂不支持在工作台编辑 {ext or '无扩展名'} 文件。")
    with open(target, "rb") as fh:
        raw = fh.read()
    if b"\x00" in raw[:8192]:
        raise FsError(415, "二进制文件不可在工作台编辑。")
    try:
        content = raw.decode("utf-8-sig")  # 容忍带 BOM 的 UTF-8（Windows 编辑器常见）
    except UnicodeDecodeError:
        raise FsError(415, "文件不是 UTF-8 编码，请在外部编辑器转码后再打开。")
    root_abs = os.path.abspath(str(root))
    rdirs = _region_dirs(root_abs)
    rmeta = _region_of(rel_n, rdirs)
    snap = _git_snapshot(root_abs)
    tracked, dirty = (None, None)
    if snap:
        try:
            tracked, dirty = _git_fields(target, rel_n, snap)
        except Exception:  # noqa: BLE001
            tracked, dirty = None, None
    return {
        "ok": True,
        "path": rel_n,
        "content": content,
        "lang": _lang_of(rel_n),
        "size": size,
        "mtime": os.path.getmtime(target),
        "region": rmeta["key"] if rmeta else None,
        "region_name": rmeta["name"] if rmeta else None,
        "writable": True,
        "tracked": tracked,
        "dirty": dirty,
    }


# ---------------------------------------------------------------------------
# .py 语法校验（进程内 compile，frozen 安全）
# ---------------------------------------------------------------------------
def _check_python_syntax(content: str, rel_n: str):
    try:
        compile(content, rel_n, "exec")
    except SyntaxError as e:
        raise FsError(
            422,
            f"Python 语法校验失败，已取消写入（原文件未改动）：{e.msg}（行 {e.lineno}）",
            {"lineno": e.lineno, "msg": e.msg},
        )


def _reindex_one(root_abs: str, target: str, rel_n: str) -> int:
    """保存后增量更新代码问答集合；调用方负责捕获所有异常。"""
    from ingest import ingest_code_file
    from config import CODE_COLLECTION_NAME
    from vectorstore import delete_by_source

    delete_by_source(rel_n, collection=CODE_COLLECTION_NAME)
    return ingest_code_file(target, root_abs, collection=CODE_COLLECTION_NAME)


# ---------------------------------------------------------------------------
# 3) save
# ---------------------------------------------------------------------------
def save_file(root, rel, content, if_mtime=None, reindex: bool = False) -> dict:
    if not isinstance(content, str):
        raise FsError(400, "content 必须是字符串。")
    target, rel_n = _resolve(root, rel, for_write=True)
    if os.path.isdir(target):
        raise FsError(409, f"{rel_n} 已存在且是目录。")
    ext = os.path.splitext(target)[1].lower()
    if ext not in WB_EDIT_EXTS:
        raise FsError(403, f"不允许写入 {ext or '无扩展名'} 文件（不在可编辑白名单内）。")
    data = content.encode("utf-8")
    if len(data) > WB_MAX_FILE_BYTES:
        raise FsError(413, f"写入内容超过上限（{WB_MAX_FILE_BYTES // 1024}KB），已取消。")
    if ext == ".py" and content.strip():
        _check_python_syntax(content, rel_n)

    existed = os.path.isfile(target)
    server_mtime = os.path.getmtime(target) if existed else None
    if existed and if_mtime is not None and server_mtime is not None:
        if abs(float(if_mtime) - server_mtime) > 1.5:
            raise FsError(
                409,
                "文件在编辑器外被修改，已阻止覆盖。请对比后选择覆盖或另存。",
                {"server_mtime": server_mtime},
            )

    changed = True
    if existed:
        with open(target, "rb") as fh:
            changed = fh.read() != data
    parent = os.path.dirname(target)
    os.makedirs(parent, exist_ok=True)
    # newline="" 关闭写入翻译，保留编辑器给出的原始换行
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(content)
    invalidate_status(root)

    root_abs = os.path.abspath(str(root))
    rdirs = _region_dirs(root_abs)
    rmeta = _region_of(rel_n, rdirs)
    warnings: list = []
    code_chunks = None
    if changed and reindex and ext in _CODE_EXT:
        try:
            code_chunks = _reindex_one(root_abs, target, rel_n)
        except Exception as e:  # noqa: BLE001  索引失败不回滚文件保存
            warnings.append(f"代码索引增量更新失败（可点「重新索引」修复）：{type(e).__name__}: {e}")
    return {
        "ok": True,
        "path": rel_n,
        "mtime": os.path.getmtime(target),
        "changed": changed,
        "region": rmeta["key"] if rmeta else None,
        "region_name": rmeta["name"] if rmeta else None,
        "reindexed": {"code_chunks": code_chunks} if code_chunks is not None else None,
        "reindex_warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 4) create
# ---------------------------------------------------------------------------
def create_path(root, rel, kind: str, content: str = "") -> dict:
    kind = (kind or "").strip().lower()
    if kind not in ("file", "folder"):
        raise FsError(400, "type 必须是 file 或 folder。")
    target, rel_n = _resolve(root, rel, for_write=True)
    if os.path.exists(target):
        raise FsError(409, f"{rel_n} 已存在。")
    if kind == "folder":
        os.makedirs(target, exist_ok=False)
        return {"ok": True, "path": rel_n, "node": {"path": rel_n, "name": os.path.basename(target), "type": "dir", "children": []}}
    ext = os.path.splitext(target)[1].lower()
    if ext not in WB_EDIT_EXTS:
        raise FsError(403, f"不允许创建 {ext or '无扩展名'} 文件（不在可编辑白名单内）。")
    if content and not isinstance(content, str):
        raise FsError(400, "content 必须是字符串。")
    data = content or ""
    if ext == ".py" and data.strip():
        _check_python_syntax(data, rel_n)
    if len(data.encode("utf-8")) > WB_MAX_FILE_BYTES:
        raise FsError(413, "文件内容超过上限。")
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        fh.write(data)
    invalidate_status(root)
    return {
        "ok": True,
        "path": rel_n,
        "node": {
            "path": rel_n, "name": os.path.basename(target), "type": "file",
            "lang": _lang_of(rel_n), "writable": True, "size": len(data.encode("utf-8")),
            "tracked": False, "dirty": True,
        },
    }


# ---------------------------------------------------------------------------
# 5) rename / move
# ---------------------------------------------------------------------------
def rename_path(root, rel, new_rel, reindex: bool = False) -> dict:
    target, rel_n = _resolve(root, rel, must_exist=True, for_write=True)
    dst, new_n = _resolve(root, new_rel, for_write=True)
    if os.path.exists(dst):
        raise FsError(409, f"目标已存在：{new_n}")
    # 目标父目录必须存在（避免 rename 顺带隐式建目录，行为更可预期）
    if not os.path.isdir(os.path.dirname(dst)):
        raise FsError(404, f"目标文件夹不存在：{os.path.dirname(new_n)}")

    root_abs = os.path.abspath(str(root))
    rdirs = _region_dirs(root_abs)
    if _regions_enabled(root_abs):
        src_region = _region_of(rel_n, rdirs)
        dst_region = _region_of(new_n, rdirs)
        if (src_region or {}).get("key") != (dst_region or {}).get("key"):
            raise FsError(
                403,
                "禁止跨分区移动文件（会破坏分区依赖契约）。请让 Agent 使用 dev_refactor "
                "走契约校验后搬移，或在同一分区内整理。",
            )

    snap = _git_snapshot(root_abs)
    repo, state = _find_repo(target, snap)
    git_renamed = False
    if repo:
        in_repo_old = os.path.relpath(target, repo).replace("\\", "/")
        in_repo_new = os.path.relpath(dst, repo).replace("\\", "/")
        if in_repo_old in state["tracked"]:
            ok, _ = _git(["mv", in_repo_old, in_repo_new], cwd=repo)
            git_renamed = ok
    if not git_renamed:
        shutil.move(target, dst)
    invalidate_status(root_abs)

    warnings: list = []
    if reindex and os.path.isfile(dst) and os.path.splitext(dst)[1].lower() in _CODE_EXT:
        try:
            from config import CODE_COLLECTION_NAME
            from vectorstore import delete_by_source
            delete_by_source(rel_n, collection=CODE_COLLECTION_NAME)
            _reindex_one(root_abs, dst, new_n)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"代码索引更新失败（可点「重新索引」修复）：{type(e).__name__}: {e}")
    return {"ok": True, "from": rel_n, "to": new_n, "git_renamed": git_renamed, "reindex_warnings": warnings}


# ---------------------------------------------------------------------------
# 6) delete
# ---------------------------------------------------------------------------
def _iter_files(dir_abs: str):
    for dp, dns, fns in os.walk(dir_abs):
        # .git 永不进入删除清单（PROTECTED_PARTS 已拦，双保险）
        dns[:] = [d for d in dns if d != ".git"]
        for fn in fns:
            yield os.path.join(dp, fn)


def delete_path(root, rel, recursive: bool = False, force: bool = False) -> dict:
    target, rel_n = _resolve(root, rel, must_exist=True, for_write=True)

    if os.path.isdir(target):
        try:
            entries = sorted(os.listdir(target))
        except OSError:
            entries = []
        entries = [e for e in entries if e != ".git"]
        if entries and not recursive:
            raise FsError(409, f"文件夹非空（{len(entries)} 项），删除需 recursive=true 二次确认。",
                          {"sample": entries[:10], "count": len(entries)})
        # 目录删除不可逆性检查：内含未跟踪文件时必须 force
        snap = _git_snapshot(os.path.abspath(str(root)))
        untracked = []
        for f in _iter_files(target):
            repo, state = _find_repo(f, snap)
            if not repo:
                untracked.append(os.path.relpath(f, target).replace("\\", "/"))
            else:
                in_repo = os.path.relpath(f, repo).replace("\\", "/")
                if in_repo not in state["tracked"]:
                    untracked.append(os.path.relpath(f, target).replace("\\", "/"))
        if untracked and not force:
            raise FsError(
                409,
                f"文件夹中有 {len(untracked)} 个未纳入 git 的文件，删除不可恢复，需 force=true 二次确认。",
                {"untracked_sample": untracked[:10], "untracked_count": len(untracked)},
            )
        shutil.rmtree(target)
        invalidate_status(root)
        return {"ok": True, "deleted": rel_n, "recoverable": not untracked}

    # 单文件
    snap = _git_snapshot(os.path.abspath(str(root)))
    repo, state = _find_repo(target, snap)
    tracked = bool(repo and os.path.relpath(target, repo).replace("\\", "/") in state["tracked"])
    if not tracked and not force:
        raise FsError(409, "该文件未纳入 git，删除后不可恢复，需 force=true 二次确认。")
    os.remove(target)
    invalidate_status(root)
    if os.path.splitext(target)[1].lower() in _CODE_EXT:
        try:
            from config import CODE_COLLECTION_NAME
            from vectorstore import delete_by_source
            delete_by_source(rel_n, collection=CODE_COLLECTION_NAME)
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "deleted": rel_n, "recoverable": tracked}


# ---------------------------------------------------------------------------
# 7) gitlog
# ---------------------------------------------------------------------------
def git_log(root, rel, limit: int = 20) -> dict:
    target, rel_n = _resolve(root, rel, must_exist=True)
    root_abs = os.path.abspath(str(root))
    probe = target if os.path.isdir(target) else os.path.dirname(target)
    root_real = os.path.realpath(root_abs)
    cur = os.path.realpath(probe)
    repo = None
    while True:
        if os.path.isdir(os.path.join(cur, ".git")):
            repo = cur
            break
        if cur == root_real:
            break
        parent = os.path.dirname(cur)
        if parent == cur or not cur.startswith(root_real + os.sep):
            break
        cur = parent
    if not repo:
        raise FsError(400, "该文件不在任何 git 仓库内（分区尚未初始化 git）。")
    in_repo = os.path.relpath(target, repo).replace("\\", "/")
    limit = max(1, min(int(limit or 20), 100))
    ok, out = _git(
        ["log", f"-{limit}", "--pretty=format:%H%x09%h%x09%ad%x09%an%x09%s",
         "--date=iso-strict", "--", in_repo],
        cwd=repo,
    )
    if not ok:
        raise FsError(400, f"git log 失败：{out[:300]}")
    commits = []
    for line in out.splitlines():
        parts = line.split("\t", 4)
        if len(parts) != 5:
            continue
        full, short, dt, author, subject = parts
        ts = None
        try:
            ts = datetime.fromisoformat(dt).timestamp()
        except Exception:  # noqa: BLE001
            ts = None
        commits.append({"hash": short, "full_hash": full, "time": ts, "time_raw": dt,
                        "author": author, "message": subject})
    return {"ok": True, "path": rel_n, "commits": commits}


# ---------------------------------------------------------------------------
# HTTP 层
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api/fs", tags=["workbench-fs"])


def _runtime_root() -> str:
    root = (get_runtime("code_root") or CODE_ROOT or "").strip()
    return _require_root(root)


def _err(e: FsError) -> JSONResponse:
    body = {"ok": False, "error": e.message}
    if e.extra:
        body.update(e.extra)
    return JSONResponse(body, status_code=e.status)


class SaveReq(BaseModel):
    path: str
    content: str
    if_mtime: float | None = None
    reindex: bool = True


class CreateReq(BaseModel):
    path: str
    type: str = "file"
    content: str = ""


class RenameReq(BaseModel):
    path: str
    new_path: str
    reindex: bool = True


class DeleteReq(BaseModel):
    path: str
    recursive: bool = False
    force: bool = False


@router.get("/tree")
def tree_ep(depth: int = 4):
    try:
        return build_tree(_runtime_root(), depth=depth)
    except FsError as e:
        return _err(e)


@router.get("/file")
def file_ep(path: str):
    try:
        return read_full(_runtime_root(), path)
    except FsError as e:
        return _err(e)


@router.post("/save")
def save_ep(req: SaveReq):
    try:
        return save_file(_runtime_root(), req.path, req.content, req.if_mtime, reindex=req.reindex)
    except FsError as e:
        return _err(e)


@router.post("/create")
def create_ep(req: CreateReq):
    try:
        return create_path(_runtime_root(), req.path, req.type, req.content)
    except FsError as e:
        return _err(e)


@router.post("/rename")
def rename_ep(req: RenameReq):
    try:
        return rename_path(_runtime_root(), req.path, req.new_path, reindex=req.reindex)
    except FsError as e:
        return _err(e)


@router.post("/delete")
def delete_ep(req: DeleteReq):
    try:
        return delete_path(_runtime_root(), req.path, req.recursive, req.force)
    except FsError as e:
        return _err(e)


@router.get("/gitlog")
def gitlog_ep(path: str, limit: int = 20):
    try:
        return git_log(_runtime_root(), path, limit=limit)
    except FsError as e:
        return _err(e)
