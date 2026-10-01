"""HEAD 自洽检查：把【提交本身】挂成干净 worktree，在那个目录里真 import 入口模块。

存在的理由是一起真实事故（2026-10-01，本仓 `b828d10`）：pathspec 形式的 `git commit -- <路径>`
绕过索引，把别人未提交的 WIP 一起提交掉，于是 `agent.py` 里引用
`agent_runtime/run_budget.py` 的那行 import 进了 HEAD，而**那个文件本身还没入库**。
工作树里一切正常（模块还在磁盘上），单独检出 HEAD 却 `ModuleNotFoundError`——
CI、别人的 `git worktree add`、任何 clone 都会红，而在这个共享工作树里跑测试永远看不出来。

所以这条回路必须回到【只有已提交内容】的那棵树上跑。三条设计约束都是被这次事故决定的：

* **先把 ref 解析成 sha 再用**：HEAD 在共享工作树里会被别人推着走，`worktree add` 与
  报告必须指向同一个提交，否则"我验过的"和"我说的"不是同一个东西。
* **只允许 `--detach`**：绝不占用分支，绝不碰主工作树的 checkout 或索引。
* **未跟踪文件要能被归因**：缺模块时同时回答"它在磁盘上吗、它在提交里吗"——
  只在磁盘上 = 别人的新文件被我的提交引用了，这正是上面那类错的指纹。

边界：这条验的是【HEAD 能不能被单独跑起来】，不验行为对不对（那是 run_command 跑测试、
dev_media、dev_page_action 的事）；入口模块之外的深层 import 要靠 import 链真的走到才会暴露。
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

from agent_runtime import process_runner

#: 入口模块：从这些进就能把仓里主要的 import 图走一遍（config→tools→agent 是真实依赖顺序）。
ENTRY_MODULES = ("config", "tools", "agent")
#: 本模块只允许的 git 子命令：全部是读或 worktree 生命周期，没有 history/暂存/推送。
GIT_ALLOWED = frozenset({"rev-parse", "worktree", "ls-files", "ls-tree", "status"})
MAX_REF_LENGTH = 80
#: ref 允许的形状：不能以 `-` 开头（否则被 git 当选项），不含空格/glob/`..`/分号。
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.^~/-]*$")
_MISSING_RE = re.compile(r"No module named '([^']+)'")
_FRAME_RE = re.compile(r'File "(?P<path>[^"]+)", line (?P<line>\d+), in (?P<func>[^\n]+)\n'
                       r'(?P<body>(?: {4}\S[^\n]*\n?)*)')
_IMPORT_STMT_RE = re.compile(r"(?:from|import)\s+([A-Za-z_][\w.]*)")
_MARKER = ".docmind-headcheck-owner"


class HeadCheckError(RuntimeError):
    """HEAD 自洽检查无法开始。"""


def resolve_ref(root: str, ref: str = "HEAD") -> dict[str, Any]:
    """把 ref 钉成 sha，顺手证明这里确实是个 git 工作树。空 ref 就按默认 HEAD 处理。"""
    text = str(ref if ref is not None else "").strip() or "HEAD"
    if len(text) > MAX_REF_LENGTH or not _REF_RE.match(text) or ".." in text:
        return {"ok": False, "error": "ref 形状不被接受：%s" % text[:80]}
    rep = _git(root, ["rev-parse", "--verify", f"{text}^{{commit}}"])
    if rep.get("error") or rep.get("exit_code") != 0:
        return {"ok": False, "error": "找不到提交：%s" % text,
                "detail": (rep.get("output") or "")[-200:]}
    lines = (rep.get("output") or "").strip().splitlines()
    return {"ok": True, "ref": text, "sha": lines[0] if lines else ""}


def _git(root: str, args: list[str], *, timeout: int = 60) -> dict[str, Any]:
    if not args or args[0] not in GIT_ALLOWED:
        return {"ok": False, "exit_code": None, "output": "",
                "error": "拒绝执行：%s 不在 HEAD 自检白名单内。" % (args[0] if args else "")}
    argv = ["git", "--no-pager", "-c", "core.quotepath=false", *args]
    return process_runner.run_bounded(argv, cwd=root, timeout=timeout,
                                      command_text=" ".join(argv))


def worktree_base(root: str) -> Path:
    """检出永远落在项目状态目录下（仓外），不与任何人的工作树抢路径。"""
    import project_state
    return Path(project_state.directory(root)) / "headcheck"


def make_target(root: str, sha: str) -> Path:
    import secrets
    base = worktree_base(root)
    base.mkdir(parents=True, exist_ok=True)
    return base / ("headcheck-%s-%s" % (sha[:8], secrets.token_hex(3)))


def add_worktree(root: str, target: Path, sha: str, *, timeout: int = 120) -> dict[str, Any]:
    if target.exists():
        return {"ok": False, "error": "检出目录已存在，不敢覆盖：%s" % target}
    rep = _git(root, ["worktree", "add", "--detach", str(target), sha], timeout=timeout)
    if rep.get("error") or rep.get("exit_code") != 0:
        return {"ok": False, "error": "git worktree add 失败："
                 + ((rep.get("output") or rep.get("error") or "").strip()[-300:] or "无输出")}
    # 归属标记：remove 之前必须证明这个目录是本次建的，不拆别人的检出。
    with contextlib.suppress(OSError):
        (target / _MARKER).write_text(sha + "\n", encoding="utf-8", newline="\n")
    return {"ok": True, "path": os.path.normpath(str(target))}


def remove_worktree(root: str, target: Path, *, timeout: int = 60) -> dict[str, Any]:
    """收回本次创建的检出；不是我建的一律不动。"""
    if not target.exists():
        return {"ok": True, "removed": False, "notes": ["目录已不在"]}
    marker = target / _MARKER
    if not marker.is_file():
        return {"ok": False, "removed": False,
                "error": "检出目录没有本工具的归属标记，拒绝删除：%s" % target}
    rep = _git(root, ["worktree", "remove", "--force", str(target)], timeout=timeout)
    output = (rep.get("output") or rep.get("error") or "").strip()
    if rep.get("exit_code") == 0:
        _git(root, ["worktree", "prune"], timeout=timeout)
    gone = not target.exists()
    if not gone and target.is_dir():
        # Windows 上 `worktree remove` 常留下空壳目录（自己的 cwd 或杀软句柄占住）。
        with contextlib.suppress(OSError):
            for leftover in sorted(target.rglob("*"), key=lambda p: len(str(p)), reverse=True):
                with contextlib.suppress(OSError):
                    if leftover.is_dir():
                        leftover.rmdir()
                    else:
                        leftover.unlink()
            target.rmdir()
        gone = not target.exists()
    return {"ok": gone, "removed": gone,
            "error": "" if gone else "检出没收回干净：%s" % output[-200:]}


def import_module(target: Path, name: str, *, timeout: int = 60,
                  runner: Callable[..., dict[str, Any]] = process_runner.run_bounded) -> dict[str, Any]:
    """在检出的目录里真 import 一个模块（cwd 决定 sys.path[0]，所以 import 的是 HEAD 的代码）。"""
    code = ("import importlib, json, sys\n"
            "try:\n"
            "    importlib.import_module(sys.argv[1])\n"
            "    print(json.dumps({'ok': True, 'error': ''}))\n"
            "except BaseException as exc:\n"
            "    import traceback\n"
            "    print(json.dumps({'ok': False, 'kind': type(exc).__name__,\n"
            "                      'error': '%s: %s' % (type(exc).__name__, exc),\n"
            "                      'trace': traceback.format_exc()}))\n")
    rep = runner([sys.executable, "-B", "-c", code, name], cwd=str(target), timeout=timeout,
                 command_text="import %s" % name)
    return {"module": name, "report": rep}


def _relative_importer(path: str, target: Path, repo: str) -> str:
    """traceback 里给的是【临时检出】的绝对路径，报回仓内相对路径才有用。"""
    cleaned = str(path).replace("\\", "/")
    for base in (str(target).replace("\\", "/"), str(repo).replace("\\", "/")):
        if cleaned.startswith(base + "/"):
            return cleaned[len(base) + 1:]
    return cleaned


def parse_outcome(rep: dict[str, Any]) -> dict[str, Any]:
    """子进程回的是 JSON 一行；解析失败就当命令本身出了问题，绝不默认成功。"""
    text = str((rep or {}).get("output") or "").strip()
    try:
        row = json.loads(text)
    except ValueError:
        return {"ok": False, "error": text[-400:] or "子进程没有回话",
                "trace": text, "kind": "RunnerError"}
    if not isinstance(row, dict):
        return {"ok": False, "error": text[-400:], "trace": text, "kind": "RunnerError"}
    # 只认真正的 True：`{"ok": "..."}` 这类truthy 字符串不能算成功。
    return {"ok": row.get("ok") is True, "error": str(row.get("error") or ""),
            "trace": str(row.get("trace") or ""), "kind": str(row.get("kind") or "")}


def find_importer(trace: str, name: str) -> tuple[str, int, str]:
    """从 traceback 里找出【真正写出那行 import】的帧。

    直接正则搜"File ... 后面跟一句 import"会命中 importlib 自己的帧（`<string>` /
    `importlib/__init__.py`），报出来的引用点是假的。逐帧取源码行，再从最深的帧往前
    找与被缺模块匹配的那一条。
    """
    hits: list[tuple[str, int, str]] = []
    for frame in _FRAME_RE.finditer(trace or ""):
        statement = _IMPORT_STMT_RE.search(frame.group("body"))
        if statement:
            hits.append((frame.group("path").replace("\\", "/"),
                         int(frame.group("line")), statement.group(1)))
    for path, line, imported in reversed(hits):
        if imported == name or name.startswith(imported + "."):
            return path, line, imported
    return hits[-1] if hits else ("", 0, "")


def classify(trace: str, root: str, sha: str, name: str) -> dict[str, Any]:
    """把一次 import 失败翻成人话：谁 import 了它、它在不在提交里、它在不在磁盘上。"""
    importer, importer_line, imports = find_importer(trace, name)
    relative = (name.replace(".", "/") + ".py").lstrip("/")
    package = (name.replace(".", "/") + "/__init__.py").lstrip("/")
    in_commit = any(_git(root, ["ls-tree", "-r", "--name-only", sha, "--", candidate]).get("output")
                    for candidate in (relative, package))
    on_disk = (Path(root) / relative).is_file() or (Path(root) / package).is_file()
    return {"missing": name, "importer": importer, "importer_line": importer_line,
            "import_statement": imports,
            "in_commit": bool(in_commit), "on_disk": bool(on_disk),
            "likely": ("别人未提交的新文件被提交引用（工作树能跑、单独检出必红）"
                       if on_disk and not in_commit else
                       "提交里根本没有这个模块（漏提交、改名，或它本该是第三方依赖）"
                       if not in_commit and not on_disk else "模块在提交里，失败另有原因")}


def check(root: str | Path, *, ref: str = "HEAD", modules: tuple[str, ...] | None = None,
          timeout: float = 120.0, runner: Callable[..., Any] = None) -> dict[str, Any]:
    """挂干净检出 → 逐个 import 入口模块 → 归因失败 → 无论如何收回检出。"""
    runner = runner or process_runner.run_bounded
    base = Path(root).resolve()
    if not base.is_dir():
        return {"ok": False, "passed": False, "checks": {}, "error": "当前项目目录不存在。"}
    top = _git(str(base), ["rev-parse", "--show-toplevel"])
    if top.get("error") or top.get("exit_code") != 0:
        return {"ok": False, "passed": False, "checks": {},
                "error": "这里不是 git 工作树，HEAD 自检无从开始。"}
    lines = (top.get("output") or "").strip().splitlines()
    repo = os.path.normpath(lines[0]) if lines else str(base)
    resolved = resolve_ref(repo, ref)
    if not resolved["ok"]:
        return {"ok": False, "passed": False, "checks": {}, "error": resolved["error"]}
    sha = resolved["sha"]
    # 空列表不能悄悄退回默认入口：那样"我验了 0 个模块"会被显示成"我验了 3 个"。
    wanted = ENTRY_MODULES if modules is None else tuple(modules)
    target = make_target(repo, sha)
    added = add_worktree(repo, target, sha, timeout=int(timeout))
    if not added["ok"]:
        return {"ok": False, "passed": False, "checks": {"worktree_added": False},
                "error": added["error"], "ref": resolved["ref"], "sha": sha}
    results: list[dict[str, Any]] = []
    cleanup: dict[str, Any] = {"ok": False, "removed": False, "error": "没走到收回"}
    try:
        for name in wanted:
            outcome = parse_outcome(import_module(target, name, timeout=int(timeout),
                                                  runner=runner)["report"])
            row = {"module": name, "ok": bool(outcome["ok"]),
                   "error": "" if outcome["ok"] else (outcome["error"] or outcome["trace"])[-400:]}
            if not row["ok"]:
                missing = _MISSING_RE.search(row["error"])
                if missing:
                    row.update(classify(outcome["trace"] or row["error"], repo, sha, missing.group(1)))
                    if row.get("importer"):
                        row["importer"] = _relative_importer(row["importer"], target, repo)
            results.append(row)
    finally:
        cleanup = remove_worktree(repo, target, timeout=int(timeout))
    ok_imports = [row for row in results if row.get("ok")]
    checks = {
        "worktree_added": True,
        # 一个都没跑成不算通过：`all([])` 是 True，那是废话判定。
        "every_entry_imported": bool(results) and len(ok_imports) == len(results),
        "no_leftover_checkout": bool(cleanup.get("removed")) or not target.exists(),
    }
    return {"ok": checks["every_entry_imported"], "passed": checks["every_entry_imported"],
            "checks": checks, "ref": resolved["ref"], "sha": sha, "modules": list(wanted),
            "results": results, "worktree": added["path"], "cleanup": cleanup,
            "imported": [row["module"] for row in ok_imports],
            "failures": [row for row in results if not row.get("ok")],
            "error": "" if checks["every_entry_imported"] else
                     "HEAD 单独检出时 import 失败：%s" % ", ".join(
                         row["module"] for row in results if not row.get("ok"))}


def render(result: dict[str, Any]) -> str:
    if not result.get("checks"):
        return "HEAD 自检未能开始：%s" % result.get("error", "未知原因")
    head = "HEAD 自检【%s】：%s 个入口模块在干净检出里 import 成功（%d 个尝试）。" % (
        "通过" if result.get("ok") else "未通过", len(result.get("imported") or []),
        len(result.get("modules") or []))
    lines = [head, "提交：%s = %s" % (result.get("ref"), str(result.get("sha"))[:12])]
    for row in result.get("failures") or []:
        lines.append("失败：%s → %s" % (row.get("module"), str(row.get("error") or "")[-160:]))
        if row.get("missing"):
            lines.append("  缺的模块：%s；在提交里=%s，在磁盘上=%s → %s" % (
                row["missing"], row["in_commit"], row["on_disk"], row["likely"]))
            if row.get("importer"):
                lines.append("  引用它的地方：%s:%s（import %s）" % (
                    row["importer"], row.get("importer_line") or "?",
                    row.get("import_statement") or "?"))
    if not (result.get("cleanup") or {}).get("removed"):
        lines.append("注意：临时检出没有收回干净（%s），路径 %s" % (
            (result.get("cleanup") or {}).get("error", ""), result.get("worktree")))
    if result.get("ok"):
        lines.append("能证明的只有这些：把 HEAD 单独检出后，上面这些入口能被真的 import 起来。")
    lines.append("边界：这是【能不能跑起来】的检查，不验行为对不对；入口之外的深层 import "
                 "要靠 import 链真的走到才会暴露。")
    return "\n".join(lines)


__all__ = ["ENTRY_MODULES", "GIT_ALLOWED", "HeadCheckError", "add_worktree", "check",
           "classify", "find_importer", "import_module", "make_target", "remove_worktree",
           "render", "resolve_ref", "worktree_base"]
