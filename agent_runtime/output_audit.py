"""Structured final-output audit for workflow and Subagent results."""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence


_CREDENTIAL_PATTERNS = (
    re.compile(r"(?i)(api[_-]?key|access[_-]?token|password|passwd|secret|private[_-]?key)\s*[:=]\s*([^\s,;]+)"),
    re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)
_SENSITIVE_TERMS = re.compile(r"(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|private[_-]?key)\b")
_LOCATION_QUERY = re.compile(
    r"(?i)(?:在哪|哪个文件|文件位置|路径|行号|定位|逻辑|实现|定义在哪|where|which file|line)"
)
_FILE_REF = re.compile(
    r"(?<![\w])([A-Za-z0-9_./\\-]+\.(?:gd|py|ts|tsx|js|jsx|vue|cs|cpp|cc|h|hpp|json|md))"
    r"(?:\s*:\s*L?(\d+)(?:\s*[-~]\s*L?(\d+))?)?"
)


def _trace_steps(result: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = (result.get("trace") or {}).get("steps")
    if raw is None:
        raw = result.get("steps") or []
    return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, (list, tuple)) else []


def audit_evidence(result: Mapping[str, Any] | None, *, question: str = "",
                   code_root: str = "", require_read: bool | None = None) -> dict[str, Any]:
    """Check that a location answer is backed by a readable source file.

    This is intentionally a narrow, deterministic gate.  It does not claim to
    understand the semantics of a function; it verifies the minimum evidence
    contract first: location questions need a file reference, a successful
    ``read_file`` step, and line numbers that exist in the current code root.
    Non-location answers are left untouched unless ``require_read`` is true.
    """
    result = dict(result or {})
    text = str(result.get("conclusion") or result.get("text") or result.get("merged") or "")
    location_question = bool(_LOCATION_QUERY.search(str(question or "")))
    required = location_question if require_read is None else bool(require_read)
    steps = _trace_steps(result)
    actions = [str(item.get("action") or item.get("tool") or "").strip() for item in steps]
    read_steps = [item for item, action in zip(steps, actions) if action == "read_file"]
    refs = []
    for match in _FILE_REF.finditer(text):
        refs.append({"path": match.group(1).replace("\\", "/"),
                     "start_line": int(match.group(2)) if match.group(2) else None,
                     "end_line": int(match.group(3) or match.group(2)) if match.group(2) else None})
    file_checks = []
    root = Path(code_root).resolve() if code_root else None
    for ref in refs[:50]:
        path = Path(ref["path"])
        if root is not None and not path.is_absolute():
            path = (root / path).resolve()
        exists = False
        in_root = True
        line_ok = ref["start_line"] is None
        try:
            exists = path.is_file()
            if root is not None:
                in_root = path == root or root in path.parents
            if exists and ref["start_line"] is not None:
                with path.open("r", encoding="utf-8", errors="ignore") as handle:
                    line_count = sum(1 for _ in handle)
                line_ok = 1 <= ref["start_line"] <= line_count and ref["end_line"] <= line_count
        except (OSError, ValueError):
            exists = False
            line_ok = False
        file_checks.append(dict(ref, exists=exists, in_root=in_root, line_ok=line_ok))
    # A trace can be supplied by a workflow runner or by a direct Agent.  A
    # successful flag is the only portable signal; failure observations remain
    # evidence of an attempted read, not proof of verification.
    successful_read = any(item.get("ok", True) is not False for item in read_steps)
    valid_refs = [item for item in file_checks if item["exists"] and item["in_root"] and item["line_ok"]]
    checks = {
        "location_question": location_question,
        "reference_present": bool(refs) if required else True,
        "read_file_present": successful_read if required else True,
        "references_exist": all(item["exists"] and item["in_root"] for item in file_checks) if refs else not required,
        "reference_lines_valid": all(item["line_ok"] for item in file_checks) if refs else not required,
    }
    # Without a project root, do not manufacture a filesystem failure; the
    # read_file trace is still useful and the caller can surface this gap.
    if required and refs and not root:
        checks["references_exist"] = successful_read
        checks["reference_lines_valid"] = successful_read
    return {"ok": bool(all(value for key, value in checks.items()
                             if key != "location_question")),
            "required": required, "checks": checks,
            "references": file_checks, "verified_references": valid_refs,
            "read_steps": len(read_steps)}


def redact(text: Any) -> str:
    value = str(text or "").replace("\x00", "").strip()
    for pattern in _CREDENTIAL_PATTERNS[:1]:
        value = pattern.sub(lambda match: "%s=[REDACTED]" % match.group(1), value)
    value = _CREDENTIAL_PATTERNS[1].sub("[REDACTED]", value)
    value = _CREDENTIAL_PATTERNS[2].sub("[PRIVATE_KEY_REDACTED]", value)
    return value[:8000]


def audit_final_output(result: Mapping[str, Any] | None, *, max_tool_failures: int = 3,
                       question: str = "", code_root: str = "",
                       require_evidence: bool | None = None) -> dict[str, Any]:
    result = dict(result or {})
    raw_text = str(result.get("conclusion") or result.get("text") or result.get("merged") or "")
    credential_detected = any(pattern.search(raw_text) for pattern in _CREDENTIAL_PATTERNS)
    text = redact(raw_text)
    raw_steps = (result.get("trace") or {}).get("steps")
    if raw_steps is None:
        raw_steps = result.get("steps") or []
    steps = list(raw_steps) if isinstance(raw_steps, (list, tuple)) else []
    tool_failures = [step for step in steps
                     if isinstance(step, Mapping) and step.get("ok") is False]
    file_changes = result.get("file_changes") or result.get("files") or []
    tests = result.get("tests") or result.get("test_results") or []
    if isinstance(file_changes, Mapping):
        file_changes = [file_changes]
    if isinstance(tests, Mapping):
        tests = [tests]
    failed_tests = [item for item in tests if isinstance(item, Mapping) and item.get("ok") is False]
    evidence = audit_evidence(result, question=question, code_root=code_root,
                              require_read=require_evidence)
    checks = {
        "has_result": bool(text),
        "no_replacement_chars": "\ufffd" not in text,
        "credentials_absent": not credential_detected,
        "sensitive_terms_without_value": not bool(_SENSITIVE_TERMS.search(text)) or "[REDACTED]" in text,
        "tool_failures_below_limit": len(tool_failures) < max(1, int(max_tool_failures)),
        "status_ok": result.get("status", "ok") == "ok",
        "file_evidence_bounded": len(file_changes) <= 100,
        "test_evidence_bounded": len(tests) <= 100,
        "tests_ok": not failed_tests,
        "evidence_verified": evidence["ok"],
    }
    failures = []
    if not checks["credentials_absent"]:
        failures.append({"kind": "credential", "message": "输出疑似包含凭据，已阻断", "recovery": "删除凭据后重新生成并轮换已暴露密钥"})
    if not checks["no_replacement_chars"]:
        failures.append({"kind": "encoding", "message": "输出包含乱码替换字符", "recovery": "重新读取原文并使用 UTF-8 输出"})
    if tool_failures:
        failures.append({"kind": "tool", "message": "%s 个工具步骤失败" % len(tool_failures),
                         "recovery": "检查失败工具证据，切换替代工具或重新规划"})
    if failed_tests:
        failures.append({"kind": "test", "message": "%s 个测试失败" % len(failed_tests),
                         "recovery": "先修复失败测试，再重新执行验证任务"})
    if not evidence["ok"]:
        failures.append({"kind": "evidence", "message": "定位回答缺少可核验的文件原文或有效行号",
                         "recovery": "先用 search_code 定位，再用 read_file 读取原文；无法确认时明确说明"})
    return {"ok": bool(all(checks.values())), "checks": checks, "text": text,
            "failures": failures, "tool_failure_count": len(tool_failures),
            "failed_test_count": len(failed_tests),
            "evidence_audit": evidence,
            "evidence": {"files": list(file_changes)[:100], "tests": list(tests)[:100],
                         "tools": steps[-100:]},
            "sensitive_redacted": "[REDACTED]" in text}


__all__ = ["audit_evidence", "audit_final_output", "redact"]
