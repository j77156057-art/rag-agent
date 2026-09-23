"""Shared, application-independent Agent execution contracts."""

from .applications import (
    APPLICATIONS,
    DEVELOPER_APP,
    ApplicationSpec,
    ToolRegistry,
    application_state_dir,
)
from .tools import (
    Capability,
    SideEffect,
    ToolResult,
    ToolSpec,
    coerce_tool_spec,
    execute_tool,
    tool_idempotency_scope,
    upgrade_registry,
)
from .context_router import ContextPlan, ContextRouter, CompressionResult, compress_context, compress_text

__all__ = [
    "APPLICATIONS", "DEVELOPER_APP", "ApplicationSpec",
    "Capability", "ContextPlan", "ContextRouter", "CompressionResult", "compress_context", "compress_text",
    "SideEffect", "ToolRegistry", "ToolResult", "ToolSpec",
    "application_state_dir", "coerce_tool_spec", "execute_tool", "tool_idempotency_scope", "upgrade_registry",
]
