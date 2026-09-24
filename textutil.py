"""把工具 / 回调返回值统一落地为可读字符串的公共工具。

背景：部分工具（`web_fetch` / `web_search` 等）在部分版本或分支上返回的是带 `.text`
的包装对象（`ToolResult`），而非裸字符串；下游若对返回值直接做切片 / 正则 / `in` /
`join`，会抛 `TypeError`（曾导致 HTTP 500）。这里集中一处规整，避免各模块各自维护
`_as_text` 而行为漂移。

本模块**不 import 任何项目内模块**（仅标准库），因此不存在循环依赖；
`tools.py` / `llm.py` / `api.py` / `mcp_autoconnect.py` 均可安全引用。
"""
from __future__ import annotations

from typing import Any

__all__ = ["as_text"]


def as_text(value: Any) -> str:
    """把 value 规整为 str，绝不抛出。

    - ``None`` -> ``""``
    - ``str`` -> 原样返回
    - 有 ``.text`` 且为 ``str``（如 ``ToolResult``）-> 取 ``.text``
    - 其它 -> ``str(value)``；连 ``str()`` 都抛异常时回落 ``""``
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    text = getattr(value, "text", None)
    if isinstance(text, str):
        return text
    try:
        return str(value)
    except Exception:
        return ""
