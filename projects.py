"""项目数据层（P2）：把「代码库」升格为一等实体「项目」的持久化层。

本模块**只做数据/持久化**，不含任何 HTTP 端点（端点项目化是 P3）。依赖 `config` 与
标准库，**不 import api**（避免循环依赖），导入期无磁盘副作用（所有读写都在函数内）。

概念与存储
----------
- 稳定 `project_id`：由代码库路径推导（`prj-<sha1[:12]>`），同一目录恒得同一 id，
  与注册顺序/时间无关，便于跨重启、跨设备稳定引用。
- 注册表持久化到现有 `config.STATE_FILE`，新增两个顶层键：
    {"projects": {"prj-xxxx": {"root","name","created_at","last_opened"}},
     "current_project_id": "prj-xxxx"}
  旧键 `code_root` 保留不动（向后兼容；迁移见 config._apply_persisted_state）。

特性开关
--------
env `DOCMIND_PROJECTS`（默认 "1"）。为 "0" 时**完全退回今日单项目行为**：
`current_project_id()` 返回固定 legacy 伪 id、`code_collection()` 返回 legacy 集合名、
`sessions.py` 走旧路径（不分桶）——出问题可一键回滚。开关为**动态读取**（非导入期常量），
故测试直接 `patch.dict(os.environ)` 即可生效，无需 `importlib.reload`。
"""
from __future__ import annotations

import hashlib
import os
from datetime import datetime

import config

_ENV_FLAG = "DOCMIND_PROJECTS"
# 项目功能关闭时的固定伪项目 id（供 current_project_id 返回，不代表任何真实项目）
LEGACY_ID = "legacy"
_COLLECTION_PREFIX = "docmind_code__"


def enabled() -> bool:
    """项目功能是否开启（env DOCMIND_PROJECTS != "0"）。动态读取，便于测试与回滚。"""
    return os.getenv(_ENV_FLAG, "1") != "0"


def project_id(root) -> str:
    """由代码库路径推导稳定项目 id：`prj-<sha1(normcase(abspath(root)))[:12]>`。

    用 `os.path.normcase(os.path.abspath(root))` 归一 Windows 盘符大小写与斜杠方向，
    使 `D:/a` 与 `d:\\a\\` 得到同一 id；不同目录则不同 id。
    """
    norm = os.path.normcase(os.path.abspath(root or ""))
    return "prj-" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 注册表读写
def _load_registry() -> dict:
    reg = config.load_state("projects", {})
    return reg if isinstance(reg, dict) else {}


def _save_registry(reg: dict) -> None:
    config.save_state("projects", reg)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_name(root_abs: str) -> str:
    return os.path.basename(root_abs.rstrip("\\/")) or root_abs


def _row(pid: str, rec: dict) -> dict:
    """把内部记录整理成对外行（附 project_id，字段有默认值）。"""
    return {
        "project_id": pid,
        "root": rec.get("root", ""),
        "name": rec.get("name", ""),
        "created_at": rec.get("created_at", ""),
        "last_opened": rec.get("last_opened", ""),
    }


# ---------------------------------------------------------------- 公开 API
def ensure_project(root, name=None) -> str:
    """登记一个项目（幂等）：不存在则新建、存在则复用；两种情况都刷新 last_opened。

    返回该项目的稳定 `project_id`。不修改 current_project_id（由 set_current 负责）。
    """
    root_abs = os.path.abspath(root or "")
    pid = project_id(root_abs)
    reg = _load_registry()
    now = _now()
    rec = reg.get(pid)
    if isinstance(rec, dict):
        rec = dict(rec)
        rec["root"] = rec.get("root") or root_abs
        if name:
            rec["name"] = name
        rec.setdefault("name", _default_name(root_abs))
        rec.setdefault("created_at", now)
        rec["last_opened"] = now
    else:
        rec = {
            "root": root_abs,
            "name": name or _default_name(root_abs),
            "created_at": now,
            "last_opened": now,
        }
    reg[pid] = rec
    _save_registry(reg)
    return pid


def get_project(pid) -> dict | None:
    """按 id 取项目（含 project_id 字段）；不存在返回 None。"""
    key = str(pid or "")
    reg = _load_registry()
    rec = reg.get(key)
    return _row(key, rec) if isinstance(rec, dict) else None


def list_projects() -> list:
    """列出全部项目，按 last_opened 倒序（最新在前）。"""
    reg = _load_registry()
    rows = [_row(pid, rec) for pid, rec in reg.items() if isinstance(rec, dict)]
    rows.sort(key=lambda r: r.get("last_opened") or "", reverse=True)
    return rows


def remove_project(pid) -> bool:
    """注销登记（**不删磁盘上的索引/会话**——破坏性操作留给 P3 的显式 API）。

    若注销的正是当前项目：把 current 落到「剩余项目中 last_opened 最新者」，无剩余则置空，
    并同步 runtime.project_id（清掉指向已删项目的残留值）。返回是否确有删除。
    """
    key = str(pid or "")
    reg = _load_registry()
    if key not in reg:
        return False
    reg.pop(key, None)
    _save_registry(reg)
    if _stored_current() == key:
        remaining = list_projects()
        new_current = remaining[0]["project_id"] if remaining else ""
        config.save_state("current_project_id", new_current)
        if new_current:
            config.set_runtime("project_id", new_current)
        else:
            config._RUNTIME.pop("project_id", None)
    return True


def _stored_current() -> str:
    v = config.load_state("current_project_id", "")
    return v if isinstance(v, str) else ""


def current_project_id() -> str:
    """当前项目 id：优先持久化的 current_project_id（且该项目仍在册）；

    否则取 last_opened 最新者；无任何项目时返回空串。项目功能关闭时返回 LEGACY_ID。
    """
    if not enabled():
        return LEGACY_ID
    cid = _stored_current()
    reg = _load_registry()
    if cid and cid in reg:
        return cid
    rows = list_projects()
    return rows[0]["project_id"] if rows else ""


def set_current(pid) -> bool:
    """把某个**已登记**的项目设为当前项目（写 STATE_FILE 并同步 runtime.project_id）。

    空 id / 未登记项目返回 False。
    """
    key = str(pid or "")
    if not key:
        return False
    reg = _load_registry()
    if key not in reg:
        return False
    config.save_state("current_project_id", key)
    config.set_runtime("project_id", key)
    return True


def code_collection(pid=None) -> str:
    """项目功能开启时返回按项目隔离的代码集合名 `docmind_code__<pid>`；

    功能关闭、或未能确定项目（无项目 / legacy）时返回 legacy 名 `config.CODE_COLLECTION_NAME`。
    """
    if not enabled():
        return config.CODE_COLLECTION_NAME
    key = pid or current_project_id()
    if not key or key == LEGACY_ID:
        return config.CODE_COLLECTION_NAME
    return _COLLECTION_PREFIX + str(key)
