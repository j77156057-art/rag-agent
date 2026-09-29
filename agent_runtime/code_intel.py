"""Agent-facing code intelligence: read-only git diff preview and symbol reference lookup.

Both helpers are strictly read-only and bounded:

* :func:`git_diff_preview` shells out to ``git`` through the bounded runner
  (:mod:`agent_runtime.process_runner`) so a runaway repo can never hang the agent,
  and only ever calls ``rev-parse``/``status``/``diff``.
* :func:`find_references` walks the source tree with a hard file/byte budget and
  uses :mod:`ast` for Python so comments and string literals are never reported
  as references (the whole reason this exists instead of ``grep``).
"""

from __future__ import annotations

import ast
import os
import re
from typing import Any

from . import process_runner

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

_GIT_TIMEOUT = 20
_GIT_MAX_TIMEOUT = 60

#: git subcommands this module is allowed to run. Read-only by construction.
_GIT_ALLOWED = frozenset({"rev-parse", "status", "diff"})

_STAT_CHARS = 6000
_LIST_FILES = 60
_RENDERED_REFS = 80

_SKIP_DIRS = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "env", "node_modules", "dist", "dist-ssr",
    "build", "build.bak", "coverage", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".docmind", "site-packages", "_archived_builds", ".tmp",
})

_SOURCE_EXTS = frozenset({
    ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte",
    ".go", ".rs", ".java", ".kt", ".kts", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp",
    ".gd", ".shader", ".hlsl", ".glsl", ".rb", ".php", ".swift", ".scala", ".lua",
    ".sql", ".proto", ".md", ".json", ".yaml", ".yml", ".toml",
})

_MAX_FILES = 4000
_MAX_FILE_BYTES = 2 * 1024 * 1024


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #

def _window(text: str, head: int = 4000, tail: int = 4000) -> tuple[str, bool, int]:
    """Return (windowed_text, truncated, total_chars) keeping head AND tail."""
    text = text or ""
    total = len(text)
    if total <= head + tail:
        return text, False, total
    omitted = total - head - tail
    return (text[:head] + f"\n...[省略 {omitted} 字符]...\n" + text[total - tail:],
            True, total)


def _is_probably_text(sample: bytes) -> bool:
    if b"\x00" in sample:
        return False
    return True


# --------------------------------------------------------------------------- #
# git diff preview
# --------------------------------------------------------------------------- #

def _run_git(args: list[str], *, cwd: str, timeout: int = _GIT_TIMEOUT) -> dict[str, Any]:
    """Run one read-only git command through the bounded runner."""
    if not args or args[0] not in _GIT_ALLOWED:
        return {"ok": False, "command": " ".join(args),
                "error": f"拒绝执行：{args[0] if args else ''} 不在只读白名单内。"}
    # core.quotepath=false: 不要把中文/非 ASCII 路径转义成八进制，否则清单不可读
    argv = ["git", "--no-pager", "-c", "core.quotepath=false"] + args
    seconds = process_runner.clamp_timeout(timeout, _GIT_MAX_TIMEOUT, _GIT_TIMEOUT)
    rep = process_runner.run_bounded(argv, cwd=cwd, timeout=seconds,
                                     command_text=" ".join(argv))
    # git writes warnings to stderr, which the runner merges into stdout. Left
    # alone they get parsed as status entries ("w ning: could not open ...").
    if rep.get("output"):
        rep["output"] = _strip_git_noise(rep["output"])
    return rep


#: git 的诊断行（stderr 被合并进 stdout）。diff 正文行必带前导 +/-/空格/@，
#: 因此「行首直接是 warning:」的一定是噪声，可以安全剔除。
_GIT_NOISE_LINE = re.compile(r"^(warning|error|fatal|hint|notice)\s*:\s*", re.I)


def _strip_git_noise(text: str) -> str:
    """Drop git's top-level diagnostic lines while preserving everything else."""
    if not text:
        return text
    kept = [ln for ln in text.splitlines() if not _GIT_NOISE_LINE.match(ln)]
    return "\n".join(kept) + ("\n" if text.endswith("\n") else "")


def _repo_top(root: str) -> tuple[str | None, str]:
    """Resolve the worktree top-level for ``root``; (None, reason) when unavailable."""
    rep = _run_git(["rev-parse", "--show-toplevel"], cwd=root)
    if rep.get("error"):
        return None, f"无法执行 git：{rep['error']}"
    if rep.get("timed_out"):
        return None, "git rev-parse 超时，仓库可能过大或 git 被卡住。"
    if rep.get("exit_code") != 0:
        detail = (rep.get("output") or "").strip().splitlines()
        return None, "当前代码根目录不是 git 工作树：" + (detail[0][:200] if detail else "无输出")
    out = (rep.get("output") or "").strip()
    if not out:
        return None, "git rev-parse 返回空路径，无法定位仓库顶层。"
    return os.path.normpath(out.splitlines()[0]), ""


def _scope_prefix(top: str, root: str) -> str:
    """Relative path of ``root`` inside ``top`` ('' when root *is* the top)."""
    try:
        rel = os.path.relpath(os.path.normpath(root), top)
    except ValueError:
        return ""
    rel = rel.replace("\\", "/")
    return "" if rel in (".", "") else rel


def _validate_paths(paths: list[str], root: str, top: str) -> tuple[list[str] | None, str]:
    """Reject flag injection and anything escaping the code root."""
    root_abs = os.path.normpath(root)
    top_abs = os.path.normpath(top)
    kept: list[str] = []
    for raw in paths:
        p = (raw or "").strip().strip("'\"")
        if not p:
            continue
        if p.startswith("-"):
            return None, f"拒绝路径 {p!r}：看起来像命令行选项，疑似参数注入。"
        candidate = p if os.path.isabs(p) else os.path.join(root_abs, p)
        candidate = os.path.normpath(candidate)
        if not (candidate == root_abs or candidate.startswith(root_abs + os.sep)):
            return None, f"拒绝路径 {p!r}：不在代码根目录内（禁止越界读取）。"
        rel = os.path.relpath(candidate, top_abs).replace("\\", "/")
        if rel in (".", ""):
            rel = "."
        kept.append(rel)
    return kept, ""


#: porcelain 状态列合法取值：X∈[MADRCUT? ]，Y∈[MADRCUT?! ]
_VALID_XY = re.compile(r"^([MADRCUT? ])([MADRCUT?! ])$")


def _parse_porcelain(text: str) -> list[tuple[str, str]]:
    """Parse `git status --porcelain`, dropping anything that is not a real entry.

    git diagnostics (already stripped) or unexpected output would otherwise be
    mistaken for a file: `warning: ...` parses as status ``w `` + ``ning: ...``.
    """
    entries: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        if not _VALID_XY.match(xy) or xy == "  ":
            continue
        rest = line[3:]
        path = rest
        if " -> " in rest and "R" in xy:
            path = rest.split(" -> ", 1)[1]
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        entries.append((xy, path))
    return entries


def git_diff_preview(root: str, *, paths: list[str] | None = None, staged: bool = False,
                     stat_only: bool = False, context: int = 3,
                     timeout: int = _GIT_TIMEOUT) -> dict[str, Any]:
    """Read-only preview of what changed. Never writes, never stages, never commits."""
    result: dict[str, Any] = {
        "ok": False, "root": root, "staged": bool(staged), "stat_only": bool(stat_only),
        "top": None, "paths": [], "files": [], "staged_files": [], "unstaged_files": [],
        "untracked_files": [], "stat": "", "stat_truncated": False, "diff": "",
        "diff_truncated": False, "diff_total_chars": 0, "error": "", "notes": [],
    }
    if not root or not os.path.isdir(root):
        result["error"] = "代码根目录不存在或未配置。"
        return result
    top, reason = _repo_top(root)
    if top is None:
        result["error"] = reason
        return result
    result["top"] = top

    requested = [p for p in (paths or []) if str(p).strip()]
    if requested:
        kept, err = _validate_paths(requested, root, top)
        if kept is None:
            result["error"] = err
            return result
        scope = kept
    else:
        prefix = _scope_prefix(top, root)
        scope = [prefix] if prefix else []
    result["paths"] = scope

    path_args = ["--"] + scope if scope else []

    # 1) inventory: git diff cannot see untracked files, so surface them separately.
    rep = _run_git(["status", "--porcelain"] + path_args, cwd=top, timeout=timeout)
    if rep.get("error"):
        result["error"] = f"git status 失败：{rep['error']}"
        return result
    entries = _parse_porcelain(rep.get("output") or "")
    staged_files, unstaged_files, untracked_files = [], [], []
    for xy, path in entries:
        x, y = xy[0], xy[1]
        if xy == "??":
            untracked_files.append(path)
            continue
        if x not in (" ", "?"):
            staged_files.append(f"{x} {path}")
        if y not in (" ", "?"):
            unstaged_files.append(f"{y} {path}")
    result["staged_files"] = staged_files
    result["unstaged_files"] = unstaged_files
    result["untracked_files"] = untracked_files
    result["files"] = [p for _, p in entries]
    result["ok"] = True

    if not entries:
        result["notes"].append("工作树与暂存区均无改动（git status 为空）。")
        return result
    if untracked_files:
        result["notes"].append(
            f"{len(untracked_files)} 个未跟踪文件不会出现在 git diff 中"
            "（diff 只看已跟踪文件）；提交前需先 git add。"
        )

    # 2) stat
    rep = _run_git(["diff", "--stat"] + (["--cached"] if staged else []) + path_args,
                   cwd=top, timeout=timeout)
    if rep.get("error"):
        result["error"] = f"git diff --stat 失败：{rep['error']}"
        result["ok"] = False
        return result
    stat, stat_trunc, _ = _window(rep.get("output") or "", _STAT_CHARS, 0)
    result["stat"] = stat
    result["stat_truncated"] = stat_trunc

    if stat_only:
        return result

    # 3) body
    try:
        ctx = max(0, min(int(context), 50))
    except (TypeError, ValueError):
        ctx = 3
    rep = _run_git(["diff", f"-U{ctx}"] + (["--cached"] if staged else []) + path_args,
                   cwd=top, timeout=timeout)
    if rep.get("error"):
        result["error"] = f"git diff 失败：{rep['error']}"
        result["ok"] = False
        return result
    result["diff"] = rep.get("output") or ""
    result["diff_truncated"] = bool(rep.get("truncated"))
    result["diff_total_chars"] = int(rep.get("total_chars") or 0)
    if not result["diff"].strip():
        side = "暂存区" if staged else "工作树"
        result["notes"].append(f"{side}没有内容差异（改动可能只在另一侧，或仅存在未跟踪文件）。")
    return result


def render_git_diff(result: dict[str, Any]) -> str:
    if result.get("error") and not result.get("ok"):
        return f"dev_git_diff 失败：{result['error']}"
    lines: list[str] = []
    staged = result.get("staged")
    lines.append(f"=== git diff（{'暂存区 vs HEAD' if staged else '工作树 vs HEAD'}）===")
    if result.get("top"):
        lines.append(f"仓库顶层: {result['top']}")
    scope = result.get("paths") or []
    lines.append("范围: " + ("、".join(scope) if scope else "代码根目录全部"))

    s, u, t = (result.get("staged_files") or [], result.get("unstaged_files") or [],
               result.get("untracked_files") or [])
    lines.append(f"变更文件 {len(s) + len(u) + len(t)}（已暂存 {len(s)} / 未暂存 {len(u)} / 未跟踪 {len(t)}）")
    combined = s + u + [f"?? {p}" for p in t]
    for item in combined[:_LIST_FILES]:
        lines.append("  " + item)
    if len(combined) > _LIST_FILES:
        lines.append(f"  …另有 {len(combined) - _LIST_FILES} 个文件未列出")

    if result.get("stat"):
        lines.append("--- 统计 ---")
        lines.append(result["stat"].rstrip())
        if result.get("stat_truncated"):
            lines.append("（统计已截断）")

    if not result.get("stat_only"):
        lines.append("--- 差异正文 ---")
        body = (result.get("diff") or "").rstrip()
        lines.append(body if body else "（无内容差异）")
        if result.get("diff_truncated"):
            lines.append(f"（差异正文已窗口化：共 {result.get('diff_total_chars')} 字符，"
                         "保留开头与结尾；需要看中间部分请加 paths: 限定到单个文件）")
    for note in result.get("notes") or []:
        lines.append("提示: " + note)
    if result.get("ok") and result.get("error"):
        lines.append("注意: " + result["error"])
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# symbol reference lookup
# --------------------------------------------------------------------------- #

def _walk_source_files(root: str, scope: list[str] | None = None):
    """Yield source file paths under root (bounded by _MAX_FILES)."""
    root_abs = os.path.normpath(root)
    bases: list[str] = []
    singles: list[str] = []  # scope 直接指到文件时，只看该文件
    if scope:
        for rel in scope:
            cand = rel if os.path.isabs(rel) else os.path.join(root_abs, rel)
            cand = os.path.normpath(cand)
            if cand == root_abs or cand.startswith(root_abs + os.sep):
                (bases if os.path.isdir(cand) else singles).append(cand)
    if not bases and not singles:
        bases = [root_abs]
    seen = 0
    for full in singles:
        if os.path.splitext(full)[1].lower() in _SOURCE_EXTS:
            yield full
    for base in bases:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
            for name in filenames:
                if os.path.splitext(name)[1].lower() not in _SOURCE_EXTS:
                    continue
                full = os.path.join(dirpath, name)
                try:
                    if os.path.getsize(full) > _MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                seen += 1
                if seen > _MAX_FILES:
                    return
                yield full


def _read_lines(full: str) -> list[str] | None:
    try:
        with open(full, "rb") as fh:
            sample = fh.read(_MAX_FILE_BYTES)
    except OSError:
        return None
    if not _is_probably_text(sample):
        return None
    try:
        return sample.decode("utf-8", errors="replace").splitlines()
    except Exception:  # noqa: BLE001
        return None


def _py_matches(lines: list[str], symbol: str, rel: str) -> tuple[list[dict], list[dict]]:
    """AST-precise Python lookup: comments and string literals are never matches."""
    defs: list[dict] = []
    refs: list[dict] = []
    src = "\n".join(lines)
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return _regex_matches(lines, symbol, rel, lang="python-fallback")
    for node in ast.walk(tree):
        kind = None
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol:
                kind = "def"
        elif isinstance(node, ast.Name) and node.id == symbol:
            kind = "ref"
        elif isinstance(node, ast.Attribute) and node.attr == symbol:
            kind = "attr"
        elif isinstance(node, ast.alias) and symbol in (node.name.split(".")[-1],
                                                        node.asname or ""):
            kind = "import"
        elif isinstance(node, ast.arg) and node.arg == symbol:
            kind = "param"
        elif isinstance(node, (ast.Global, ast.Nonlocal)) and symbol in node.names:
            kind = "ref"
        if kind is not None:
            line = getattr(node, "lineno", 0) or 0
            code = lines[line - 1].strip() if 1 <= line <= len(lines) else ""
            entry = {"file": rel, "line": line, "code": code, "kind": kind}
            (defs if kind == "def" else refs).append(entry)

    # 字符串/字典键里的同名文本（如 TOOLS 注册表的 "dev_git_diff"）：AST 的 Name
    # 匹配抓不到，但重命名函数时它必须同步改，所以单列一类低置信命中。
    rx = re.compile(r"(?<![\w$])" + re.escape(symbol) + r"(?![\w$])")
    seen_lines = {e["line"] for e in defs} | {e["line"] for e in refs}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        if not rx.search(node.value):
            continue
        line = getattr(node, "lineno", 0) or 0
        if line in seen_lines:
            continue
        seen_lines.add(line)
        refs.append({"file": rel, "line": line,
                     "code": lines[line - 1].strip() if 1 <= line <= len(lines) else "",
                     "kind": "str"})
    return defs, refs


def _regex_matches(lines: list[str], symbol: str, rel: str,
                   lang: str = "text") -> tuple[list[dict], list[dict]]:
    rx = re.compile(r"(?<![\w$])" + re.escape(symbol) + r"(?![\w$])")
    refs: list[dict] = []
    for i, line in enumerate(lines, 1):
        if not rx.search(line):
            continue
        stripped = line.strip()
        if stripped.startswith(("#", "//", "*", "/*")):
            continue  # comment-only noise
        refs.append({"file": rel, "line": i, "code": stripped, "kind": "ref"})
    return [], refs


def _validate_scope(root: str, scope: list[str] | None) -> tuple[list[str] | None, str]:
    """scope 必须真实存在且落在代码根内——写错时静默退化成全仓扫描会更糟。"""
    if not scope:
        return [], ""
    root_abs = os.path.normpath(root)
    kept: list[str] = []
    for rel in scope:
        cand = os.path.normpath(rel if os.path.isabs(rel) else os.path.join(root_abs, rel))
        if not (cand == root_abs or cand.startswith(root_abs + os.sep)):
            return None, f"拒绝 scope {rel!r}：不在代码根目录内。"
        if not os.path.exists(cand):
            return None, f"scope 不存在：{rel}"
        kept.append(cand)
    return kept, ""


def find_references(root: str, symbol: str, *, scope: list[str] | None = None,
                    kind: str = "all", limit: int = _RENDERED_REFS) -> dict[str, Any]:
    """Locate definitions and references of ``symbol`` with a hard scan budget."""
    result: dict[str, Any] = {
        "ok": False, "symbol": symbol or "", "root": root, "scanned": 0,
        "definitions": [], "references": [], "total": 0, "truncated": False,
        "by_file": {}, "error": "", "notes": [],
    }
    symbol = (symbol or "").strip()
    if not symbol:
        result["error"] = "参数缺失：请提供 symbol: <符号名>。"
        return result
    if not root or not os.path.isdir(root):
        result["error"] = "代码根目录不存在或未配置。"
        return result
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = _RENDERED_REFS
    want = (kind or "all").strip().lower()
    if want not in ("all", "def", "ref"):
        want = "all"
    scope, scope_err = _validate_scope(root, scope)
    if scope is None:
        result["error"] = scope_err
        return result

    root_abs = os.path.normpath(root)
    definitions: list[dict] = []
    references: list[dict] = []
    scanned = 0
    hit_limit = False
    for full in _walk_source_files(root_abs, scope):
        scanned += 1
        lines = _read_lines(full)
        if lines is None:
            continue
        rel = os.path.relpath(full, root_abs).replace("\\", "/")
        if full.endswith(".py") or full.endswith(".pyi"):
            defs, refs = _py_matches(lines, symbol, rel)
        else:
            defs, refs = _regex_matches(lines, symbol, rel)
        definitions.extend(defs)
        references.extend(refs)
        if len(definitions) + len(references) > limit * 8:
            hit_limit = True
            break

    string_hits = [e for e in references if e.get("kind") == "str"]
    code_refs = [e for e in references if e.get("kind") != "str"]
    by_file: dict[str, int] = {}
    for entry in code_refs:
        by_file[entry["file"]] = by_file.get(entry["file"], 0) + 1
    result.update({
        "ok": True, "scanned": scanned, "definitions": definitions[:limit],
        "references": code_refs[:limit], "string_hits": string_hits[:limit],
        "by_file": by_file, "total": len(definitions) + len(code_refs) + len(string_hits),
        "truncated": hit_limit or len(code_refs) > limit or len(definitions) > limit,
    })
    if want == "def":
        result["references"] = []
        result["string_hits"] = []
    elif want == "ref":
        result["definitions"] = []
    if hit_limit:
        result["notes"].append(
            f"命中过多已提前停止（扫描 {scanned} 个文件）；请用 scope: 限定目录或换更精确的符号名。")
    if not definitions and not references:
        result["notes"].append("未找到任何匹配；确认符号名与拼写，或该符号位于被跳过的构建目录中。")
    return result


def render_references(result: dict[str, Any]) -> str:
    if result.get("error") and not result.get("ok"):
        return f"dev_find_references 失败：{result['error']}"
    defs = result.get("definitions") or []
    refs = result.get("references") or []
    strs = result.get("string_hits") or []
    lines = [f"=== 符号 {result.get('symbol')} ===",
             f"定义 {len(defs)} 处 / 引用 {len(refs)} 处 / 字符串键 {len(strs)} 处"
             f"（扫描 {result.get('scanned')} 个文件）"]
    if defs:
        lines.append("--- 定义 ---")
        for d in defs:
            lines.append(f"  {d['file']}:L{d['line']}  {d['code']}")
    if refs:
        lines.append("--- 引用 ---")
        shown = 0
        for rel, count in sorted((result.get("by_file") or {}).items(),
                                 key=lambda kv: -kv[1]):
            lines.append(f"  {rel} ({count})")
            for r in refs:
                if r["file"] != rel:
                    continue
                lines.append(f"    L{r['line']}  {r['code']}")
                shown += 1
                if shown >= 60:
                    break
            if shown >= 60:
                lines.append("  …（引用过多，先显示前 60 条；请用 scope: 缩小范围）")
                break
    if strs:
        lines.append("--- 字符串/配置键命中（重命名时也要同步改，低置信）---")
        for e in strs[:20]:
            lines.append(f"  {e['file']}:L{e['line']}  {e['code']}")
        if len(strs) > 20:
            lines.append(f"  …另有 {len(strs) - 20} 处")
    for note in result.get("notes") or []:
        lines.append("提示: " + note)
    return "\n".join(lines)
