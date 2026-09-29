"""Manual end-to-end check of the realtime adapter against the live endpoint.

The unit tests in ``tests/test_realtime_provider.py`` drive a fake socket, so
they prove the contract but never prove the vendor still speaks it. This script
drives the real :class:`~agent_runtime.realtime_omni.OmniRealtimeProvider` with
a real key and reports which neutral event kinds actually come back.

Usage::

    set DOCMIND_OMNI_API_KEY=sk-...        (or rely on DASHSCOPE_API_KEY in .env)
    .venv\\Scripts\\python.exe -B verify_realtime_live.py
    .venv\\Scripts\\python.exe -B verify_realtime_live.py --wav path/to/speech.wav

Exit code 0 means a full round trip was observed: user transcript, assistant
text deltas, assistant audio deltas and a terminal done.

Why this script exists — two vendor traps it has already caught:

* ``qwen-omni-turbo-realtime`` retired on 2026-10-10 and was half-disabled
  first: handshake fine, then the socket dropped ~2 s after audio with zero
  events. That looks exactly like "account not enabled" and is not.
* Voices are model-specific. ``Chelsie`` (the legacy default) is rejected by
  the current model with ``Voice 'Chelsie' is not supported``.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
import urllib.request
import wave

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SAMPLE_URL = ("https://raw.githubusercontent.com/ggml-org/whisper.cpp"
              "/master/samples/jfk.wav")
# Deliberately outside the repo so a downloaded sample never dirties git status.
SAMPLE_WAV = os.path.join(tempfile.gettempdir(), "docmind_jfk_sample.wav")


def _load_dotenv() -> None:
    """Fill missing keys from a local .env so the script runs without setup."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip(), value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def _ensure_wav(path: str) -> str:
    """Return a 16 kHz mono s16le wav, downloading the sample if needed."""
    if path and os.path.exists(path):
        return path
    if not os.path.exists(SAMPLE_WAV):
        print(f"downloading sample speech -> {SAMPLE_WAV}")
        try:
            urllib.request.urlretrieve(SAMPLE_URL, SAMPLE_WAV)
        except Exception as exc:
            raise SystemExit(
                f"could not download the sample clip ({type(exc).__name__}). "
                f"Pass --wav <16kHz-mono-s16le.wav> instead, e.g. a recording "
                f"of your own voice.") from exc
    return SAMPLE_WAV


def _describe(path: str) -> tuple[int, int, int]:
    with wave.open(path, "rb") as w:
        return w.getnchannels(), w.getsampwidth(), w.getframerate()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wav", default="", help="16 kHz mono s16le wav of speech")
    parser.add_argument("--seconds", type=float, default=11.0,
                        help="how much of the clip to stream (default 11)")
    parser.add_argument("--wait", type=float, default=20.0,
                        help="how long to wait for the response (default 20)")
    parser.add_argument("--tail", type=float, default=1.0,
                        help="silence pushed after the clip so the server VAD "
                             "closes the turn (default 1.0)")
    parser.add_argument("--keep-sample", action="store_true",
                        help="do not delete the downloaded sample afterwards")
    args = parser.parse_args()
    _load_dotenv()

    wav = _ensure_wav(args.wav)
    ch, width, rate = _describe(wav)
    if width != 2 or rate != 16000 or ch != 1:
        print(f"WARNING: expected 16 kHz mono s16le, got {rate} Hz / {ch}ch / "
              f"{width * 8}-bit — the server wants pcm16 @16 kHz mono")

    from agent_runtime import realtime_provider as rp
    from agent_runtime.realtime_omni import OmniRealtimeProvider

    provider = OmniRealtimeProvider(session_id="live-check", project_id="prj-live-check")
    state = provider.availability()
    print(f"model      : {state.get('model')}")
    print(f"voice      : {state.get('voice')}")
    print(f"capabilities: {provider.capabilities()}")
    if not state.get("ok"):
        print(f"unavailable: {state.get('reason')}")
        return 2
    if not provider.start():
        event = provider.poll(timeout=2)
        print(f"start failed: {event.payload if event else 'no event'}")
        return 2

    with wave.open(wav, "rb") as w:
        pcm = w.readframes(w.getnframes())
    step = 3200  # 100 ms of 16 kHz s16le
    total = min(int(args.seconds * 10), len(pcm) // step)
    print(f"streaming {total} chunks ({total * 0.1:.1f}s)...")

    try:
        for i in range(total):
            provider.send_audio(pcm[i * step:(i + 1) * step],
                                captured_at=int(time.time() * 1000))
            time.sleep(0.1)
        # Trailing silence is mandatory. The server VAD closes a turn on the
        # quiet after the last word, so cutting the stream while the clip is
        # still loud leaves the turn open: you get a full transcript, no answer,
        # and a stream_broken ~8 s later. Real microphones keep running after
        # the speaker stops, which is what normally supplies this tail.
        for _ in range(int(args.tail * 10)):
            provider.send_audio(b"\x00" * step, captured_at=int(time.time() * 1000))
            time.sleep(0.1)
        deadline = time.time() + args.wait
        kinds: list[str] = []
        samples: dict[str, str] = {}
        while time.time() < deadline:
            event = provider.poll(timeout=0.5)
            if event is None:
                if kinds and kinds[-1] == rp.EVENT_DONE:
                    break
                continue
            kinds.append(event.kind)
            samples.setdefault(event.kind, str(
                {k: v for k, v in event.payload.items() if k != "audio"})[:150])
    finally:
        provider.close()
        if not args.keep_sample and not args.wav and os.path.exists(SAMPLE_WAV):
            os.remove(SAMPLE_WAV)

    counts: dict[str, int] = {}
    for kind in kinds:
        counts[kind] = counts.get(kind, 0) + 1
    print(f"\nevents: {len(kinds)}")
    for kind, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5d}  {kind}")
    print("\nfirst sample per kind:")
    for kind, text in samples.items():
        print(f"  {kind}: {text}")

    missing = [k for k in (rp.EVENT_TRANSCRIPT, rp.EVENT_TEXT_DELTA,
                           rp.EVENT_AUDIO_DELTA, rp.EVENT_DONE) if k not in counts]
    if missing:
        print(f"\nRESULT: PARTIAL — missing {missing}")
        if rp.EVENT_DONE in missing and rp.EVENT_TRANSCRIPT in counts:
            print("Hint: transcript arrived but no answer -> the turn was never "
                  "closed. Raise --tail (silence after the clip); see "
                  "docs/realtime-r12-acceptance-*.md finding 1.")
        print("Hint: if audio was followed by an immediate disconnect, check the "
              "model has not been retired and that the voice is supported by it.")
        return 1
    print("\nRESULT: PASS — full round trip")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
