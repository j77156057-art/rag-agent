"""AI-A：R0/R3 网关的补充契约与压力测试（只读，不触碰实现）。

与既有的 `test_realtime_protocol_contract.py` / `test_realtime_gateway_faults.py`（R11）
互补，本文件钉死的是它们没有覆盖的网关对外行为：

1. 线上信封规则——网关发出的**每一个**事件都必须能被前端 `parseRealtimeServerEvent`
   解析（``v == 1``、``type`` 是已声明的服务端事件、``sent_at`` 是数值）；
2. 模型被拖住时控制通道（heartbeat/cancel）仍然即时响应；
3. 畸形包风暴与取消风暴只丢包、不拖垮会话，也不让坏数据进入模型；
4. 同一项目并发两个会话时事件按 session 隔离，取消不跨会话生效；
5. 反复重连不留残骸；hello 的 ``session_id`` 截断上限是对外契约的一部分；
6. 采集时间窗口在网关入口的拒绝行为（陈旧帧/未来帧不进模型）。

真实摄像头/麦克风/实时模型联验属于 R12；本文件全部使用注入的假模型。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api
from agent_runtime.realtime_protocol import PROTOCOL_VERSION, SERVER_TYPES

ENDPOINT = "/api/vision/live-stream"
PROJECT = "stress-test"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _packet(payload: bytes = b"jpeg", *, sequence: int = 1,
            captured_at: int | None = None, kind: str = "video.frame") -> bytes:
    header = {"v": PROTOCOL_VERSION, "type": kind, "sequence": sequence,
              "captured_at": _now_ms() if captured_at is None else captured_at}
    return json.dumps(header).encode("utf-8") + b"\n" + payload


def _hello(session_id: str = "") -> str:
    return json.dumps({"v": PROTOCOL_VERSION, "type": "hello", "project_id": PROJECT,
                       "session_id": session_id,
                       "capabilities": ["video.frame", "audio.chunk", "interrupt"]})


def _control(kind: str, **fields) -> str:
    return json.dumps({"v": PROTOCOL_VERSION, "type": kind, **fields})


class _Recorder:
    """假视觉模型：记录调用，可按 payload 阻塞、按固定节奏延迟。"""

    def __init__(self, *, hold_on: bytes | None = None, delay: float = 0.0) -> None:
        self.calls: list[bytes] = []
        self.hold_on = hold_on
        self.delay = delay
        self.entered = threading.Event()
        self.release = threading.Event()

    async def analyze(self, file, previous_observation: str = "", focused_region: str = ""):
        payload = await file.read()
        self.calls.append(payload)
        if self.hold_on is not None and payload == self.hold_on:
            self.entered.set()
            await asyncio.to_thread(self.release.wait, 3)
        if self.delay:
            await asyncio.sleep(self.delay)
        return {"ok": True, "observations": [f"看到 {payload.decode('utf-8', 'replace')}"],
                "anomalies": [], "audit": {"mode": "native"}}

    def payloads(self) -> list[bytes]:
        return list(self.calls)


@contextmanager
def _gateway(recorder):
    with patch.object(api.projects, "get_project",
                      side_effect=lambda pid: {"root": pid} if pid == PROJECT else None), \
            patch.object(api, "analyze_live_frame_ep", side_effect=recorder.analyze):
        with TestClient(api.app) as client:
            yield client


def _connect(client):
    return client.websocket_connect(f"{ENDPOINT}?project_id={PROJECT}")


def _handshake(socket, session_id: str = "") -> dict:
    socket.send_text(_hello(session_id))
    return socket.receive_json()


# ---- 线上信封规则：每个事件都必须能被前端解析 ---------------------------------

def _assert_frontend_parsable(event: dict) -> None:
    """复刻前端 parseRealtimeServerEvent 的三条硬规则，任何一条不过就是协议事故。"""
    assert event.get("v") == PROTOCOL_VERSION, f"版本号不符，前端会丢弃: {event}"
    assert isinstance(event.get("type"), str) and event["type"] in SERVER_TYPES, \
        f"事件类型不在词汇表内: {event}"
    assert isinstance(event.get("sent_at"), (int, float)) and not isinstance(
        event.get("sent_at"), bool), f"sent_at 必须是数值: {event}"
    for field in ("sequence", "captured_at"):
        if field in event and event[field] is not None:
            assert isinstance(event[field], int), f"{field} 必须是整数: {event}"


def test_every_event_emitted_over_the_session_lifecycle_is_frontend_parsable():
    """一次会话走满 hello/观察/错误/心跳/取消/关闭六种事件，全部钉进信封规则。"""
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client) as socket:
            lifecycle = [_handshake(socket, "sess-env")]
            socket.send_bytes(_packet(b"frame-1", sequence=1))
            lifecycle.append(socket.receive_json())
            socket.send_bytes(b"garbage-no-header")
            lifecycle.append(socket.receive_json())
            socket.send_text(_control("heartbeat", sent_at=123))
            lifecycle.append(socket.receive_json())
            socket.send_text(_control("cancel", reason="压测"))
            lifecycle.append(socket.receive_json())
            socket.send_text(_control("session.close"))
            lifecycle.append(socket.receive_json())
    types = [event["type"] for event in lifecycle]
    assert types == ["hello.ok", "video.observation", "error", "heartbeat",
                     "cancel.ok", "session.closed"], types
    for event in lifecycle:
        _assert_frontend_parsable(event)
    assert set(lifecycle[0]["capabilities"]) == {"video.observation", "heartbeat", "cancel"}, \
        "hello.ok 声明的能力必须都是本网关真实可达的通道"
    for event in lifecycle[1:]:
        if event["type"] != "error":
            assert event.get("session_id") == "sess-env", \
                f"握手后的非错误事件必须带会话 id，前端靠它隔离重连前的旧事件: {event}"


def test_error_events_after_handshake_carry_the_session_id():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client) as socket:
            _handshake(socket, "sess-gap")
            socket.send_bytes(b"garbage-no-header")
            event = socket.receive_json()
    assert event["type"] == "error" and event["code"] == "invalid_media"
    assert event["session_id"] == "sess-gap"


# ---- 控制通道存活：模型慢不能饿死 heartbeat/cancel ----------------------------

def test_control_channel_stays_responsive_while_the_model_is_blocked():
    recorder = _Recorder(hold_on=b"slow-frame")
    with _gateway(recorder) as client:
        with _connect(client) as socket:
            _handshake(socket, "sess-ctl")
            socket.send_bytes(_packet(b"slow-frame", sequence=1))
            assert recorder.entered.wait(3), "假模型未按预期进入阻塞"
            for beat in (1, 2, 3):
                socket.send_text(_control("heartbeat", sent_at=beat))
                echo = socket.receive_json()
                assert echo["type"] == "heartbeat" and echo["echo"] == beat, \
                    f"模型在忙，第 {beat} 次心跳没有得到即时响应"
            socket.send_text(_control("cancel", reason="抢话"))
            ack = socket.receive_json()
            assert ack["type"] == "cancel.ok"
            recorder.release.set()
            time.sleep(0.15)
            socket.send_text(_control("heartbeat", sent_at=99))
            tail = socket.receive_json()
    assert tail["type"] == "heartbeat", "被取消帧的结果在取消后泄漏到了线上"


# ---- 畸形包风暴 / 取消风暴：只丢包，不拖垮会话 --------------------------------

def test_invalid_media_burst_drops_only_the_bad_packets():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client) as socket:
            _handshake(socket)
            for _ in range(100):
                socket.send_bytes(b"\x00\xff not-a-packet")
                rejected = socket.receive_json()
                assert rejected["type"] == "error" and rejected["code"] == "invalid_media"
            socket.send_bytes(_packet(b"survivor", sequence=101))
            observation = socket.receive_json()
    assert observation["type"] == "video.observation" and observation["ok"] is True
    assert recorder.payloads() == [b"survivor"], "畸形包风暴期间有坏包漏进了模型"


def test_cancel_storm_acks_every_cancel_and_keeps_the_pipeline_usable():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client) as socket:
            _handshake(socket)
            for _ in range(50):
                socket.send_text(_control("cancel"))
                ack = socket.receive_json()
                assert ack["type"] == "cancel.ok"
            socket.send_bytes(_packet(b"after-storm", sequence=1))
            observation = socket.receive_json()
    assert observation["sequence"] == 1 and observation["ok"] is True
    assert recorder.payloads() == [b"after-storm"]


# ---- 同项目多会话：事件按 session 隔离，取消不跨会话 --------------------------

def test_two_sessions_on_one_project_never_see_each_others_events():
    recorder = _Recorder(hold_on=b"a-frame")
    with _gateway(recorder) as client:
        with _connect(client) as socket_a, _connect(client) as socket_b:
            _handshake(socket_a, "sess-a")
            _handshake(socket_b, "sess-b")
            socket_a.send_bytes(_packet(b"a-frame", sequence=1))
            assert recorder.entered.wait(3)
            # A 的帧卡在模型里时，B 必须照常完成自己的帧。
            socket_b.send_bytes(_packet(b"b-frame", sequence=2))
            b_observation = socket_b.receive_json()
            # A 抢话取消：只作废 A 的旧帧，不能碰 B。
            socket_a.send_text(_control("cancel", reason="A 用户抢话"))
            a_cancel = socket_a.receive_json()
            recorder.release.set()
            socket_a.send_text(_control("heartbeat", sent_at=7))
            a_tail = socket_a.receive_json()
            time.sleep(0.15)
            socket_b.send_text(_control("heartbeat", sent_at=8))
            b_tail = socket_b.receive_json()
    assert b_observation["session_id"] == "sess-b" and b_observation["sequence"] == 2
    assert a_cancel["type"] == "cancel.ok" and a_cancel["session_id"] == "sess-a"
    assert a_tail["type"] == "heartbeat", "A 取消后旧帧结果仍回了 A"
    assert b_tail["type"] == "heartbeat" and b_tail["session_id"] == "sess-b"
    seen = {call for call in recorder.payloads()}
    assert seen == {b"a-frame", b"b-frame"}


def test_a_frame_cancelled_in_one_session_is_not_delivered_to_that_session():
    """会话 A 取消后其滞留帧结果不能漂移到同项目会话 B 的连接上。"""
    recorder = _Recorder(hold_on=b"a-frame")
    with _gateway(recorder) as client:
        with _connect(client) as socket_a, _connect(client) as socket_b:
            _handshake(socket_a, "sess-a")
            _handshake(socket_b, "sess-b")
            socket_a.send_bytes(_packet(b"a-frame", sequence=1))
            assert recorder.entered.wait(3)
            socket_a.send_text(_control("cancel"))
            assert socket_a.receive_json()["type"] == "cancel.ok"
            recorder.release.set()
            time.sleep(0.15)
            socket_b.send_bytes(_packet(b"b-frame", sequence=1))
            b_event = socket_b.receive_json()
    assert b_event["session_id"] == "sess-b" and b_event["sequence"] == 1
    assert b_event["observations"] == ["看到 b-frame"], "B 收到了 A 的观察结果"


# ---- 重连卫生：反复连接-收帧-断开不留残骸 ------------------------------------

def test_repeated_reconnect_cycles_leave_no_residue_and_all_succeed():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        for index in range(8):
            session_id = f"cycle-{index}"
            with _connect(client) as socket:
                _handshake(socket, session_id)
                socket.send_bytes(_packet(f"f{index}".encode(), sequence=1))
                observation = socket.receive_json()
            assert observation["type"] == "video.observation"
            assert observation["session_id"] == session_id
        assert recorder.payloads() == [f"f{i}".encode() for i in range(8)], \
            "重连周期里帧被上一会话的残骸吃掉或丢了"
        with api._LIVE_VISION_LOCK:
            assert PROJECT not in api._LIVE_VISION_CLIENTS
        with _connect(client) as socket:
            _handshake(socket, "fresh")
            socket.send_bytes(_packet(b"fresh", sequence=1))
            assert socket.receive_json()["observations"] == ["看到 fresh"]


# ---- session_id 上限：160 截断是对外契约 -------------------------------------

def test_oversized_session_id_is_truncated_to_160_on_hello_ok():
    long_id = "s" * 300
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client) as socket:
            hello = _handshake(socket, long_id)
            assert len(hello["session_id"]) == 160
            socket.send_text(_control("heartbeat", sent_at=1))
            echo = socket.receive_json()
    assert echo["session_id"] == "s" * 160, "后续事件必须使用截断后的同一个会话 id"


# ---- 时间窗在网关入口的拒绝：陈旧帧/未来帧不进模型 ----------------------------

@pytest.mark.parametrize("offset, note", [
    (-121_000, "早于 120 秒窗口的陈旧帧"),
    (61_000, "晚于 60 秒窗口的未来帧"),
])
def test_frames_outside_the_capture_window_are_refused_at_the_gateway(offset, note):
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client) as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"bad-clock", sequence=1,
                                      captured_at=_now_ms() + offset))
            rejected = socket.receive_json()
            socket.send_bytes(_packet(b"good-clock", sequence=2))
            observation = socket.receive_json()
    assert rejected["type"] == "error" and rejected["code"] == "invalid_media", note
    assert observation["ok"] is True, "拒绝越窗帧不应拖垮会话"
    assert recorder.payloads() == [b"good-clock"], f"{note}被送进了模型"
