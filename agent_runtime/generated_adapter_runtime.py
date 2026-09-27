"""生成式领域适配器的受控暂存、隔离执行和回滚。

生成的模块只负责把已获得的工具结果转换成预览 artifact。它不能自行
启动命令、联网、读取项目外文件或修改宿主进程；真正的 EasyEDA/Godot/CAD
状态获取仍由已批准的 MCP/内置工具完成。
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import urlsplit
from typing import Any, Callable, Mapping


MAX_SOURCE_BYTES = 180_000
MAX_INPUT_BYTES = 2_000_000
MAX_OUTPUT_BYTES = 2_000_000
MAX_EXECUTION_SECONDS = 30
_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_RUNTIMES = {"python", "node"}
_PY_IMPORTS = {"json", "math", "re", "statistics", "dataclasses", "typing"}
_PY_BLOCKED_NAMES = {
    "__import__", "eval", "exec", "compile", "open", "input", "breakpoint",
    "system", "popen", "spawn", "run", "Popen", "connect", "urlopen",
    "__builtins__", "getattr", "setattr", "globals", "locals", "vars", "dir",
}
_NODE_BLOCKED = re.compile(
    r"(?i)(?:require\s*\(|import\s+|process\b|child_process|(?:^|[^a-z])fs\b|"
    r"net\b|https?\b|fetch\s*\(|eval\s*\(|new\s+Function\b|exec\s*\()")
_SECRET_KEY = re.compile(r"(?i)(?:api[_-]?key|token|password|passwd|secret|private[_-]?key)")


class GeneratedAdapterError(ValueError):
    """用户可见的生成适配器验证/执行错误。"""


def validate_output(value: Any, project_root: str | os.PathLike[str]) -> Any:
    """Validate the bounded JSON/artifact contract returned by an adapter."""
    root = _root(project_root)
    count = 0

    def walk(item: Any, depth: int = 0) -> Any:
        nonlocal count
        count += 1
        if count > 512 or depth > 8:
            raise GeneratedAdapterError("适配器输出层级或元素数量超过限制")
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                key = str(key)
                if _SECRET_KEY.search(key):
                    raise GeneratedAdapterError("适配器输出包含凭据字段")
                if key.lower() in {"path", "file", "filepath"} and isinstance(child, str):
                    candidate = child
                    if candidate.lower().startswith("file://"):
                        candidate = candidate[7:]
                    if candidate and not candidate.startswith(("http://", "https://", "data:")):
                        target = Path(candidate).expanduser()
                        if not target.is_absolute():
                            target = root / target
                        try:
                            target.resolve().relative_to(root)
                        except ValueError as exc:
                            raise GeneratedAdapterError("适配器输出路径越过当前项目目录") from exc
                if key.lower() in {"url", "uri"} and isinstance(child, str):
                    parsed = urlsplit(child)
                    if parsed.username or parsed.password or _SECRET_KEY.search(parsed.query):
                        raise GeneratedAdapterError("适配器输出 URL 包含凭据")
                result[key] = walk(child, depth + 1)
            return result
        if isinstance(item, list):
            return [walk(child, depth + 1) for child in item[:512]]
        if isinstance(item, (str, int, float, bool)) or item is None:
            return item
        raise GeneratedAdapterError("适配器输出包含不支持的数据类型")

    if not isinstance(value, (dict, list)):
        raise GeneratedAdapterError("适配器必须返回 JSON 对象或数组")
    return walk(value)


def _clean_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9_.-]+", "-", str(value or "").strip().lower())[:64].strip(".-")


def _root(project_root: str | os.PathLike[str]) -> Path:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise GeneratedAdapterError("项目目录不存在")
    return root


def _paths(project_root: str | os.PathLike[str], adapter_id: str) -> dict[str, Path]:
    root = _root(project_root)
    clean = _clean_id(adapter_id)
    if not _ID.fullmatch(clean):
        raise GeneratedAdapterError("适配器 id 无效")
    base = root / ".docmind" / "preview-adapters"
    return {
        "base": base,
        "pending": base / ".pending" / clean,
        "active": base / clean,
        "history": base / ".history" / clean,
        "manifest": base / (clean + ".json"),
    }


def _source_path(folder: Path, runtime: str) -> Path:
    return folder / ("adapter.py" if runtime == "python" else "adapter.js")


def validate_source(runtime: str, source: str, *, entrypoint: str = "adapt") -> dict[str, Any]:
    """Validate the small source contract before it is written to disk."""
    runtime = str(runtime or "").strip().lower()
    source = str(source or "")
    if runtime not in _RUNTIMES:
        raise GeneratedAdapterError("runtime 仅支持 python 或 node")
    if not source.strip():
        raise GeneratedAdapterError("适配器 source 不能为空")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise GeneratedAdapterError("适配器 source 超过 180KB 限制")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,48}", str(entrypoint or "")):
        raise GeneratedAdapterError("entrypoint 只能是安全的函数名")
    if "\x00" in source:
        raise GeneratedAdapterError("适配器 source 含有非法字符")
    if runtime == "python":
        try:
            tree = ast.parse(source, mode="exec")
        except SyntaxError as exc:
            raise GeneratedAdapterError("Python 适配器语法错误：%s" % exc.msg) from exc
        functions = {node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if entrypoint not in functions:
            raise GeneratedAdapterError("Python 适配器必须定义 %s(payload)" % entrypoint)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name.split(".", 1)[0] for item in node.names]
                if any(name not in _PY_IMPORTS for name in names):
                    raise GeneratedAdapterError("Python 适配器只能导入受控标准库：json/math/re/statistics/dataclasses/typing")
            elif isinstance(node, ast.ImportFrom):
                name = (node.module or "").split(".", 1)[0]
                if name not in _PY_IMPORTS:
                    raise GeneratedAdapterError("Python 适配器导入了不受控模块：%s" % (node.module or ""))
            elif isinstance(node, ast.Name) and (node.id in _PY_BLOCKED_NAMES or node.id.startswith("__")):
                raise GeneratedAdapterError("Python 适配器禁止调用 %s" % node.id)
            elif isinstance(node, ast.Attribute) and (node.attr in _PY_BLOCKED_NAMES or node.attr.startswith("__")):
                raise GeneratedAdapterError("Python 适配器禁止调用 %s" % node.attr)
    else:
        if not re.search(r"(?:module\.exports\s*=|exports\.%s\s*=)" % re.escape(entrypoint), source):
            raise GeneratedAdapterError("Node 适配器必须导出 module.exports.%s" % entrypoint)
        if _NODE_BLOCKED.search(source):
            raise GeneratedAdapterError("Node 适配器禁止 require/import、文件系统、网络和进程调用")
    return {"runtime": runtime, "entrypoint": entrypoint, "source_bytes": len(source.encode("utf-8")),
            "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest()}


def stage_source(project_root: str | os.PathLike[str], adapter_id: str, runtime: str,
                 source: str, *, entrypoint: str = "adapt") -> dict[str, Any]:
    info = validate_source(runtime, source, entrypoint=entrypoint)
    paths = _paths(project_root, adapter_id)
    folder = paths["pending"]
    if folder.exists():
        raise GeneratedAdapterError("适配器代码草稿已存在")
    folder.mkdir(parents=True, exist_ok=False)
    path = _source_path(folder, info["runtime"])
    path.write_text(source, encoding="utf-8", newline="\n")
    return {**info, "pending_module": str(path.relative_to(_root(project_root))).replace("\\", "/")}


def activate_source(project_root: str | os.PathLike[str], adapter_id: str, runtime: str,
                    *, entrypoint: str = "adapt") -> dict[str, Any]:
    paths = _paths(project_root, adapter_id)
    pending = _source_path(paths["pending"], runtime)
    if not pending.is_file():
        raise GeneratedAdapterError("没有找到待激活的适配器代码")
    source = pending.read_text(encoding="utf-8")
    info = validate_source(runtime, source, entrypoint=entrypoint)
    active = _source_path(paths["active"], runtime)
    paths["active"].mkdir(parents=True, exist_ok=True)
    backup = ""
    if active.is_file():
        paths["history"].mkdir(parents=True, exist_ok=True)
        backup_dir = paths["history"] / (str(int(time.time())) + "-" + uuid.uuid4().hex[:8])
        backup_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(active, backup_dir / active.name)
        backup = str((backup_dir / active.name).relative_to(_root(project_root))).replace("\\", "/")
    os.replace(pending, active)
    try:
        paths["pending"].rmdir()
    except OSError:
        pass
    return {**info, "module_path": str(active.relative_to(_root(project_root))).replace("\\", "/"),
            "backup_path": backup}


def rollback(project_root: str | os.PathLike[str], adapter_id: str, runtime: str) -> dict[str, Any]:
    paths = _paths(project_root, adapter_id)
    active = _source_path(paths["active"], runtime)
    backups = sorted(paths["history"].glob("*/" + active.name)) if paths["history"].is_dir() else []
    if backups:
        latest = backups[-1]
        active.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(latest, active)
        return {"ok": True, "restored": str(latest.relative_to(_root(project_root))).replace("\\", "/")}
    if active.exists():
        active.unlink()
    try:
        paths["active"].rmdir()
    except OSError:
        pass
    return {"ok": True, "restored": "", "removed": True}


_PY_RUNNER = r'''import importlib.util, json, pathlib, sys
module_path, entrypoint, input_path = sys.argv[1:]
spec = importlib.util.spec_from_file_location("docmind_generated_adapter", module_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
value = getattr(module, entrypoint)(json.loads(pathlib.Path(input_path).read_text(encoding="utf-8")))
print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
'''
_NODE_RUNNER = r'''const fs = require('fs');
const [modulePath, entrypoint, inputPath] = process.argv.slice(1);
const mod = require(modulePath);
const value = mod[entrypoint](JSON.parse(fs.readFileSync(inputPath, 'utf8')));
process.stdout.write(JSON.stringify(value));
'''


def execute(project_root: str | os.PathLike[str], manifest: Mapping[str, Any], payload: Any,
            *, timeout: int = 12, runner: Callable[..., Any] | None = None) -> dict[str, Any]:
    runtime = str(manifest.get("runtime") or "").strip().lower()
    entrypoint = str(manifest.get("entrypoint") or "adapt")
    adapter_id = _clean_id(manifest.get("id"))
    paths = _paths(project_root, adapter_id)
    module = _source_path(paths["active"], runtime)
    if runtime not in _RUNTIMES or not module.is_file():
        raise GeneratedAdapterError("适配器代码未激活")
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_INPUT_BYTES:
        raise GeneratedAdapterError("适配器输入超过 2MB 限制")
    run_root = paths["base"] / ".runs"
    run_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=adapter_id + "-", dir=run_root) as temp:
        temp_path = Path(temp)
        input_path = temp_path / "input.json"
        input_path.write_text(raw, encoding="utf-8")
        if runtime == "python":
            command = [sys.executable, "-I", "-c", _PY_RUNNER, str(module), entrypoint, str(input_path)]
        else:
            node = shutil.which("node")
            if not node:
                raise GeneratedAdapterError("未找到 Node.js，无法运行 Node 适配器")
            command = [node, "--no-warnings", "-e", _NODE_RUNNER, str(module), entrypoint, str(input_path)]
        # Keep Windows process bootstrap variables (SystemRoot/TEMP/etc.) but
        # remove inherited credentials and import hooks before entering the
        # Job Object. Network proxy variables are also cleared by default.
        environment = dict(os.environ)
        for key in list(environment):
            upper = key.upper()
            if (any(token in upper for token in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "PRIVATE_KEY"))
                    or upper in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "PYTHONPATH", "NODE_PATH"}):
                environment.pop(key, None)
        environment.update({"PYTHONIOENCODING": "utf-8", "NODE_NO_WARNINGS": "1"})
        try:
            if runner is not None:
                completed = runner(command, cwd=str(temp_path), capture_output=True,
                                   text=True, timeout=max(1, min(MAX_EXECUTION_SECONDS, int(timeout))),
                                   env=environment)
            else:
                from .windows_sandbox import run_isolated
                completed = run_isolated(command, cwd=str(temp_path), env=environment,
                                         timeout=max(1, min(MAX_EXECUTION_SECONDS, int(timeout))))
        except subprocess.TimeoutExpired as exc:
            raise GeneratedAdapterError("适配器执行超时") from exc
        except (OSError, RuntimeError) as exc:
            raise GeneratedAdapterError("适配器隔离启动失败：%s" % str(exc)[:200]) from exc
        code = int(getattr(completed, "returncode", 0) if not isinstance(completed, Mapping) else completed.get("returncode", 0))
        stdout = str(getattr(completed, "stdout", "") if not isinstance(completed, Mapping) else completed.get("stdout", ""))
        stderr = str(getattr(completed, "stderr", "") if not isinstance(completed, Mapping) else completed.get("stderr", ""))
        if code != 0:
            raise GeneratedAdapterError("适配器执行失败：%s" % stderr[-300:])
        if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise GeneratedAdapterError("适配器输出超过 2MB 限制")
        try:
            value = json.loads(stdout)
        except (TypeError, ValueError) as exc:
            raise GeneratedAdapterError("适配器没有返回合法 JSON") from exc
        value = validate_output(value, project_root)
        return {"ok": True, "value": value, "runtime": runtime,
                "module": str(module.relative_to(_root(project_root))).replace("\\", "/")}


__all__ = ["GeneratedAdapterError", "activate_source", "execute", "rollback", "stage_source", "validate_output", "validate_source"]
