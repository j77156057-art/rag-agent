"""原子化的 unified diff 应用与文件移动。

为什么需要它：`apply_edit` 一次只能改一个文件的一处位置，跨文件重构只能靠
`dev_apply_edits` 手写 old_text 块；从 `dev_git_diff` 或外部评审里拿到的真正的
diff 没有入口。这里补上，并且必须**全成或全不成**——补丁改到一半留下的半成品
比不改更糟。

`move_path` 走 `git mv` 而不是文件系统移动，是为了让 git 认到重命名、保住历史与
blame。注意 `run_command` 本来就只拦【历史类】git 子命令（commit/push/reset/
checkout/worktree 等），`git mv` 是可以直接跑的；这个工具的价值在于把移动【约束】
起来：越界拒绝、目标已存在拒绝、单文件上限、并强制提示回归检查。
"""
from __future__ import annotations

import ast
import os
import re
import shutil
import tempfile
from typing import Any

from . import process_runner

MAX_FILES = 20
#: 新建文件沿用 create_file 的 200KB 约定；但【修改已有文件】不能被它挡住——
#: 本仓库的 agent.py / HANDOFF.md 都超过 200KB，真代码上的 git diff 会被误拒。
#: 风险不在文件多大，而在补丁多大，所以补丁正文另有上限。
MAX_NEW_FILE_BYTES = 200_000
MAX_TARGET_BYTES = 4_000_000
MAX_PATCH_BYTES = 4_000_000
MAX_HUNK_LINES = 2000
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_DEV_NULL = {"/dev/null", "nul"}
_PREFIXES = ("a/", "b/", "i/", "w/", "c/")


def _resolve(root: str, path: str) -> tuple[str, str]:
    """补丁/参数里的路径 → root 内的相对路径（posix 风格）；越界与绝对路径拒绝。"""
    text = str(path or "").strip().strip("'\"").replace("\\", "/")
    if text in _DEV_NULL:
        return "", ""
    for prefix in _PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    if not text:
        return "", "缺少路径。"
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or ".." in text.split("/"):
        return "", f"拒绝路径 {path!r}：必须是代码根目录内的相对路径（禁止越界）。"
    full = os.path.normpath(os.path.join(root, text))
    root_abs = os.path.normpath(root)
    if not (full == root_abs or full.startswith(root_abs + os.sep)):
        return "", f"拒绝路径 {path!r}：不在代码根目录内（禁止越界写入）。"
    return text, ""


def parse_unified(text: str, root: str) -> tuple[list[dict[str, Any]], list[str]]:
    """解析 unified diff。返回 (patches, errors)；errors 非空则整份补丁不可用。"""
    patches: list[dict[str, Any]] = []
    errors: list[str] = []
    current: dict[str, Any] | None = None
    hunk: dict[str, Any] | None = None
    old_path = ""
    for raw in (text or "").splitlines():
        if raw.startswith("--- "):
            old_path = raw[4:].split("\t")[0].strip()
            hunk = None
            continue
        if raw.startswith("+++ "):
            new_path = raw[4:].split("\t")[0].strip()
            current, hunk = None, None
            if new_path in _DEV_NULL:
                if old_path in _DEV_NULL:
                    errors.append("既没有源也没有目标的空补丁。")
                else:
                    errors.append(f"拒绝在补丁里删除文件 {old_path!r}：补丁只做新建与修改，"
                                  "删除请走带确认的删除工具。")
                continue
            path, err = _resolve(root, new_path)
            if err:
                errors.append(err)
                continue
            current = {"path": path, "created": old_path in _DEV_NULL, "hunks": []}
            patches.append(current)
            continue
        match = _HUNK_RE.match(raw)
        if match:
            if current is None:
                errors.append(f"遇到 @@ 头却没有 +++ 目标：{raw[:80]}")
                hunk = None
                continue
            hunk = {"start_old": int(match.group(1)), "start_new": int(match.group(3)),
                    "lines": []}
            current["hunks"].append(hunk)
            continue
        if hunk is None or current is None:
            continue
        if raw.startswith("\\"):          # \ No newline at end of file
            continue
        if raw[:1] in (" ", "+", "-"):
            hunk["lines"].append((raw[0], raw[1:]))
            if len(hunk["lines"]) > MAX_HUNK_LINES:
                errors.append(f"{current['path']}：单个 hunk 超过 {MAX_HUNK_LINES} 行，拒绝。")
                current, hunk = None, None
            continue
    for patch in list(patches):
        if not patch["hunks"]:
            errors.append(f"{patch['path']}：没有任何 @@ hunk。")
            patches.remove(patch)
    return patches, errors


def _consumes(hunk: dict[str, Any]) -> int:
    return sum(1 for op, _ in hunk["lines"] if op in (" ", "-"))


def _matches(lines: list[str], hunk: dict[str, Any], at: int) -> bool:
    """上下文与被删行必须逐字相等——不做模糊匹配，宁可报错也不改错地方。"""
    need = _consumes(hunk)
    if at < 0 or at + need > len(lines):
        return False
    cursor = at
    for op, payload in hunk["lines"]:
        if op in (" ", "-"):
            if lines[cursor] != payload:
                return False
            cursor += 1
    return True


def _declared_position(lines: list[str], hunk: dict[str, Any]) -> int:
    """补丁声明的 0-based 落点：纯插入是「第 N 行之后」= N，其余是 N-1。"""
    if _consumes(hunk) == 0:
        return min(max(0, hunk["start_old"]), len(lines))
    return min(max(0, hunk["start_old"] - 1), len(lines))


def _find_offset(lines: list[str], hunk: dict[str, Any]) -> int:
    """声明落点优先，其次按距离由近到远扫描；-1 表示上下文对不上。"""
    want = _declared_position(lines, hunk)
    if _consumes(hunk) == 0 or _matches(lines, hunk, want):
        return want
    for distance in range(1, len(lines) + 2):
        for candidate in (want - distance, want + distance):
            if _matches(lines, hunk, candidate):
                return candidate
    return -1


def _mismatch_report(path: str, lines: list[str], hunk: dict[str, Any]) -> str:
    wanted = "\n".join(f"    {payload!r}" for op, payload in hunk["lines"][:6] if op != "+")
    start = max(0, hunk["start_old"] - 3)
    actual = "\n".join(f"    {line!r}" for line in lines[start:start + 6]) or "    （文件比这短）"
    return (f"{path}：@@ -{hunk['start_old']} 这一块的上下文对不上，补丁整体拒绝。\n"
            f"  补丁要求的开头：\n{wanted}\n  文件实际那里是：\n{actual}\n"
            f"  先用 dev_git_diff / read_file 确认当前内容，再重出补丁。")


def _apply_hunks(path: str, original: list[str],
                 patch: dict[str, Any]) -> tuple[list[str] | None, list[str], str]:
    """返回 (新文件行, 移位提示, 错误)。错误非空时新文件行为 None。"""
    lines = list(original)
    shifted: list[str] = []
    for hunk in patch["hunks"]:
        offset = _find_offset(lines, hunk)
        if offset < 0:
            return None, [], _mismatch_report(path, lines, hunk)
        if offset != _declared_position(lines, hunk):
            shifted.append(f"{path}：@@ -{hunk['start_old']} 实际落在第 {offset + 1} 行"
                           f"（上下文一致，基线行号偏了）")
        cursor = offset
        for op, payload in hunk["lines"]:
            if op == " ":
                cursor += 1
            elif op == "-":
                del lines[cursor]
            else:
                lines.insert(cursor, payload)
                cursor += 1
    return lines, shifted, ""


def _syntax_error(path: str, text: str) -> str:
    if not path.endswith((".py", ".pyi")):
        return ""
    try:
        ast.parse(text)
    except SyntaxError as exc:
        return f"{path}：打完补丁后 Python 语法不通过（第 {exc.lineno} 行）：{exc.msg}"
    return ""


def apply_patch(root: str, text: str, *, dry_run: bool = False) -> dict[str, Any]:
    """把一段 unified diff 应用到代码根目录内；全成才写盘，任一失败则一个都不写。"""
    result: dict[str, Any] = {"ok": False, "dry_run": bool(dry_run), "changes": [],
                              "errors": [], "notes": [], "written": []}
    if not root or not os.path.isdir(root):
        result["errors"].append("代码根目录不存在或未配置。")
        return result
    if len((text or "").encode("utf-8")) > MAX_PATCH_BYTES:
        result["errors"].append(f"补丁正文超过 {MAX_PATCH_BYTES} 字节，拒绝；请拆成多个补丁。")
        return result
    patches, errors = parse_unified(text, root)
    if errors:
        result["errors"].extend(errors)
        return result
    if not patches:
        result["errors"].append("补丁里没有可应用的 hunk（检查 --- / +++ / @@ 头）。")
        return result
    if len(patches) > MAX_FILES:
        result["errors"].append(f"一次最多改 {MAX_FILES} 个文件（当前 {len(patches)}）；请拆分。")
        return result

    staged: dict[str, bytes] = {}
    for patch in patches:
        path = patch["path"]
        full = os.path.join(root, *path.split("/"))
        if os.path.isdir(full):
            result["errors"].append(f"{path} 是目录，不能当文件打补丁。")
            continue
        exists = os.path.isfile(full)
        if patch["created"] and exists:
            result["errors"].append(f"{path} 已存在，但补丁声明为新建。")
            continue
        if not patch["created"] and not exists:
            result["errors"].append(f"{path} 不存在，而补丁不是新建（--- 不是 /dev/null）。")
            continue
        original: list[str] = []
        if exists:
            if os.path.getsize(full) > MAX_TARGET_BYTES:
                result["errors"].append(f"{path} 超过目标文件上限 {MAX_TARGET_BYTES} 字节。")
                continue
            try:
                with open(full, "r", encoding="utf-8", newline="") as handle:
                    original = handle.read().splitlines()
            except (OSError, UnicodeError) as exc:
                result["errors"].append(f"{path} 读不了或不是文本：{exc}")
                continue
        applied, shifted, message = _apply_hunks(path, original, patch)
        if applied is None:
            result["errors"].append(message)
            continue
        result["notes"].extend(shifted)
        body = ("\n".join(applied) + "\n") if applied else ""
        if patch["created"] and len(body.encode("utf-8")) > MAX_NEW_FILE_BYTES:
            result["errors"].append(f"{path}：新建文件超过 {MAX_NEW_FILE_BYTES} 字节上限"
                                    "（与 create_file 同一约定）。")
            continue
        syntax = _syntax_error(path, body)
        if syntax:
            result["errors"].append(syntax)
            continue
        added = sum(1 for hunk in patch["hunks"] for op, _ in hunk["lines"] if op == "+")
        removed = sum(1 for hunk in patch["hunks"] for op, _ in hunk["lines"] if op == "-")
        result["changes"].append({"file": path, "added": added, "removed": removed,
                                  "created": bool(patch["created"]),
                                  "bytes": len(body.encode("utf-8"))})
        staged[path] = body.encode("utf-8")

    if result["errors"]:
        result["notes"].append("整体未写入（原子性）：修正补丁后重试即可，树上没有半成品。")
        return result
    if dry_run:
        result["ok"] = True
        result["notes"].append("dry_run：只做校验与语法检查，未写盘。")
        return result

    written: list[tuple[str, str, bytes | None]] = []
    try:
        for path, body in sorted(staged.items()):
            full = os.path.join(root, *path.split("/"))
            os.makedirs(os.path.dirname(full) or root, exist_ok=True)
            backup: bytes | None = None
            if os.path.isfile(full):
                with open(full, "rb") as handle:
                    backup = handle.read()
            fd, tmp = tempfile.mkstemp(prefix="docmind-patch-",
                                       dir=os.path.dirname(full) or root)
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
            written.append((path, full, backup))
            os.replace(tmp, full)
        result["written"] = sorted(staged)
        result["ok"] = True
    except (OSError, UnicodeError) as exc:
        for path, full, backup in written:
            try:
                if backup is None:
                    if os.path.isfile(full):
                        os.remove(full)
                else:
                    with open(full, "wb") as handle:
                        handle.write(backup)
            except OSError:
                result["notes"].append(f"{path} 回滚失败，需要人工检查。")
        result["errors"].append(f"写入失败，已尽量回滚：{exc}")
    return result


# --------------------------------------------------------------------------- #
# move（跟踪中的文件走 git mv 保历史，其余退回文件系统）
# --------------------------------------------------------------------------- #

def _is_tracked(root: str, rel: str, timeout: int) -> bool:
    rep = process_runner.run_bounded(["git", "--no-pager", "ls-files", "--error-unmatch", rel],
                                     cwd=root, timeout=timeout, command_text="git ls-files")
    return bool(rep) and not rep.get("error") and rep.get("exit_code") == 0


def move_path(root: str, src: str, dst: str, *, timeout: int = 30) -> dict[str, Any]:
    """移动/重命名根目录内的单个文件。跟踪中的走 `git mv` 保历史，其余退回文件系统移动。"""
    result: dict[str, Any] = {"ok": False, "engine": "", "src": "", "dst": "",
                              "error": "", "notes": []}
    if not root or not os.path.isdir(root):
        result["error"] = "代码根目录不存在或未配置。"
        return result
    src_rel, err = _resolve(root, src)
    if err:
        result["error"] = err
        return result
    dst_rel, err = _resolve(root, dst)
    if err:
        result["error"] = err
        return result
    src_full = os.path.join(root, *src_rel.split("/"))
    dst_full = os.path.join(root, *dst_rel.split("/"))
    if not os.path.isfile(src_full):
        result["error"] = f"源文件不存在或不是文件：{src_rel}"
        return result
    if os.path.exists(dst_full):
        result["error"] = f"目标已存在，不覆盖：{dst_rel}"
        return result
    if os.path.getsize(src_full) > MAX_TARGET_BYTES:
        result["error"] = f"源文件超过 {MAX_TARGET_BYTES} 字节，拒绝移动。"
        return result
    os.makedirs(os.path.dirname(dst_full) or root, exist_ok=True)
    remind = "移动后用 dev_find_references / grep 检查旧路径的 import 与引用。"
    if _is_tracked(root, src_rel, timeout):
        argv = ["git", "--no-pager", "mv", "--", src_rel, dst_rel]
        rep = process_runner.run_bounded(argv, cwd=root, timeout=timeout,
                                         command_text=" ".join(argv))
        if not rep.get("error") and rep.get("exit_code") == 0:
            result.update({"ok": True, "engine": "git mv", "src": src_rel, "dst": dst_rel})
            result["notes"] = ["git 认到重命名，历史与 blame 延续（已暂存这一次重命名，未提交）。",
                               remind]
            return result
        result["notes"].append("git mv 没成（" + ((rep.get("output") or rep.get("error")
                                                   or "").strip()[-200:] or "无输出")
                               + "），退回文件系统移动。")
    try:
        shutil.move(src_full, dst_full)
    except (OSError, shutil.Error) as exc:
        result["error"] = f"移动失败：{exc}"
        return result
    result.update({"ok": True, "engine": "filesystem", "src": src_rel, "dst": dst_rel})
    result["notes"].append("未走 git（文件未被跟踪），git 会看成删除+新增。" + remind)
    return result


# --------------------------------------------------------------------------- #
# render
# --------------------------------------------------------------------------- #

def render_patch(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        lines = ["补丁被拒绝，一个文件都没改："]
        lines += ["  " + str(err).rstrip() for err in result.get("errors") or ["未知原因"]]
    else:
        verb = "校验通过（dry_run，未写盘）" if result.get("dry_run") else "补丁已应用"
        changes = result.get("changes") or []
        added = sum(row["added"] for row in changes)
        removed = sum(row["removed"] for row in changes)
        lines = [f"{verb}：{len(changes)} 个文件，+{added} -{removed}"]
        for row in changes:
            flag = "（新建）" if row["created"] else ""
            lines.append(f"  {row['file']}  +{row['added']} -{row['removed']}{flag}")
    lines += ["提示: " + note for note in result.get("notes") or []]
    return "\n".join(lines)


def render_move(result: dict[str, Any]) -> str:
    if not result.get("ok"):
        return "移动失败：" + (result.get("error") or "未知原因")
    lines = [f"已移动 {result['src']} → {result['dst']}（{result['engine']}）"]
    lines += ["提示: " + note for note in result.get("notes") or []]
    return "\n".join(lines)
