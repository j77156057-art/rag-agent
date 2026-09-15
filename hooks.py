"""工具/回合钩子注册表（支持热插拔）。

钩子目录：`<BASE_DIR>/.docmind/hooks/*.py`（可用 `DOCMIND_HOOKS_DIR` 覆盖）。
每个模块可用两种写法之一注册：

  # 写法 A：显式注册
  def register(api):
      api.pre_tool(my_pre)
      api.post_tool(my_post)

  # 写法 B：模块级同名函数（自动收集）
  def pre_tool(name, arg): ...
  def post_tool(name, arg, obs): ...
  def pre_turn(question): ...
  def post_turn(record): ...

钩子契约（返回值可为 None）：
  pre_tool(name, arg) -> {"block": bool, "reason": str} | {"arg": new_arg}
  post_tool(name, arg, obs) -> {"obs": new_obs}
  pre_turn(question) -> {"question": new_question} | {"block": bool, "reason": str}
  post_turn(record) -> 忽略返回值（用于埋点/通知）

**任何钩子异常都被吞掉并计入 errors，绝不影响主流程。** 目录不存在时全程静默 no-op。
"""
from __future__ import annotations

import importlib.util
import os
import threading

from config import BASE_DIR

HOOKS_DIR = os.getenv("DOCMIND_HOOKS_DIR") or os.path.join(BASE_DIR, ".docmind", "hooks")

_lock = threading.Lock()
_hooks = {"pre_tool": [], "post_tool": [], "pre_turn": [], "post_turn": []}
_sources = {}     # kind -> [module_name]
_errors = []      # 加载/执行错误（供 /api/hooks 查看）
_loaded = False
_seq = 0


class _Api:
    """传给 register(api) 的注册门面。"""

    def __init__(self, module_name):
        self.module_name = module_name

    def _add(self, kind, fn):
        _hooks[kind].append(fn)
        _sources.setdefault(kind, []).append(self.module_name)

    def pre_tool(self, fn):
        self._add("pre_tool", fn)

    def post_tool(self, fn):
        self._add("post_tool", fn)

    def pre_turn(self, fn):
        self._add("pre_turn", fn)

    def post_turn(self, fn):
        self._add("post_turn", fn)


def _reset():
    global _seq
    for k in _hooks:
        _hooks[k] = []
    _sources.clear()
    _errors.clear()
    _seq += 1


def reload(force=True):
    """重新扫描钩子目录（热插拔）。返回 {loaded, errors, kinds}。"""
    global _loaded
    with _lock:
        _reset()
        if not os.path.isdir(HOOKS_DIR):
            _loaded = True
            return {"loaded": 0, "errors": [], "hooks_dir": HOOKS_DIR}
        count = 0
        for fname in sorted(os.listdir(HOOKS_DIR)):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            path = os.path.join(HOOKS_DIR, fname)
            mod_name = f"docmind_hook_{_seq}_{os.path.splitext(fname)[0]}"
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)          # type: ignore[union-attr]
                api = _Api(fname)
                reg = getattr(mod, "register", None)
                if callable(reg):
                    reg(api)
                for kind in _hooks:
                    fn = getattr(mod, kind, None)
                    if callable(fn):
                        api._add(kind, fn)
                count += 1
            except Exception as e:  # noqa: BLE001 —— 坏钩子只记录，不影响其它
                _errors.append({"module": fname, "error": f"{type(e).__name__}: {e}"})
        _loaded = True
        return {"loaded": count, "errors": list(_errors), "hooks_dir": HOOKS_DIR}


def _ensure():
    if not _loaded:
        reload()


def run_pre_tool(name, arg):
    """返回 (blocked, reason, arg)。"""
    _ensure()
    blocked, reason = False, ""
    for fn in list(_hooks["pre_tool"]):
        try:
            out = fn(name, arg)
        except Exception as e:  # noqa: BLE001
            _errors.append({"hook": "pre_tool", "error": f"{type(e).__name__}: {e}"})
            continue
        if isinstance(out, dict):
            if out.get("block"):
                blocked = True
                reason = str(out.get("reason") or "被钩子拦截")
            if out.get("arg") is not None:
                arg = out["arg"]
    return blocked, reason, arg


def run_post_tool(name, arg, obs):
    """返回可能被钩子改写的 obs。"""
    _ensure()
    for fn in list(_hooks["post_tool"]):
        try:
            out = fn(name, arg, obs)
        except Exception as e:  # noqa: BLE001
            _errors.append({"hook": "post_tool", "error": f"{type(e).__name__}: {e}"})
            continue
        if isinstance(out, dict) and out.get("obs") is not None:
            obs = out["obs"]
    return obs


def run_pre_turn(question):
    """返回 (blocked, reason, question)。"""
    _ensure()
    blocked, reason = False, ""
    for fn in list(_hooks["pre_turn"]):
        try:
            out = fn(question)
        except Exception as e:  # noqa: BLE001
            _errors.append({"hook": "pre_turn", "error": f"{type(e).__name__}: {e}"})
            continue
        if isinstance(out, dict):
            if out.get("block"):
                blocked = True
                reason = str(out.get("reason") or "被钩子拦截")
            if out.get("question") is not None:
                question = out["question"]
    return blocked, reason, question


def run_post_turn(record):
    _ensure()
    for fn in list(_hooks["post_turn"]):
        try:
            fn(record)
        except Exception as e:  # noqa: BLE001
            _errors.append({"hook": "post_turn", "error": f"{type(e).__name__}: {e}"})


def list_hooks():
    _ensure()
    return {
        "hooks_dir": HOOKS_DIR,
        "exists": os.path.isdir(HOOKS_DIR),
        "counts": {k: len(v) for k, v in _hooks.items()},
        "sources": {k: list(v) for k, v in _sources.items()},
        "errors": list(_errors),
    }


def has_any():
    _ensure()
    return any(len(v) for v in _hooks.values())
