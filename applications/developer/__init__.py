"""Compatibility boundary for the existing developer/game workbench."""
from agent_runtime import DEVELOPER_APP, ToolRegistry


def developer_tool_registry(tools):
    """Bind the legacy tool mapping to the developer application boundary."""
    return ToolRegistry(DEVELOPER_APP, tools)


__all__ = ["developer_tool_registry"]

