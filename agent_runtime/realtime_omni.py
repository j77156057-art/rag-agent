"""DashScope Qwen-Omni Realtime adapter (plan R4, first backend).

Speaks the documented WebSocket protocol directly with ``websocket-client``
(already vendored) instead of the DashScope SDK, so the cockpit gains no new
dependency and no SDK version floor.

Verified against the official docs (2026-09):
  endpoint  wss://dashscope.aliyuncs.com/api-ws/v1/realtime?model=<model>
  auth      Authorization: Bearer <DASHSCOPE_API_KEY>
  models    qwen-omni-turbo-realtime (voice Chelsie) / qwen3.5-omni-plus-realtime
  client    session.update, input_audio_buffer.append|commit|clear,
            input_image_buffer.append, response.create
  server    session.created|updated, input_audio_buffer.committed, response.created,
            response.audio_transcript.delta|done, response.audio.delta|done,
            response.done, conversation.item.input_audio_transcription.completed
  audio     in PCM 16 kHz mono 16-bit, out PCM 24 kHz mono 16-bit

Live probe results (2026-09-29, real ``sk-ws-`` key, read-only):

* **Confirmed working** — connection and auth against the default endpoint; the
  server immediately emits ``session.created`` with ``input_audio_format:
  "pcm16"``, ``output_audio_format: "pcm24"``, ``turn_detection.type:
  server_vad`` (threshold 0.5), ``input_audio_transcription: {model:
  "gummy-realtime-v1"}``. Those wire values are what this module now sends.
* **Confirmed rejected** — ``input_audio_buffer.commit`` and ``response.cancel``
  each make the server drop the connection (10054). Manual mode is therefore
  unavailable on this endpoint; only server-VAD streaming works, so
  :meth:`commit` and :meth:`interrupt` degrade to the buffer-clear path.
* **Still unproven** — ``session.update`` returns no ``session.updated``, and a
  1 s tone triggered neither VAD nor any response event, so the exact session
  field spelling and the VAD trigger need a real human voice (plan R12).
"""
from __future__ import annotations

import base64
import json
import os
import threading
import time
import uuid
from typing import Any, Callable

from agent_runtime.realtime_provider import (
    CAP_AUDIO_IN, CAP_AUDIO_OUT, CAP_INTERRUPT, CAP_TEXT_OUT, CAP_VIDEO_IN,
    DEGRADED_SAMPLED_FRAMES, EVENT_AUDIO_DELTA, EVENT_DONE, EVENT_ERROR,
    EVENT_STATUS, EVENT_TEXT_DELTA, EVENT_TRANSCRIPT, RealtimeEvent,
    RealtimeProvider, register,
)

PROVIDER_NAME = "dashscope_omni"
DEFAULT_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
DEFAULT_MODEL = "qwen-omni-turbo-realtime"
DEFAULT_VOICE = "Chelsie"
# Wire values confirmed by the live session.created echo, not the SDK docs.
INPUT_AUDIO_FORMAT = "pcm16"
OUTPUT_AUDIO_FORMAT = "pcm24"
TRANSCRIPTION_MODEL = "gummy-realtime-v1"
# 1 MiB of PCM16 @16 kHz ≈ 32 s; anything larger is a caller bug, not a stream.
MAX_AUDIO_CHUNK = 1 << 20

_TRANSCRIPT_DONE = "conversation.item.input_audio_transcription.completed"


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


class OmniRealtimeProvider(RealtimeProvider):
    """Realtime session against DashScope Qwen-Omni."""

    name = PROVIDER_NAME

    def __init__(self, *, session_id: str = "", project_id: str = "",
                 ws_factory: Callable[[str, list[str]], Any] | None = None) -> None:
        super().__init__(session_id=session_id, project_id=project_id)
        self._ws_factory = ws_factory
        self._ws: Any = None
        self._thread: threading.Thread | None = None
        self._model = _env("DOCMIND_OMNI_MODEL", DEFAULT_MODEL)
        self._voice = _env("DOCMIND_OMNI_VOICE", DEFAULT_VOICE)
        self._url = _env("DOCMIND_OMNI_URL", DEFAULT_URL)
        self._vad = _env("DOCMIND_OMNI_VAD", "server_vad").lower()

    # -- configuration --------------------------------------------------------
    @property
    def api_key(self) -> str:
        return _env("DOCMIND_OMNI_API_KEY") or _env("DASHSCOPE_API_KEY")

    def endpoint(self) -> str:
        separator = "&" if "?" in self._url else "?"
        return f"{self._url}{separator}model={self._model}"

    def capabilities(self) -> list[str]:
        return [CAP_AUDIO_IN, CAP_VIDEO_IN, CAP_TEXT_OUT, CAP_AUDIO_OUT, CAP_INTERRUPT]

    def availability(self) -> dict[str, Any]:
        if not self.api_key:
            return {"ok": False, "provider": self.name,
                    "reason": "未配置 DashScope API Key（DOCMIND_OMNI_API_KEY / DASHSCOPE_API_KEY）",
                    "degraded_to": DEGRADED_SAMPLED_FRAMES}
        if self._ws_factory is None:
            try:
                import websocket  # noqa: F401  (presence probe only)
            except Exception:
                return {"ok": False, "provider": self.name,
                        "reason": "缺少 websocket-client 依赖", "degraded_to": DEGRADED_SAMPLED_FRAMES}
        return {"ok": True, "provider": self.name, "model": self._model,
                "voice": self._voice, "vad": self._vad, "verified": "connect-only",
                "note": "连接与 session.created 已真机验证；commit/cancel 被服务端断连，VAD 触发待真人声联验"}

    # -- lifecycle ------------------------------------------------------------
    def start(self) -> bool:
        state = self.availability()
        if not state.get("ok"):
            self._emit(EVENT_ERROR, code="unavailable",
                       message=str(state.get("reason") or "实时模型不可用"),
                       degraded_to=DEGRADED_SAMPLED_FRAMES)
            return False
        if self._running:
            return True
        try:
            headers = [f"Authorization: Bearer {self.api_key}"]
            if self._ws_factory is not None:
                self._ws = self._ws_factory(self.endpoint(), headers)
            else:
                import websocket
                self._ws = websocket.create_connection(self.endpoint(), header=headers, timeout=10)
        except Exception as exc:
            self._emit(EVENT_ERROR, code="connect_failed", message=f"{type(exc).__name__}",
                       retryable=True)
            return False
        self._running = True
        self._send(self._session_payload())
        self._thread = threading.Thread(target=self._recv_loop, name="omni-realtime", daemon=True)
        self._thread.start()
        return True

    def close(self) -> None:
        self._running = False
        ws, self._ws = self._ws, None
        if ws is not None:
            try:
                ws.close()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)

    # -- media input ----------------------------------------------------------
    def send_audio(self, pcm: bytes, captured_at: int = 0) -> bool:
        if not self._ready():
            return False
        if not pcm:
            return True
        if len(pcm) > MAX_AUDIO_CHUNK:
            self._emit(EVENT_ERROR, code="audio_too_large", message="音频分片超过 1MiB",
                       chars=len(pcm))
            return False
        return self._send(self._event("input_audio_buffer.append",
                                      audio=base64.b64encode(pcm).decode("ascii")))

    def send_frame(self, image: bytes, captured_at: int = 0) -> bool:
        if not self._ready():
            return False
        if not image:
            return True
        # ``image`` field name follows the documented input_image_buffer.append
        # event; correct here in one place if the console disagrees.
        return self._send(self._event("input_image_buffer.append",
                                      image=base64.b64encode(image).decode("ascii")))

    def commit(self) -> bool:
        """Manual mode is not available on this endpoint.

        A live probe (2026-09-29) showed ``input_audio_buffer.commit`` makes the
        server drop the connection, so this never reaches the wire: the session
        stays in server-VAD mode and the server decides the turn boundary.
        """
        self._emit(EVENT_ERROR, code="commit_unsupported",
                   message="该端点不支持手动提交（实测 input_audio_buffer.commit 会断连），请使用服务端 VAD 断句")
        return False

    def interrupt(self) -> bool:
        if not self._ready():
            return False
        # ``response.cancel`` is deliberately NOT sent: it also dropped the
        # connection in the live probe. Clearing the input buffer is the only
        # interrupt primitive this endpoint tolerated.
        return self._send(self._event("input_audio_buffer.clear"))

    # -- protocol payloads ----------------------------------------------------
    def _session_payload(self) -> dict[str, Any]:
        # Field names below are taken from the live ``session.created`` echo
        # (2026-09-29 real-key probe), not from the SDK docs: the wire format
        # strings are "pcm16" / "pcm24", transcription is an object, and a
        # payload with unknown fields is dropped silently by the server (no
        # session.updated and no error event).
        session: dict[str, Any] = {
            "modalities": ["text", "audio"],
            "voice": self._voice,
            "input_audio_format": INPUT_AUDIO_FORMAT,
            "output_audio_format": OUTPUT_AUDIO_FORMAT,
            "input_audio_transcription": {"model": TRANSCRIPTION_MODEL},
        }
        if self._vad in ("server_vad", "semantic_vad"):
            session["turn_detection"] = {"type": self._vad, "silence_duration_ms": 800}
        elif self._vad in ("", "none", "manual", "false"):
            session["turn_detection"] = None
        instructions = _env("DOCMIND_OMNI_INSTRUCTIONS")
        if instructions:
            session["instructions"] = instructions
        return self._event("session.update", session=session)

    @staticmethod
    def _event(event_type: str, **fields: Any) -> dict[str, Any]:
        payload = {"event_id": f"event_{uuid.uuid4().hex[:12]}", "type": event_type}
        payload.update(fields)
        return payload

    def _send(self, payload: dict[str, Any]) -> bool:
        if self._ws is None:
            return False
        try:
            self._ws.send(json.dumps(payload, ensure_ascii=False))
            return True
        except Exception as exc:
            self._emit(EVENT_ERROR, code="send_failed", message=f"{type(exc).__name__}",
                       retryable=True)
            return False

    def _ready(self) -> bool:
        if self._running and self._ws is not None:
            return True
        self._emit(EVENT_ERROR, code="not_started", message="实时会话尚未建立")
        return False

    # -- receive loop ---------------------------------------------------------
    def _recv_loop(self) -> None:
        while self._running and self._ws is not None:
            try:
                raw = self._ws.recv()
            except Exception:
                if self._running:
                    self._emit(EVENT_ERROR, code="stream_broken", retryable=True)
                self._running = False
                return
            if raw in (None, ""):
                continue
            try:
                message = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not isinstance(message, dict):
                continue
            event = self._translate(message)
            if event is not None:
                self._events.put(event)

    def _translate(self, message: dict[str, Any]) -> RealtimeEvent | None:
        """Map a vendor event onto the neutral vocabulary. Unknown → ignored."""
        event_type = str(message.get("type") or "")
        if event_type in ("session.created", "session.updated"):
            return RealtimeEvent(EVENT_STATUS, session_id=self.session_id,
                                 payload={"state": "ready", "provider": self.name,
                                          "model": self._model})
        if event_type == _TRANSCRIPT_DONE:
            return RealtimeEvent(EVENT_TRANSCRIPT, session_id=self.session_id,
                                 payload={"text": str(message.get("transcript") or ""),
                                          "role": "user"})
        if event_type == "response.audio_transcript.delta":
            return RealtimeEvent(EVENT_TEXT_DELTA, session_id=self.session_id,
                                 payload={"text": str(message.get("delta") or ""),
                                          "role": "assistant"})
        if event_type == "response.audio_transcript.done":
            return RealtimeEvent(EVENT_TEXT_DELTA, session_id=self.session_id,
                                 payload={"text": str(message.get("transcript") or ""),
                                          "role": "assistant", "final": True})
        if event_type == "response.audio.delta":
            delta = message.get("delta") or ""
            try:
                audio = base64.b64decode(delta) if delta else b""
            except (ValueError, TypeError):
                audio = b""
            return RealtimeEvent(EVENT_AUDIO_DELTA, session_id=self.session_id,
                                 payload={"audio": audio, "encoding": OUTPUT_AUDIO_FORMAT,
                                          "chars": len(audio)})
        if event_type == "response.done":
            return RealtimeEvent(EVENT_DONE, session_id=self.session_id,
                                 payload={"response_id": str(message.get("response_id") or ""),
                                          "reason": "completed"})
        if event_type == "error":
            return RealtimeEvent(EVENT_ERROR, session_id=self.session_id,
                                 payload={"code": str(message.get("code") or "vendor_error"),
                                          "message": str(message.get("message") or "")[:400]})
        return None


register(PROVIDER_NAME, OmniRealtimeProvider)
