"""Application boundaries shared by every Agent-facing product surface.

The application id is a security boundary, not a prompt hint. A runtime can
only resolve tools registered for its application and its state lives under a
separate namespace.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


DEVELOPER_APP = "developer"


@dataclass(frozen=True)
class ApplicationSpec:
    application_id: str
    title: str
    state_namespace: str
    tool_names: frozenset[str]


APPLICATIONS = {
    DEVELOPER_APP: ApplicationSpec(
        DEVELOPER_APP, "开发工作台", "developer", frozenset()
    ),
}


class ToolRegistry:
    """Immutable application-scoped view over callable tool metadata."""

    def __init__(self, application_id: str, tools: Mapping[str, Mapping[str, Any]]):
        if application_id not in APPLICATIONS:
            raise ValueError("unknown application")
        self.application_id = application_id
        self._tools = dict(tools)

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def contains(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Mapping[str, Any]:
        if name not in self._tools:
            raise KeyError("tool is not available in this application")
        return self._tools[name]

    def as_dict(self) -> dict[str, Mapping[str, Any]]:
        return dict(self._tools)


def application_state_dir(state_root: str, application_id: str) -> Path:
    if application_id not in APPLICATIONS:
        raise ValueError("unknown application")
    namespace = APPLICATIONS[application_id].state_namespace
    return Path(state_root) / ".docmind" / "applications" / namespace
