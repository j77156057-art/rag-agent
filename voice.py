"""Optional speech bridge for the harness.

The browser path is the zero setup fallback. When a compatible STT/TTS endpoint
is configured, these helpers proxy audio without making the main Agent depend on
one vendor SDK.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid


def _cfg(prefix: str):
    return (
        os.getenv(f"DOCMIND_{prefix}_URL", "").strip(),
        os.getenv(f"DOCMIND_{prefix}_API_KEY", "").strip(),
        os.getenv(f"DOCMIND_{prefix}_MODEL", "").strip(),
    )


def configured(kind: str) -> bool:
    url, _key, _model = _cfg(kind.upper())
    # 本地 Ollama/Whisper/TTS 兼容服务通常不需要 API Key；云端服务再由
    # 调用方通过 DOCMIND_*_API_KEY 提供 Bearer 凭证。
    return bool(url)


def transcribe(audio: bytes, filename: str = "audio.webm", language: str = "") -> dict:
    url, key, model = _cfg("STT")
    if not url:
        return {"ok": False, "fallback": "browser", "error": "未配置服务端语音识别模型，可使用浏览器语音输入。"}
    boundary = "----DocMindVoice" + uuid.uuid4().hex
    fields = [("model", model or "whisper-1"), ("language", language)]
    body = bytearray()
    for name, value in fields:
        if value:
            body.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n").encode())
    body.extend((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\nContent-Type: audio/webm\r\n\r\n").encode())
    body.extend(audio)
    body.extend((f"\r\n--{boundary}--\r\n").encode())
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=bytes(body), method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8", "replace"))
    return {"ok": True, "text": str(payload.get("text") or payload.get("transcript") or "")}


def speech_request(text: str, voice: str = "") -> tuple[bytes, str]:
    url, key, model = _cfg("TTS")
    if not url:
        raise RuntimeError("未配置服务端语音合成模型")
    payload = json.dumps({"model": model or "tts-1", "input": text,
                          "voice": voice or os.getenv("DOCMIND_TTS_VOICE", "alloy"),
                          "response_format": "mp3"}).encode()
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read(), response.headers.get_content_type() or "audio/mpeg"
