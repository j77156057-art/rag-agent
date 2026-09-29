"""Offline checks for the R12 acceptance runner's recovery contract."""

import verify_realtime_acceptance as acceptance


def test_done_is_required_even_when_transcript_exists():
    turn = acceptance.Turn(label="partial")
    turn.transcript = "用户说了话"
    turn.text_deltas = 3
    assert acceptance.turn_succeeded(turn) is False
    assert acceptance.turn_needs_retry(turn) is True

    turn.saw_done = True
    assert acceptance.turn_succeeded(turn) is True
    assert acceptance.turn_needs_retry(turn) is False


def test_done_without_assistant_output_is_not_success():
    turn = acceptance.Turn(label="empty")
    turn.saw_done = True
    assert acceptance.turn_succeeded(turn) is False
    assert acceptance.turn_needs_retry(turn) is True


def test_stream_pushes_one_second_silence_tail(monkeypatch):
    class Provider:
        def __init__(self):
            self.sent = []

        def send_audio(self, payload, captured_at=0):
            self.sent.append(payload)
            return True

    provider = Provider()
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(acceptance, "_collect", lambda *_args, **_kwargs: 0)
    pcm = b"speech" * (acceptance.STEP * 10 // 6 + 1)
    acceptance._stream(provider, pcm, 1.0, acceptance.Turn(label="tail"), tail=1.0)

    assert len(provider.sent) == 20
    assert provider.sent[-10:] == [acceptance.SILENCE] * 10


def test_stream_broken_is_retried_without_being_counted_as_fatal(monkeypatch):
    class Provider:
        def __init__(self):
            self._running = True
            self._ws = object()
            self.started = 0

        def close(self):
            self._running = False

        def start(self):
            self._running = True
            self._ws = object()
            self.started += 1
            return True

    provider = Provider()
    turn = acceptance.Turn(label="broken", stream_broken=True)
    calls = []
    monkeypatch.setattr(acceptance.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(acceptance, "_wait_ready", lambda *_args, **_kwargs: True)
    def replay(*_args, **_kwargs):
        calls.append("replayed")
        turn.saw_done = True
        turn.text_deltas = 1
    monkeypatch.setattr(acceptance, "_stream", replay)

    assert acceptance._retry_turn(provider, b"pcm", 1.0, turn, {"reconnects": 0}, 1.0)
    assert provider.started == 1
    assert calls == ["replayed"]
    assert turn.retried is True
