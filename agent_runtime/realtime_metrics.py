"""In-memory metrics for realtime cockpit sessions (task R10).

The registry keeps bounded ring-buffer histograms (latencies), monotonic
counters and point-in-time gauges. Percentiles use the nearest-rank method on
the retained window, so a p50/p95 report is available without an external
metrics server. All inputs are validated; malformed numbers are rejected
instead of contaminating the report.

Typical recording points in the gateway/provider layer::

    registry.observe(OBSERVATION_LATENCY_MS, delivered_at - captured_at, session_id)
    registry.incr(FRAMES_DROPPED, session_id=session_id)
    registry.gauge(QUEUE_DEPTH, pending_count, session_id)
"""
from __future__ import annotations

import math
import re
import threading
from typing import Any

DEFAULT_WINDOW = 1024
MAX_NAME_LENGTH = 60
MAX_SESSION_LENGTH = 160
_NAME_RE = re.compile(r"^[a-z][a-z0-9_.]{0,%d}$" % (MAX_NAME_LENGTH - 1))
# Session ids are client-supplied identifiers (often UUIDs), not metric names:
# hyphens are allowed, but the charset stays bounded and lowercase.
_SESSION_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,%d}$" % (MAX_SESSION_LENGTH - 1))

# Canonical metric names; recorders may also use their own dotted names.
FIRST_TOKEN_MS = "first_token_ms"
FIRST_AUDIO_MS = "first_audio_ms"
END_TO_END_MS = "end_to_end_ms"
OBSERVATION_LATENCY_MS = "observation_latency_ms"
QUEUE_DEPTH = "queue_depth"
FRAMES_SENT = "frames_sent"
FRAMES_DROPPED = "frames_dropped"
MODEL_REJECTIONS = "model_rejections"
RECONNECTS = "reconnects"
CONNECTIONS = "connections"


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not _NAME_RE.match(name):
        raise ValueError(f"invalid metric name: {name!r}")
    return name


def _check_session_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not _SESSION_RE.match(session_id):
        raise ValueError(f"invalid session id: {session_id!r}")
    return session_id


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"metric value must be a number, got {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("metric value must be finite")
    return number


class _Histogram:
    """Bounded ring buffer of observations with nearest-rank percentiles."""

    __slots__ = ("window", "samples", "total")

    def __init__(self, window: int = DEFAULT_WINDOW):
        self.window = max(2, int(window))
        self.samples: list[float] = []
        self.total = 0

    def observe(self, value: float) -> None:
        if len(self.samples) >= self.window:
            self.samples.pop(0)
        self.samples.append(value)
        self.total += 1

    def percentile(self, q: float) -> float | None:
        if not self.samples:
            return None
        if not 0 <= q <= 1:
            raise ValueError("percentile must be between 0 and 1")
        ordered = sorted(self.samples)
        index = max(0, math.ceil(q * len(ordered)) - 1)
        return ordered[index]

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": self.total,
            "window": len(self.samples),
            "min": min(self.samples) if self.samples else None,
            "max": max(self.samples) if self.samples else None,
            "p50": self.percentile(0.50),
            "p95": self.percentile(0.95),
        }


class _Counter:
    __slots__ = ("total",)

    def __init__(self) -> None:
        self.total = 0

    def incr(self, amount: float = 1) -> None:
        number = _finite_number(amount)
        if number < 0:
            raise ValueError("counter increment must be non-negative")
        self.total += number

    def snapshot(self) -> float:
        return self.total


class _Gauge:
    __slots__ = ("value",)

    def __init__(self) -> None:
        self.value: float | None = None

    def set(self, value: float) -> None:
        self.value = _finite_number(value)

    def snapshot(self) -> float | None:
        return self.value


class _Scope:
    """A metric namespace: the global scope or one session scope."""

    def __init__(self, window: int) -> None:
        self._window = window
        self.histograms: dict[str, _Histogram] = {}
        self.counters: dict[str, _Counter] = {}
        self.gauges: dict[str, _Gauge] = {}

    def _histogram(self, name: str) -> _Histogram:
        metric = self.histograms.get(name)
        if metric is None:
            metric = _Histogram(self._window)
            self.histograms[name] = metric
        return metric

    def observe(self, name: str, value: Any) -> None:
        _check_name(name)
        self._histogram(name).observe(_finite_number(value))

    def incr(self, name: str, amount: Any = 1) -> None:
        _check_name(name)
        counter = self.counters.get(name)
        if counter is None:
            counter = _Counter()
            self.counters[name] = counter
        counter.incr(amount)

    def gauge(self, name: str, value: Any) -> None:
        _check_name(name)
        point = self.gauges.get(name)
        if point is None:
            point = _Gauge()
            self.gauges[name] = point
        point.set(value)

    def snapshot(self) -> dict[str, Any]:
        return {
            "histograms": {name: metric.snapshot() for name, metric in sorted(self.histograms.items())},
            "counters": {name: counter.snapshot() for name, counter in sorted(self.counters.items())},
            "gauges": {name: gauge.snapshot() for name, gauge in sorted(self.gauges.items())},
        }


class MetricsRegistry:
    """Thread-safe process-wide registry with per-session metric scopes."""

    def __init__(self, window: int = DEFAULT_WINDOW) -> None:
        self._window = max(2, int(window))
        self._lock = threading.RLock()
        self._global = _Scope(self._window)
        self._sessions: dict[str, _Scope] = {}

    def _session_scope(self, session_id: str | None) -> _Scope | None:
        if not session_id:
            return None
        _check_session_id(session_id)
        scope = self._sessions.get(session_id)
        if scope is None:
            scope = _Scope(self._window)
            self._sessions[session_id] = scope
        return scope

    def observe(self, name: str, value: Any, session_id: str | None = None) -> None:
        """Record one latency/size sample, globally and for the session."""
        with self._lock:
            number = _finite_number(value)
            _check_name(name)
            self._global._histogram(name).observe(number)
            scope = self._session_scope(session_id)
            if scope is not None:
                scope._histogram(name).observe(number)

    def incr(self, name: str, amount: Any = 1, session_id: str | None = None) -> None:
        """Increase a monotonic counter, globally and for the session."""
        with self._lock:
            _check_name(name)
            number = _finite_number(amount)
            if number < 0:
                raise ValueError("counter increment must be non-negative")
            self._global.incr(name, number)
            scope = self._session_scope(session_id)
            if scope is not None:
                scope.incr(name, number)

    def gauge(self, name: str, value: Any, session_id: str | None = None) -> None:
        """Set a point-in-time gauge, globally and for the session."""
        with self._lock:
            number = _finite_number(value)
            _check_name(name)
            self._global.gauge(name, number)
            scope = self._session_scope(session_id)
            if scope is not None:
                scope.gauge(name, number)

    def drop_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def reset(self) -> None:
        with self._lock:
            self._global = _Scope(self._window)
            self._sessions.clear()

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe report of global metrics and every live session."""
        with self._lock:
            return {
                "global": self._global.snapshot(),
                "sessions": {sid: scope.snapshot() for sid, scope in sorted(self._sessions.items())},
            }
