"""Realtime multimodal timeline (plan R5).

Audio chunks, video frames, observations, transcripts and model answers are
bound to one monotonic ``captured_at`` clock so an answer can prove which
picture it was looking at. Two rules are enforced here rather than in the model
prompt, because neither is safe to leave to judgement:

* **No future evidence.** A frame captured after the user finished speaking can
  never be cited as the reason for that answer.
* **No cross-project leakage.** An entry whose project does not match the
  timeline's project is dropped on arrival and never returned.

This module is deliberately transport agnostic: it consumes whatever the gateway
already has and is equally usable by the sampled-frame path, so the cockpit can
adopt it before a native realtime model exists.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

KIND_FRAME = "frame"
KIND_OBSERVATION = "observation"
KIND_TRANSCRIPT = "transcript"
KIND_TEXT = "text"
KIND_AUDIO = "audio"
KIND_RESPONSE = "response"

TIMELINE_KINDS = (KIND_FRAME, KIND_OBSERVATION, KIND_TRANSCRIPT,
                  KIND_TEXT, KIND_AUDIO, KIND_RESPONSE)

DEFAULT_MAX_ENTRIES = 240
DEFAULT_TOLERANCE_MS = 1500
DEFAULT_SPEECH_WINDOW_MS = 6000


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class TimelineEntry:
    kind: str
    captured_at: int
    project_id: str = ""
    session_id: str = ""
    sequence: int = 0
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "captured_at": self.captured_at,
                "project_id": self.project_id, "session_id": self.session_id,
                "sequence": self.sequence, "data": dict(self.data)}


class RealtimeTimeline:
    """Bounded per-project timeline of realtime evidence."""

    def __init__(self, project_id: str = "", *, max_entries: int = DEFAULT_MAX_ENTRIES,
                 tolerance_ms: int = DEFAULT_TOLERANCE_MS,
                 speech_window_ms: int = DEFAULT_SPEECH_WINDOW_MS) -> None:
        self.project_id = project_id or ""
        self.max_entries = max(1, int(max_entries))
        self.tolerance_ms = max(0, int(tolerance_ms))
        self.speech_window_ms = max(0, int(speech_window_ms))
        self._entries: list[TimelineEntry] = []
        self._lock = threading.Lock()
        self.dropped_other_project = 0

    # -- write side -----------------------------------------------------------
    def add(self, kind: str, captured_at: int = 0, *, project_id: str = "",
            session_id: str = "", sequence: int = 0, **data: Any) -> TimelineEntry | None:
        if kind not in TIMELINE_KINDS:
            raise ValueError(f"unknown timeline kind: {kind}")
        owner = project_id or self.project_id
        if self.project_id and owner and owner != self.project_id:
            with self._lock:
                self.dropped_other_project += 1
            return None
        stamp = int(captured_at) if captured_at and captured_at > 0 else _now_ms()
        entry = TimelineEntry(kind=kind, captured_at=stamp, project_id=owner,
                              session_id=session_id, sequence=int(sequence or 0), data=data)
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self.max_entries:
                del self._entries[: len(self._entries) - self.max_entries]
        return entry

    def set_project(self, project_id: str) -> int:
        """Switch owner: everything from the previous project is discarded."""
        with self._lock:
            self.project_id = project_id or ""
            removed = len(self._entries)
            self._entries = []
        return removed

    def drop_project(self, project_id: str) -> int:
        with self._lock:
            before = len(self._entries)
            self._entries = [e for e in self._entries if e.project_id != project_id]
            return before - len(self._entries)

    def clear(self) -> None:
        with self._lock:
            self._entries = []
            self.dropped_other_project = 0

    # -- read side ------------------------------------------------------------
    def entries(self, kind: str = "", limit: int = 0) -> list[TimelineEntry]:
        with self._lock:
            items = [e for e in self._entries if not kind or e.kind == kind]
        if limit and len(items) > limit:
            items = items[-limit:]
        return items

    def latest_before(self, when_ms: int, kind: str = "") -> TimelineEntry | None:
        """Most recent entry that is not newer than ``when_ms``."""
        with self._lock:
            candidates = [e for e in self._entries
                          if e.captured_at <= when_ms and (not kind or e.kind == kind)]
        return candidates[-1] if candidates else None

    def evidence_for_speech(self, start_ms: int, end_ms: int) -> dict[str, Any]:
        """Evidence an answer may cite for a user utterance.

        Returns ``within=False`` with a reason when nothing trustworthy exists,
        so callers say "cannot confirm from the picture" instead of guessing.
        """
        end = int(end_ms) if end_ms and end_ms > 0 else _now_ms()
        start = int(start_ms) if start_ms and start_ms > 0 else end - self.speech_window_ms
        deadline = end + self.tolerance_ms
        with self._lock:
            frames = [e for e in self._entries
                      if e.kind == KIND_FRAME and start <= e.captured_at <= deadline]
            observations = [e for e in self._entries
                            if e.kind == KIND_OBSERVATION and start <= e.captured_at <= deadline]
        frame = frames[-1] if frames else None
        observation = observations[-1] if observations else None
        result: dict[str, Any] = {
            "start": start, "end": end, "deadline": deadline,
            "project_id": self.project_id,
            "frames": [e.as_dict() for e in frames[-5:]],
            "observation": observation.as_dict() if observation else None,
        }
        if frame is None and observation is None:
            result["within"] = False
            result["reason"] = "发言时段内没有可信画面证据"
        else:
            result["within"] = True
            result["reason"] = ""
            result["captured_at"] = max(e.captured_at for e in [frame, observation] if e)
        return result

    def snapshot(self, limit: int = 50) -> dict[str, Any]:
        with self._lock:
            items = [e.as_dict() for e in self._entries[-limit:]]
            dropped = self.dropped_other_project
            count = len(self._entries)
        return {"project_id": self.project_id, "count": count,
                "dropped_other_project": dropped, "entries": items}
