"""Conservative scheduling policy for local LLM providers.

Local OpenAI-compatible servers usually share one loaded model and one GPU/CPU
memory pool.  Creating several cloned clients does not create useful capacity;
it mostly increases queueing, context memory, and the chance of timeout/OOM.
This module keeps the policy in one place so orchestration and delegation make
the same decision.
"""
from __future__ import annotations

import os
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass
from threading import BoundedSemaphore, Lock


@dataclass(frozen=True)
class LocalResourceProfile:
    provider: str
    model: str
    local: bool
    tier: str
    max_parallel: int
    max_subagents: int
    recommended_roles: tuple[str, ...]
    reason: str

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "model": self.model,
            "local": self.local,
            "tier": self.tier,
            "max_parallel": self.max_parallel,
            "max_subagents": self.max_subagents,
            "recommended_roles": list(self.recommended_roles),
            "reason": self.reason,
        }


_LOCAL_PROVIDERS = {"ollama", "llamacpp"}
_SEMAPHORES: dict[tuple[str, str, int], BoundedSemaphore] = {}
_SEMAPHORE_LOCK = Lock()
_HELD_SLOTS: contextvars.ContextVar[set[tuple[str, str, int]]] = contextvars.ContextVar(
    "docmind_held_local_slots", default=set())


def _env_int(name: str, default: int, *, minimum: int = 1, maximum: int = 16) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _tier(model: str) -> str:
    name = (model or "").lower()
    if any(hint in name for hint in ("35b", "35b-a3b", "36b")):
        return "large"
    if any(hint in name for hint in ("14b", "13b", "16b")):
        return "medium"
    if any(hint in name for hint in ("7b", "8b", "9b", "deepseek-r1")):
        return "small"
    if any(hint in name for hint in ("1b", "2b", "3b", "4b")):
        return "tiny"
    return "unknown"


def resource_profile(provider: str, model: str) -> LocalResourceProfile:
    """Return a conservative model resource profile.

    ``DOCMIND_LOCAL_LLM_MAX_CONCURRENCY`` is an explicit operator override for
    the number of simultaneous local model generations.  The default remains
    one even for a large MoE model: active parameters do not imply that several
    full contexts fit in memory.  ``DOCMIND_LOCAL_SUBAGENT_MAX`` is a separate
    hard cap on the number of planned child tasks; it does not increase
    simultaneous generations, which remain controlled by the concurrency cap.
    """
    p, m = (provider or "").strip().lower(), (model or "").strip()
    if p not in _LOCAL_PROVIDERS:
        return LocalResourceProfile(
            p, m, False, "cloud", 16, 16,
            ("researcher", "coder", "reviewer", "tester"),
            "非本地 provider，不应用本地推理槽位限制",
        )

    tier = _tier(m)
    # ``max_parallel`` is the memory/concurrency guard.  ``max_subagents``
    # is the total number of bounded DAG tasks, which may still run one after
    # another.  A tiny model therefore keeps one active generation but can
    # complete a small design -> implementation -> verification workflow.
    defaults = {
        "tiny": (1, 3, ("dispatcher", "designer", "coder", "tester", "reviewer")),
        "small": (1, 4, ("researcher", "coder", "tester", "reviewer")),
        "medium": (1, 4, ("researcher", "coder", "reviewer", "tester")),
        "large": (1, 4, ("dispatcher", "designer", "coder", "reviewer", "tester")),
        "unknown": (1, 3, ("dispatcher", "designer", "tester", "reviewer")),
    }
    default_parallel, default_subagents, roles = defaults[tier]
    parallel = _env_int("DOCMIND_LOCAL_LLM_MAX_CONCURRENCY", default_parallel)
    subagents = _env_int("DOCMIND_LOCAL_SUBAGENT_MAX", default_subagents)
    # A subagent cap should never silently exceed the selected model tier's
    # conservative default; operators can raise it explicitly with the tier's
    # concurrency override when they have measured headroom.
    subagents = min(subagents, max(1, default_subagents))
    return LocalResourceProfile(
        p, m, True, tier, parallel, subagents, roles,
        "本地模型共享同一推理服务，默认串行生成以避免显存/上下文争抢",
    )


def effective_parallelism(provider: str, model: str, requested: int | None) -> int:
    """Clamp an orchestration request to the provider/model resource profile."""
    requested_value = max(1, int(requested or 1))
    profile = resource_profile(provider, model)
    return min(requested_value, profile.max_parallel) if profile.local else requested_value


def effective_subagent_limit(provider: str, model: str, requested: int | None) -> int:
    requested_value = max(1, int(requested or 1))
    profile = resource_profile(provider, model)
    return min(requested_value, profile.max_subagents) if profile.local else requested_value


@contextmanager
def local_llm_slot(provider: str, model: str):
    """Serialize local generations even when callers use independent clients."""
    profile = resource_profile(provider, model)
    if not profile.local:
        yield
        return
    key = (profile.provider, profile.model, profile.max_parallel)
    held = _HELD_SLOTS.get()
    if key in held:
        # _run_child holds the slot around the whole child loop.  LLMClient
        # also uses this context manager for direct requests; make that nesting
        # re-entrant so the two protection layers cannot deadlock at capacity 1.
        yield
        return
    with _SEMAPHORE_LOCK:
        semaphore = _SEMAPHORES.setdefault(key, BoundedSemaphore(profile.max_parallel))
    semaphore.acquire()
    token = _HELD_SLOTS.set(set(held) | {key})
    try:
        yield
    finally:
        _HELD_SLOTS.reset(token)
        semaphore.release()


__all__ = [
    "LocalResourceProfile",
    "resource_profile",
    "effective_parallelism",
    "effective_subagent_limit",
    "local_llm_slot",
]
