"""Application-independent tool contracts and exception isolation.

The runtime accepts legacy ``{"description": ..., "func": ...}`` entries, but
normalizes them to :class:`ToolSpec` before dispatch.  This keeps the existing
tool implementations working while making authority, side effects and schemas
machine-readable instead of scattering name lists through the Agent loop.
"""
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Callable


class Capability(str, Enum):
    READ_LOCAL = "read_local"
    READ_EXTERNAL = "read_external"
    WRITE_LOCAL = "write_local"
    WRITE_EXTERNAL = "write_external"
    EXEC = "exec"
    NETWORK = "network"
    ADMIN = "admin"


class SideEffect(str, Enum):
    PURE = "pure"
    IDEMPOTENT_WRITE = "idempotent_write"
    MUTATING = "mutating"
    IRREVERSIBLE = "irreversible"


def _legacy_input_schema(required: bool = True) -> dict[str, Any]:
    schema = {
        "type": "object",
        "properties": {
            "input": {
                "type": "string",
                "description": (
                    "工具输入。可以是纯文本，也可以是多行 key: value；具体字段见工具说明。"
                ),
            }
        },
    }
    schema["required"] = ["input"] if required else []
    return schema


@dataclass(frozen=True)
class ToolSpec(Mapping[str, Any]):
    """Typed tool metadata with a mapping-compatible legacy view."""

    name: str
    description: str
    func: Callable
    input_schema: Mapping[str, Any] = field(default_factory=_legacy_input_schema)
    capability: Capability = Capability.READ_LOCAL
    side_effect: SideEffect = SideEffect.PURE
    parallel_safe: bool = True
    applications: frozenset[str] = field(default_factory=lambda: frozenset({"developer"}))
    group: str = "general"
    verbatim: bool = False

    @property
    def capability_type(self) -> Capability:
        return self.capability

    @property
    def side_effect_level(self) -> SideEffect:
        return self.side_effect

    @property
    def parallel_safety(self) -> bool:
        return self.parallel_safe

    @property
    def owner_app(self) -> frozenset[str]:
        return self.applications

    def __getitem__(self, key: str) -> Any:
        aliases = {
            "schema": self.input_schema,
            "input_schema": self.input_schema,
            "description": self.description,
            "func": self.func,
            "capability": self.capability,
            "capability_type": self.capability,
            "side_effect": self.side_effect,
            "side_effect_level": self.side_effect,
            "parallel_safe": self.parallel_safe,
            "parallel_safety": self.parallel_safe,
            "applications": self.applications,
            "application": self.applications,
            "owner_app": self.applications,
            "group": self.group,
            "verbatim": self.verbatim,
        }
        if key not in aliases:
            raise KeyError(key)
        return aliases[key]

    def __iter__(self) -> Iterator[str]:
        return iter(("description", "func", "schema", "input_schema", "capability",
                     "capability_type", "side_effect", "side_effect_level",
                     "parallel_safe", "parallel_safety", "applications", "application",
                     "owner_app", "group", "verbatim"))

    def __len__(self) -> int:
        return 15


_NETWORK_TOOLS = {
    "web_search", "web_fetch", "web_research", "web_subtitles",
    "dev_http_request", "dev_mcp_call", "dev_list_connector_tools",
    "dev_mcp_probe", "dev_mcp_discover",
}
_EXEC_TOOLS = {"python_exec", "run_command", "game_playtest", "self_verify"}
_ADMIN_TOOLS = {
    "dev_approve", "dev_commit", "dev_commit_all", "dev_rollback_changeset",
    "dev_apply_regions", "dev_add_region", "init_regions", "dev_install_tool",
    "dev_mcp_add", "dev_mcp_decide", "dev_mcp_remove",
    "start_workflow",
}
_WRITE_TOOLS = {
    "apply_edit", "create_file", "create_artifact", "dev_region_edit",
    "dev_refactor", "dev_rebuild_index", "dev_asset_register", "dev_capture_bug",
    "dev_update_bug", "game_upsert_task",
}
_IRREVERSIBLE_TOOLS = {"run_command", "game_playtest", "dev_mcp_call"}
_NO_ARG_TOOLS = {
    "dev_list_connectors", "dev_list_regions", "dev_verify_contracts",
    "dev_rebuild_index", "dev_list_changesets", "dev_list_bugs", "init_regions",
}
_VERBATIM_TOOLS = {"python_exec", "gen_video_prompt"}

_SCHEMA_OVERRIDES: dict[str, dict[str, Any]] = {
    "search_knowledge": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    "search_code": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    "web_search": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    "read_file": {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start": {"type": "integer", "minimum": 1},
            "end": {"type": "integer", "minimum": 1},
        },
        "required": ["path"],
    },
    "grep": {
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
        "required": ["pattern"],
    },
    "list_dir": {
        "type": "object",
        "properties": {"path": {"type": "string", "default": "."}},
        "required": [],
    },
    "calculate": {
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    },
    "dev_use_skill": {
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
    },
    "tool_search": {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
}


def _infer_group(name: str) -> str:
    if name.startswith("web_"):
        return "web"
    if name in {"search_knowledge"}:
        return "knowledge"
    if name in {"search_code", "read_file", "grep", "list_dir"}:
        return "code"
    if "asset" in name or name.startswith("gen_"):
        return "assets"
    if name.startswith("game_"):
        return "game"
    if name.startswith("dev_") or name in {
        "apply_edit", "create_file", "create_artifact", "run_command", "self_verify",
    }:
        return "developer"
    if name in {"delegate", "orchestrate"}:
        return "orchestration"
    return "general"


def coerce_tool_spec(name: str, value: Any) -> ToolSpec:
    """Convert a legacy mapping to a typed spec without changing its function."""
    if isinstance(value, ToolSpec):
        return value
    if not isinstance(value, Mapping) or not callable(value.get("func")):
        raise TypeError("invalid tool registration: %s" % name)
    capability = Capability.READ_LOCAL
    side_effect = SideEffect.PURE
    parallel_safe = True
    if name in _NETWORK_TOOLS or name.startswith("web_"):
        capability = Capability.NETWORK
    if name in _EXEC_TOOLS:
        capability = Capability.EXEC
        side_effect = SideEffect.MUTATING
        parallel_safe = False
    if name in _WRITE_TOOLS:
        capability = Capability.WRITE_LOCAL
        side_effect = SideEffect.MUTATING
        parallel_safe = False
    if name in _ADMIN_TOOLS:
        capability = Capability.ADMIN
        side_effect = SideEffect.MUTATING
        parallel_safe = False
    if name in _IRREVERSIBLE_TOOLS:
        side_effect = SideEffect.IRREVERSIBLE
        parallel_safe = False
    if name in {"orchestrate"}:
        parallel_safe = False
    schema = value.get("input_schema") or value.get("schema") or _SCHEMA_OVERRIDES.get(name)
    if schema is None:
        schema = _legacy_input_schema(required=name not in _NO_ARG_TOOLS)
    apps = value.get("applications") or {"developer"}
    return ToolSpec(
        name=name,
        description=str(value.get("description") or ""),
        func=value["func"],
        input_schema=dict(schema),
        capability=Capability(value.get("capability", capability)),
        side_effect=SideEffect(value.get("side_effect", side_effect)),
        parallel_safe=bool(value.get("parallel_safe", parallel_safe)),
        applications=frozenset(apps),
        group=str(value.get("group") or _infer_group(name)),
        verbatim=bool(value.get("verbatim", name in _VERBATIM_TOOLS)),
    )


def upgrade_registry(registry: dict[str, Any]) -> dict[str, ToolSpec]:
    for name, value in list(registry.items()):
        registry[name] = coerce_tool_spec(name, value)
    return registry


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    text: str
    data: Any = None
    error_kind: str = ""
    artifacts: dict = field(default_factory=dict)
    idempotency_key: str = ""
    replayed: bool = False


_IDEMPOTENCY_SCOPE: ContextVar[tuple[str, str] | None] = ContextVar(
    "tool_idempotency_scope", default=None)
_IDEMPOTENCY_LOCK = threading.RLock()


@contextmanager
def tool_idempotency_scope(namespace: str, storage_dir: str | Path):
    """Attach durable exactly-once protection to mutating tool calls.

    The scope is deliberately explicit and task-sized.  Read-only tools are
    never cached.  A process crash after a side effect but before a graph node
    checkpoint therefore leaves a ``running`` ledger row; recovery blocks the
    ambiguous replay instead of executing the mutation a second time.
    """
    root = Path(storage_dir).resolve()
    token = _IDEMPOTENCY_SCOPE.set((str(namespace), str(root / "tool_calls.sqlite")))
    try:
        yield
    finally:
        _IDEMPOTENCY_SCOPE.reset(token)


def _idempotency_key(namespace: str, tool_name: str, argument: str) -> str:
    payload = json.dumps(
        {"namespace": namespace, "tool": tool_name, "argument": argument},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _idempotency_claim(tool_name: str, argument: str) -> tuple[str, str, ToolResult | None]:
    scope = _IDEMPOTENCY_SCOPE.get()
    if not scope or not tool_name:
        return "", "", None
    namespace, db_path = scope
    key = _idempotency_key(namespace, tool_name, argument)
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    with _IDEMPOTENCY_LOCK, closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tool_calls ("
            "idempotency_key TEXT PRIMARY KEY, namespace TEXT NOT NULL, "
            "tool_name TEXT NOT NULL, argument_hash TEXT NOT NULL, status TEXT NOT NULL, "
            "ok INTEGER, text TEXT, error_kind TEXT, updated_at REAL NOT NULL)"
        )
        row = conn.execute(
            "SELECT status, ok, text, error_kind FROM tool_calls WHERE idempotency_key = ?",
            (key,),
        ).fetchone()
        if row:
            status, ok, text, error_kind = row
            if status == "completed":
                return key, db_path, ToolResult(
                    bool(ok), str(text or ""), error_kind=str(error_kind or ""),
                    idempotency_key=key, replayed=True,
                )
            return key, db_path, ToolResult(
                False,
                "工具调用已在先前执行中开始，但完成状态未知；为避免重复副作用，本次未重放。",
                error_kind="idempotency_in_doubt", idempotency_key=key, replayed=True,
            )
        conn.execute(
            "INSERT INTO tool_calls VALUES (?, ?, ?, ?, 'running', NULL, NULL, NULL, ?)",
            (key, namespace, tool_name,
             hashlib.sha256(argument.encode("utf-8")).hexdigest(), time.time()),
        )
        conn.commit()
    return key, db_path, None


def _idempotency_complete(db_path: str, key: str, result: ToolResult) -> None:
    if not db_path or not key:
        return
    with _IDEMPOTENCY_LOCK, closing(sqlite3.connect(db_path, timeout=30)) as conn:
        conn.execute(
            "UPDATE tool_calls SET status = 'completed', ok = ?, text = ?, "
            "error_kind = ?, updated_at = ? WHERE idempotency_key = ?",
            (int(result.ok), result.text, result.error_kind, time.time(), key),
        )
        conn.commit()


def execute_tool(function: Callable, argument: str, legacy_failure: Callable, *,
                 tool_name: str = "", side_effect: SideEffect = SideEffect.PURE) -> ToolResult:
    key = db_path = ""
    if side_effect != SideEffect.PURE:
        try:
            key, db_path, replay = _idempotency_claim(tool_name, argument)
        except Exception:
            # Never execute a mutating/irreversible tool without its durable
            # claim.  A transient filesystem/SQLite failure is safer as a
            # blocked tool result than as an untracked side effect.
            return ToolResult(False, "幂等保护不可用，工具未执行。",
                              error_kind="idempotency_unavailable")
        if replay is not None:
            return replay
    try:
        value = function(argument)
        if isinstance(value, ToolResult):
            result = value
        elif not isinstance(value, str):
            result = ToolResult(False, "工具执行失败：返回值不符合工具协议。", error_kind="invalid_result")
        else:
            ok = not legacy_failure(value)
            result = ToolResult(ok, value, error_kind="" if ok else "legacy_failure")
    except Exception as exc:
        # Exception messages may contain credentials or arbitrary external data.
        exc_name = type(exc).__name__
        exc_text = str(exc).lower()
        reason = getattr(exc, "reason", None)
        reason_name = type(reason).__name__.lower() if reason is not None else ""
        is_timeout = (isinstance(exc, TimeoutError) or "timeout" in exc_name.lower()
                      or "timed out" in exc_text or "timeout" in reason_name)
        result = ToolResult(False, "工具执行失败（%s）。" % exc_name,
                            error_kind="timeout" if is_timeout else "exception")
    if key:
        result = ToolResult(result.ok, result.text, data=result.data,
                            error_kind=result.error_kind, artifacts=result.artifacts,
                            idempotency_key=key, replayed=False)
        try:
            _idempotency_complete(db_path, key, result)
        except Exception:
            # Keep the side-effect result observable, but leave the claim in
            # ``running`` so a later recovery refuses an ambiguous replay.
            pass
    return result
