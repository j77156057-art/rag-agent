"""Application-independent tool results and exception isolation.

Legacy text tools are adapted at this boundary. Structured tools do not use
message substrings to signal failure. Permissions remain the caller's concern.
"""
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    text: str
    data: Any = None
    error_kind: str = ""
    artifacts: dict = field(default_factory=dict)


def execute_tool(function: Callable, argument: str, legacy_failure: Callable) -> ToolResult:
    try:
        value = function(argument)
        if isinstance(value, ToolResult):
            return value
        if not isinstance(value, str):
            return ToolResult(False, "工具执行失败：返回值不符合工具协议。", error_kind="invalid_result")
        ok = not legacy_failure(value)
        return ToolResult(ok, value, error_kind="" if ok else "legacy_failure")
    except Exception as exc:
        # Exception messages may contain credentials or arbitrary external data.
        return ToolResult(False, "工具执行失败（%s）。" % type(exc).__name__, error_kind="exception")
