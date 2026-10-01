"""Shared per-task token budget for the main agent and workflow helpers."""
from __future__ import annotations

import threading
from typing import Any, Mapping, Sequence


def _usage_value(usage: Mapping[str, Any] | None, keys: Sequence[str]) -> int:
    if not isinstance(usage, Mapping):
        return 0
    for key in keys:
        value = usage.get(key)
        if value is not None:
            try:
                return max(0, int(value))
            except (TypeError, ValueError):
                continue
    return 0


class RunBudget:
    """One budget shared by every model call belonging to a top-level task.

    The budget is deliberately independent from provider billing.  It is a
    local safety rail that also works for local models and providers without
    reliable cost data.
    """

    def __init__(self, token_limit: int = 0):
        self.token_limit = max(0, int(token_limit or 0))
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.calls = 0
        self.estimated_calls = 0
        self.exhausted = False
        self._lock = threading.RLock()

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def remaining(self) -> int | None:
        if self.token_limit <= 0:
            return None
        return max(0, self.token_limit - self.total_tokens)

    def preflight(self, prompt_chars: int = 0, output_tokens: int = 0) -> bool:
        """Return whether a call is allowed before it is sent to the model."""
        with self._lock:
            if self.token_limit <= 0:
                return True
            # Four characters/token is conservative for mixed Chinese/code.
            estimated_prompt = max(1, int(max(0, prompt_chars) / 4))
            projected = self.total_tokens + estimated_prompt + max(0, int(output_tokens or 0))
            if projected > self.token_limit:
                self.exhausted = True
                return False
            self.estimated_calls += 1
            return True

    def record(self, usage: Mapping[str, Any] | None = None,
               *, prompt_chars: int = 0, output_chars: int = 0) -> dict[str, int | bool | None]:
        """Record provider usage, falling back to a conservative text estimate."""
        prompt = _usage_value(usage, ("prompt_tokens", "input_tokens", "prompt_eval_count", "in"))
        completion = _usage_value(usage, ("completion_tokens", "output_tokens", "eval_count", "out"))
        if not prompt:
            prompt = max(0, int(max(0, prompt_chars) / 4))
        if not completion:
            completion = max(0, int(max(0, output_chars) / 4))
        with self._lock:
            self.prompt_tokens += prompt
            self.completion_tokens += completion
            self.calls += 1
            if self.token_limit > 0 and self.total_tokens >= self.token_limit:
                self.exhausted = True
            return self.snapshot()

    def snapshot(self) -> dict[str, int | bool | None]:
        with self._lock:
            return {
                "limit": self.token_limit,
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
                "remaining": self.remaining,
                "calls": self.calls,
                "exhausted": bool(self.exhausted),
            }


__all__ = ["RunBudget"]
