"""R12 acceptance run for the realtime path (provider-side evidence).

R12 asks for five measurements against a real model: first response, sustained
response, interrupt response, reconnect recovery and error rate. This drives
the real :class:`~agent_runtime.realtime_omni.OmniRealtimeProvider` end to end
and writes a dated acceptance report under ``docs/``.

Scope note for the other agents: this covers the **R4 provider** side of R12.
Device capture (camera / microphone / screen share) and the cockpit UX pass
belong to the R12 owner; nothing here touches the gateway or the frontend.

Usage::

    .venv\\Scripts\\python.exe -B verify_realtime_acceptance.py --wav speech.wav
    .venv\\Scripts\\python.exe -B verify_realtime_acceptance.py --quick
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import wave
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from verify_realtime_live import _ensure_wav, _load_dotenv  # noqa: E402

from agent_runtime import realtime_provider as rp  # noqa: E402
from agent_runtime.realtime_omni import OmniRealtimeProvider  # noqa: E402

STEP = 3200  # 100 ms of 16 kHz s16le
REPORT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")


@dataclass
class Turn:
    """One speak -> answer round."""

    label: str
    speak_ms: int = 0
    speech_stopped_ms: int = 0
    first_text_ms: int = 0
    first_audio_ms: int = 0
    text_deltas: int = 0
    audio_deltas: int = 0
    transcript: str = ""
    saw_done: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def e2e_ms(self) -> int:
        """Speak end -> first assistant output, includes server VAD silence."""
        first = min(x for x in (self.first_text_ms, self.first_audio_ms) if x > 0) \
            if (self.first_text_ms or self.first_audio_ms) else 0
        return first - self.speak_ms if first else 0

    @property
    def model_ms(self) -> int:
        """Server-detected speech end -> first assistant output."""
        first = min(x for x in (self.first_text_ms, self.first_audio_ms) if x > 0) \
            if (self.first_text_ms or self.first_audio_ms) else 0
        return first - self.speech_stopped_ms if first and self.speech_stopped_ms else 0


def _stream(provider: OmniRealtimeProvider, pcm: bytes, seconds: float,
            sink: Turn, stop_after_deltas: int = 0,
            interrupt_after_deltas: int = 0) -> int:
    """Send `seconds` of audio, then keep collecting. Returns deltas seen."""
    total = min(int(seconds * 10), len(pcm) // STEP)
    for i in range(total):
        provider.send_audio(pcm[i * STEP:(i + 1) * STEP],
                            captured_at=int(time.time() * 1000))
        time.sleep(0.1)
    sink.speak_ms = int(time.time() * 1000)
    return _collect(provider, sink, seconds=18.0,
                    stop_after_deltas=stop_after_deltas,
                    interrupt_after_deltas=interrupt_after_deltas)


def _collect(provider: OmniRealtimeProvider, sink: Turn, seconds: float,
             stop_after_deltas: int = 0, interrupt_after_deltas: int = 0) -> int:
    deadline = time.time() + seconds
    deltas = 0
    interrupted = False
    while time.time() < deadline:
        event = provider.poll(timeout=0.3)
        if event is None:
            continue
        kind, payload = event.kind, event.payload
        if kind == rp.EVENT_STATUS:
            if payload.get("state") == "thinking" and not sink.speech_stopped_ms:
                sink.speech_stopped_ms = event.captured_at
        elif kind == rp.EVENT_TRANSCRIPT:
            if payload.get("final"):
                sink.transcript = str(payload.get("text") or "")
        elif kind == rp.EVENT_TEXT_DELTA:
            deltas += 1
            sink.text_deltas += 1
            if not sink.first_text_ms:
                sink.first_text_ms = event.captured_at
        elif kind == rp.EVENT_AUDIO_DELTA:
            deltas += 1
            sink.audio_deltas += 1
            if not sink.first_audio_ms:
                sink.first_audio_ms = event.captured_at
        elif kind == rp.EVENT_DONE:
            sink.saw_done = True
            break
        elif kind == rp.EVENT_ERROR:
            sink.errors.append(str(payload.get("code") or "error"))
            if payload.get("code") == "stream_broken":
                break
        if interrupt_after_deltas and not interrupted and deltas >= interrupt_after_deltas:
            interrupted = True
            provider.interrupt()
            sink.errors.append("interrupt-sent")
        if stop_after_deltas and deltas >= stop_after_deltas:
            break
    return deltas


def _wait_ready(provider: OmniRealtimeProvider, timeout: float = 8.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        event = provider.poll(timeout=0.3)
        if event is not None and event.kind == rp.EVENT_STATUS:
            return True
    return False


def _drain_quiet(provider: OmniRealtimeProvider, seconds: float = 1.0) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        provider.poll(timeout=0.2)


def main() -> int:
    parser = argparse.ArgumentParser(description="R12 realtime acceptance run")
    parser.add_argument("--wav", default="", help="16 kHz mono s16le speech wav")
    parser.add_argument("--quick", action="store_true",
                        help="skip the sustained and video scenarios")
    parser.add_argument("--report", default="", help="report path (default docs/)")
    args = parser.parse_args()
    _load_dotenv()

    wav = _ensure_wav(args.wav)
    with wave.open(wav, "rb") as w:
        pcm = w.readframes(w.getnframes())
    if len(pcm) < STEP * 60:
        print("WARNING: clip is short; results may be thin")

    results: dict[str, object] = {}
    turns: list[Turn] = []
    error_events = 0
    total_events = 0

    # ---- scenario 1 + 2: single turn, then sustained turns ------------------
    provider = OmniRealtimeProvider(session_id="r12", project_id="prj-r12")
    print("availability:", provider.availability().get("model"),
          provider.availability().get("voice"))
    if not provider.start():
        print("start failed")
        return 2
    _wait_ready(provider)

    # split the clip so each turn says something different
    n_turns = 1 if args.quick else 3
    span = max(3.0, min(4.0, len(pcm) / 32000 / n_turns))
    for i in range(n_turns):
        turn = Turn(label=f"turn-{i + 1}")
        start = i * int(span * 32000)
        chunk = pcm[start:start + int(span * 32000)]
        if len(chunk) < STEP * 10:
            break
        _stream(provider, chunk, span, turn)
        turns.append(turn)
        total_events += (turn.text_deltas + turn.audio_deltas
                         + len(turn.errors) + (1 if turn.saw_done else 0))
        error_events += len([e for e in turn.errors if e != "interrupt-sent"])
        print(f"  {turn.label}: e2e={turn.e2e_ms}ms model={turn.model_ms}ms "
              f"text={turn.text_deltas} audio={turn.audio_deltas} "
              f"done={turn.saw_done} transcript={turn.transcript[:40]!r}")
        _drain_quiet(provider, 1.0)

    # ---- scenario 3: interrupt ---------------------------------------------
    interrupt_turn = Turn(label="interrupt")
    if not args.quick:
        _stream(provider, pcm, 3.0, interrupt_turn, interrupt_after_deltas=4)
        after = _collect(provider, Turn(label="post-interrupt"), seconds=6.0)
        results["interrupt"] = {
            "deltas_before": interrupt_turn.text_deltas + interrupt_turn.audio_deltas,
            "deltas_after": after,
            "saw_done": interrupt_turn.saw_done,
        }
        print(f"  interrupt: before={results['interrupt']['deltas_before']} "
              f"after={after}")
        _drain_quiet(provider, 1.0)

    # ---- scenario 4: disconnect + reconnect --------------------------------
    provider.close()
    time.sleep(1.0)
    recovery_turn = Turn(label="after-reconnect")
    restarted = provider.start()
    recovered = False
    if restarted:
        recovered = _wait_ready(provider)
        if recovered:
            _stream(provider, pcm[:STEP * 40], 3.0, recovery_turn)
    results["recovery"] = {"restarted": restarted, "ready": recovered,
                           "text": recovery_turn.text_deltas,
                           "audio": recovery_turn.audio_deltas,
                           "done": recovery_turn.saw_done}
    print(f"  recovery: restarted={restarted} ready={recovered} "
          f"text={recovery_turn.text_deltas} audio={recovery_turn.audio_deltas}")

    # ---- scenario 5: a video frame -----------------------------------------
    video_ok = None
    if not args.quick:
        video_turn = Turn(label="video")
        # 1x1 PNG is enough to prove the event is accepted, not to be understood
        tiny = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00"
                b"\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\n"
                b"IDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00"
                b"\x00IEND\xaeB`\x82")
        video_ok = provider.send_frame(tiny, captured_at=int(time.time() * 1000))
        _drain_quiet(provider, 3.0)
        results["video_frame_accepted"] = video_ok
        print(f"  video frame accepted: {video_ok}")
    provider.close()

    # ---- report -------------------------------------------------------------
    good = [t for t in turns if t.saw_done and (t.text_deltas or t.audio_deltas)]
    first = good[0] if good else (turns[0] if turns else Turn(label="none"))
    sustained_ok = len(good) >= (2 if n_turns > 1 else 1)
    interrupt_ok = bool(results.get("interrupt")) and \
        int(results["interrupt"]["deltas_after"]) <= 2
    recovery_ok = bool(results["recovery"]["ready"]) and \
        (results["recovery"]["text"] or results["recovery"]["audio"])
    error_rate = (error_events / total_events * 100) if total_events else 0.0

    lines = [
        "# R12 实时链路验收报告（R4 provider 侧证据）",
        "",
        f"- 时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 模型：`{provider._model}` / 音色：`{provider._voice}`",
        f"- 音频样本：{os.path.basename(wav)}（16 kHz 单声道 s16le，实时节奏推流）",
        "",
        "## 指标",
        "",
        "| 指标 | 结果 | 判定 |",
        "| --- | --- | --- |",
        f"| 首响应（说话结束→首个输出） | {first.e2e_ms} ms | "
        f"{'通过' if 0 < first.e2e_ms < 8000 else '未通过'} |",
        f"| 模型延迟（服务端判定说完→首个输出） | {first.model_ms} ms | "
        f"{'通过' if 0 < first.model_ms < 8000 else '未通过'} |",
        f"| 持续响应 | {len(good)}/{len(turns)} 轮完整 | "
        f"{'通过' if sustained_ok else '未通过'} |",
        f"| 打断响应 | 打断后新增 {results.get('interrupt', {}).get('deltas_after', 'n/a')} 条输出 | "
        f"{'通过' if interrupt_ok else ('未测' if args.quick else '未通过')} |",
        f"| 断线恢复 | 重连={results['recovery']['ready']}，恢复后文本"
        f"{results['recovery']['text']}/音频{results['recovery']['audio']} | "
        f"{'通过' if recovery_ok else '未通过'} |",
        f"| 错误率 | {error_rate:.1f}%（{error_events}/{total_events}） | "
        f"{'通过' if error_rate < 5 else '未通过'} |",
        "",
        "## 逐轮明细",
        "",
        "| 轮次 | 端到端 | 模型延迟 | 文本 | 音频 | done | 转写 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for t in turns + [recovery_turn]:
        lines.append(f"| {t.label} | {t.e2e_ms} ms | {t.model_ms} ms | "
                     f"{t.text_deltas} | {t.audio_deltas} | "
                     f"{'是' if t.saw_done else '否'} | {t.transcript[:44]!r} |")
    lines += [
        "",
        "## 范围声明",
        "",
        "本报告只覆盖 **R4 provider 侧**：真实模型、音频输入、打断、断线重连。",
        "设备采集（摄像头/麦克风/屏幕共享）与驾驶舱体验调优属 R12 负责人范围，未在此覆盖。",
        "",
    ]
    report_path = args.report or os.path.join(
        REPORT_DIR, f"realtime-r12-acceptance-{time.strftime('%Y%m%d')}.md")
    os.makedirs(os.path.dirname(report_path), exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    print(f"\nreport -> {report_path}")

    ok = (0 < first.e2e_ms < 8000) and sustained_ok and recovery_ok and error_rate < 5
    print("RESULT:", "PASS" if ok else "PARTIAL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
