"""R3 实时会话网关的契约、故障与压力测试（任务 R11）。

本文件**只读** `api.py` 的 `/api/vision/live-stream` 处理器，不修改任何实现。覆盖任务表
里 R11 要求的四类场景：连接生命周期、时间同步、背压、取消，外加模型异常、跨项目隔离和
断线清理。真实摄像头/麦克风/实时模型的联验属于 R12，不在本文件范围。

两点写给后来人的坑：

* 假模型必须用**绑定异步方法**（`recorder.analyze`）做 `side_effect`。`AsyncMock` 不会
  await 一个"可调用实例"返回的协程，测试会静默拿到协程对象而不是结果。
* Starlette 的 `TestClient` 在端点**正常返回**时不会给客户端发 `websocket.close`，
  客户端 `receive_*` 会永久阻塞。只有服务端显式调用 `websocket.close(code=...)` 的路径
  才能用 `_read_until_close`；`session.close` 那条只能断言 ack，再用 `socket.close()` 收尾。
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
from starlette.websockets import WebSocketDisconnect

import api

ENDPOINT = "/api/vision/live-stream"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _packet(payload: bytes = b"jpeg", *, sequence: int = 1, captured_at: int | None = None,
            version: int = 1, kind: str = "video.frame", focused: bool = False) -> bytes:
    header = {"v": version, "type": kind, "sequence": sequence,
              "captured_at": _now_ms() if captured_at is None else captured_at}
    if focused:
        header["focused"] = True
    return json.dumps(header).encode("utf-8") + b"\n" + payload


def _hello(project_id: str, session_id: str = "") -> str:
    return json.dumps({"v": 1, "type": "hello", "project_id": project_id,
                       "session_id": session_id,
                       "capabilities": ["video.frame", "audio.chunk", "interrupt"]})


def _control(kind: str, **fields) -> str:
    return json.dumps({"v": 1, "type": kind, **fields})


class _Recorder:
    """可注入的假视觉模型：记录每次调用，按需阻塞、延迟或抛错。"""

    def __init__(self, *, hold_on: bytes | None = None, delay: float = 0.0,
                 fail_first: int = 0, result=None) -> None:
        self.calls: list[dict] = []
        self.hold_on = hold_on
        self.delay = delay
        self.fail_first = fail_first
        self.result = result
        self.entered = threading.Event()
        self.release = threading.Event()

    async def analyze(self, file, previous_observation: str = "", focused_region: str = ""):
        payload = await file.read()
        index = len(self.calls)
        self.calls.append({"payload": payload, "previous_observation": previous_observation,
                           "focused_region": focused_region, "project_id": api._ctx_project_id()})
        if index < self.fail_first:
            raise RuntimeError("provider down")
        if self.hold_on is not None and payload == self.hold_on:
            self.entered.set()
            # 网关在 finally 里会取消处理器，所以这里的等待必须有上限。
            await asyncio.to_thread(self.release.wait, 3)
        if self.delay:
            await asyncio.sleep(self.delay)
        if callable(self.result):
            return self.result(payload)
        if self.result is not None:
            return self.result
        return {"ok": True, "observations": [f"看到 {payload.decode('utf-8', 'replace')}"],
                "anomalies": [], "audit": {"mode": "native"}}

    def payloads(self) -> list[bytes]:
        return [call["payload"] for call in self.calls]


@contextmanager
def _gateway(recorder, *, known=("stream-test",)):
    def get_project(project_id):
        return {"root": f"root-{project_id}"} if project_id in known else None

    with patch.object(api.projects, "get_project", side_effect=get_project), \
            patch.object(api, "analyze_live_frame_ep", side_effect=recorder.analyze):
        with TestClient(api.app) as client:
            yield client


def _connect(client, project_id: str):
    return client.websocket_connect(f"{ENDPOINT}?project_id={project_id}")


def _read_until_close(socket):
    """读完服务端显式关闭前排队的事件，返回 (事件列表, 关闭码)。"""
    events = []
    with pytest.raises(WebSocketDisconnect) as excinfo:
        while True:
            events.append(socket.receive_json())
    return events, excinfo.value.code


def _handshake(socket, project_id: str = "stream-test", session_id: str = "") -> dict:
    socket.send_text(_hello(project_id, session_id=session_id))
    return socket.receive_json()


# ---- 连接生命周期 ------------------------------------------------------------

def test_hello_completes_the_handshake_with_project_and_capabilities():
    with _gateway(_Recorder()) as client:
        with _connect(client, "stream-test") as socket:
            event = _handshake(socket, session_id="sess-1")
    assert event["type"] == "hello.ok"
    assert event["v"] == api.PROTOCOL_VERSION
    assert event["project_id"] == "stream-test"
    assert event["session_id"] == "sess-1"
    assert "video.observation" in event["capabilities"]


def _assert_frontend_parseable(event: dict) -> None:
    """前端 `parseRealtimeServerEvent` 只接受 v===1 + string 型 type + number 型 sent_at。

    少任何一个字段，前端会把整条消息静默丢掉——观察结果、错误、心跳都会凭空消失，
    而且不会在 UI 上留下任何痕迹。
    """
    assert event.get("v") == api.PROTOCOL_VERSION, f"前端会丢弃：{event}"
    assert isinstance(event.get("type"), str), f"前端会丢弃：{event}"
    assert isinstance(event.get("sent_at"), int), f"前端会丢弃：{event}"


def test_every_gateway_message_is_parseable_by_the_frontend_helper():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _assert_frontend_parseable(_handshake(socket))
            socket.send_bytes(b"garbage-without-header")
            _assert_frontend_parseable(socket.receive_json())   # error / invalid_media
            socket.send_bytes(_packet(b"ok", sequence=1))
            _assert_frontend_parseable(socket.receive_json())   # video.observation
            socket.send_text(_control("heartbeat", sent_at=1))
            _assert_frontend_parseable(socket.receive_json())   # heartbeat
            socket.send_text(_control("cancel"))
            _assert_frontend_parseable(socket.receive_json())   # cancel.ok
            socket.send_text(_control("session.close"))
            _assert_frontend_parseable(socket.receive_json())   # session.closed
            socket.close()


def test_frame_sent_before_hello_closes_the_session():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            socket.send_bytes(_packet(b"early"))
            events, code = _read_until_close(socket)
    assert [e["type"] for e in events] == ["error"]
    assert events[0]["code"] == "hello_required"
    assert code == 1008
    assert recorder.calls == [], "未握手就发的帧不得进入模型"


def test_hello_for_another_project_cannot_open_this_session():
    """跨项目串号防线：socket 绑定的项目与 hello 声明的项目必须一致。"""
    recorder = _Recorder()
    with _gateway(recorder, known=("stream-test", "other-project")) as client:
        with _connect(client, "stream-test") as socket:
            socket.send_text(_hello("other-project"))
            events, code = _read_until_close(socket)
    assert [e["code"] for e in events] == ["hello_required"]
    assert code == 1008


def test_second_hello_is_rejected_but_keeps_the_session_alive():
    with _gateway(_Recorder()) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_text(_hello("stream-test"))
            rejected = socket.receive_json()
            socket.send_text(_control("heartbeat", sent_at=42))
            following = socket.receive_json()
    assert rejected["type"] == "error" and rejected["code"] == "duplicate_hello"
    assert following["type"] == "heartbeat", "重复 hello 不应关闭会话"


def test_heartbeat_echoes_the_client_clock():
    with _gateway(_Recorder()) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket, session_id="sess-1")
            socket.send_text(_control("heartbeat", sent_at=1_726_000_000_000))
            event = socket.receive_json()
    assert event["type"] == "heartbeat"
    assert event["echo"] == 1_726_000_000_000
    assert event["session_id"] == "sess-1"


def test_session_close_acks_and_stops_processing():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"before-close", sequence=1))
            socket.receive_json()
            socket.send_text(_control("session.close", reason="用户关闭标签页"))
            ack = socket.receive_json()
            socket.send_bytes(_packet(b"after-close", sequence=2))
            socket.close()
    assert ack["type"] == "session.closed"
    assert ack["reason"] == "用户关闭标签页"
    assert recorder.payloads() == [b"before-close"], "关闭后送达的帧仍进了模型"


def test_unregistered_project_is_refused_before_accept():
    with patch.object(api.projects, "get_project", return_value=None):
        with TestClient(api.app) as client:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                with client.websocket_connect(f"{ENDPOINT}?project_id=missing"):
                    pass
    assert excinfo.value.code == 1008


def test_missing_project_id_is_refused():
    with TestClient(api.app) as client:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            with client.websocket_connect(ENDPOINT):
                pass
    assert excinfo.value.code == 1008


@pytest.mark.parametrize("origin, allowed, note", [
    (None, True, "非浏览器客户端不带 Origin"),
    ("http://localhost:8000", True, "同源 http"),
    ("https://localhost:8000", True, "同源 https"),
    ("HTTP://LOCALHOST:8000", True, "同源大小写不敏感"),
    ("https://app.example", True, "显式配置的来源"),
    ("https://evil.example", False, "外站"),
    ("http://localhost:9999", False, "同主机不同端口"),
    ("file:///c:/tmp/page.html", False, "非 http(s) 来源"),
])
def test_origin_policy_matrix(origin, allowed, note):
    headers = {"host": "localhost:8000"}
    if origin is not None:
        headers["origin"] = origin

    class _Socket:
        pass

    socket = _Socket()
    socket.headers = headers
    with patch.object(api, "DOCMIND_CORS_ORIGINS", ["https://app.example"]):
        assert api._realtime_origin_allowed(socket) is allowed, note


def test_wildcard_cors_never_authorises_a_foreign_websocket_origin():
    """CORS 的 `*` 只该放宽普通请求，不能把实时会话也放开。"""

    class _Socket:
        headers = {"host": "localhost:8000", "origin": "https://evil.example"}

    with patch.object(api, "DOCMIND_CORS_ORIGINS", ["*"]):
        assert api._realtime_origin_allowed(_Socket()) is False


# ---- 协议违规与畸形输入 ------------------------------------------------------

def test_invalid_json_closes_with_unsupported_data():
    with _gateway(_Recorder()) as client:
        with _connect(client, "stream-test") as socket:
            socket.send_text("{not json")
            events, code = _read_until_close(socket)
    assert [e["code"] for e in events] == ["invalid_json"]
    assert code == 1003


@pytest.mark.parametrize("raw, note", [
    (_control("nope"), "未声明的控制类型"),
    (_control("video.frame"), "媒体类型走文本通道"),
    (json.dumps({"type": "hello", "project_id": "stream-test"}), "缺少版本号"),
    (json.dumps({"v": 2, "type": "hello", "project_id": "stream-test"}), "版本漂移"),
])
def test_unsupported_control_packets_close_with_unsupported_data(raw, note):
    with _gateway(_Recorder()) as client:
        with _connect(client, "stream-test") as socket:
            socket.send_text(raw)
            events, code = _read_until_close(socket)
    assert [e["code"] for e in events] == ["invalid_control"], note
    assert code == 1003


def test_malformed_media_packet_is_reported_without_dropping_the_session():
    """单帧畸形只该丢掉那一帧：连续对话不能被一个坏包打断。"""
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(b"garbage-without-header")
            rejected = socket.receive_json()
            socket.send_bytes(_packet(b"good", sequence=1))
            observation = socket.receive_json()
    assert rejected["type"] == "error" and rejected["code"] == "invalid_media"
    assert observation["type"] == "video.observation"
    assert recorder.payloads() == [b"good"]


def test_version_drift_on_the_media_channel_is_rejected():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"v2-frame", version=2))
            rejected = socket.receive_json()
    assert rejected["code"] == "invalid_media"
    assert recorder.calls == []


def test_oversized_frame_is_rejected_without_reaching_the_model():
    oversized = b"x" * (api._LIVE_VISION_MAX_BYTES + 1)
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(oversized, sequence=1))
            rejected = socket.receive_json()
    assert rejected["type"] == "error" and rejected["code"] == "frame_too_large"
    assert recorder.calls == []


def test_audio_chunk_is_acknowledged_as_not_ready_and_never_becomes_a_frame():
    """协议已给音频留位，网关尚未接入：必须明说不可用，而不是静默吞掉。"""
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"pcm-16k", sequence=1, kind="audio.chunk"))
            rejected = socket.receive_json()
            socket.send_bytes(_packet(b"real-frame", sequence=2))
            observation = socket.receive_json()
    assert rejected["code"] == "audio_not_ready"
    assert rejected["retryable"] is True
    assert observation["sequence"] == 2
    assert recorder.payloads() == [b"real-frame"]


# ---- 背压与时间同步 ----------------------------------------------------------

def test_only_the_newest_frame_survives_while_the_model_is_busy():
    recorder = _Recorder(hold_on=b"first")
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"first", sequence=1))
            assert recorder.entered.wait(3)
            socket.send_bytes(_packet(b"intermediate", sequence=2))
            socket.send_bytes(_packet(b"latest", sequence=3))
            recorder.release.set()
            first = socket.receive_json()
            second = socket.receive_json()
    assert (first["sequence"], second["sequence"]) == (1, 3)
    assert recorder.payloads() == [b"first", b"latest"]


def test_observation_keeps_its_own_capture_time_not_the_newest_arrival():
    """验收门槛：回答引用的画面不能晚于用户发言——时间戳必须跟着那一帧走。"""
    recorder = _Recorder(hold_on=b"first")
    first_at = _now_ms() - 5_000
    latest_at = _now_ms()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"first", sequence=1, captured_at=first_at))
            assert recorder.entered.wait(3)
            socket.send_bytes(_packet(b"intermediate", sequence=2, captured_at=latest_at - 100))
            socket.send_bytes(_packet(b"latest", sequence=3, captured_at=latest_at))
            recorder.release.set()
            first = socket.receive_json()
            second = socket.receive_json()
    assert (first["sequence"], first["captured_at"]) == (1, first_at)
    assert (second["sequence"], second["captured_at"]) == (3, latest_at)


def test_observation_carries_the_focused_region_flag_from_that_frame():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"region", sequence=1, focused=True))
            socket.receive_json()
    assert recorder.calls[0]["focused_region"] == "1"


def test_client_sequence_numbers_are_preserved_even_when_they_arrive_out_of_order():
    """网关不重排、不改写序号：前端要靠它把观察结果对回自己发出的那一帧。"""
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"five", sequence=5))
            socket.send_bytes(_packet(b"two", sequence=2))
            seen = [socket.receive_json(), socket.receive_json()]
    assert [e["sequence"] for e in seen] == [5, 2]
    assert recorder.payloads() == [b"five", b"two"]


def test_sustained_frame_pressure_is_coalesced_instead_of_queued():
    """压力场景：40 帧连续灌入时，网关的模型调用次数必须远小于帧数。"""
    recorder = _Recorder(delay=0.02)
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            for index in range(40):
                socket.send_bytes(_packet(f"frame-{index}".encode(), sequence=index + 1))
            observations = []
            while len(observations) < 60:
                event = socket.receive_json()
                if event["type"] != "video.observation":
                    continue
                observations.append(event)
                if event["sequence"] == 40:  # 最后一帧的观察结果到了，压力已泄完
                    break
    assert recorder.payloads()[-1] == b"frame-39", "最新帧必须被处理，不能只处理积压队头"
    assert len(observations) <= 8, f"积压帧没有被合并，模型被调用了 {len(observations)} 次"


# ---- 取消与打断 --------------------------------------------------------------

def test_cancel_acks_and_discards_the_in_flight_frame():
    """用户抢话：已经送进模型的旧帧不得再回到前端。"""
    recorder = _Recorder(hold_on=b"stale")
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket, session_id="sess-1")
            socket.send_bytes(_packet(b"stale", sequence=1))
            assert recorder.entered.wait(3)
            socket.send_text(_control("cancel", reason="用户抢话"))
            ack = socket.receive_json()
            assert ack["type"] == "cancel.ok" and ack["reason"] == "用户抢话"
            recorder.release.set()
            time.sleep(0.15)
            socket.send_text(_control("heartbeat", sent_at=7))
            following = socket.receive_json()
    assert following["type"] == "heartbeat", "取消后仍收到了被取消帧的观察结果"


def test_cancel_also_clears_a_frame_that_never_reached_the_model():
    recorder = _Recorder(hold_on=b"in-flight")
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"in-flight", sequence=1))
            assert recorder.entered.wait(3)
            socket.send_bytes(_packet(b"queued", sequence=2))
            socket.send_text(_control("cancel"))
            assert socket.receive_json()["type"] == "cancel.ok"
            recorder.release.set()
            time.sleep(0.15)
            socket.send_text(_control("heartbeat", sent_at=7))
            following = socket.receive_json()
    assert following["type"] == "heartbeat"
    assert b"queued" not in recorder.payloads(), "取消时排队中的帧仍被送进了模型"


# ---- 模型异常与降级 ----------------------------------------------------------

def test_model_failure_is_reported_on_the_wire_and_the_session_survives():
    recorder = _Recorder(fail_first=1)
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"boom", sequence=1))
            failed = socket.receive_json()
            socket.send_bytes(_packet(b"recovered", sequence=2))
            recovered = socket.receive_json()
    assert failed["type"] == "video.observation" and failed["ok"] is False
    assert "视觉帧分析失败" in failed["error"]
    assert recovered["ok"] is True and recovered["observations"] == ["看到 recovered"]


def test_previous_observation_is_chained_within_one_session():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"first", sequence=1))
            socket.receive_json()
            socket.send_bytes(_packet(b"second", sequence=2))
            socket.receive_json()
    assert recorder.calls[0]["previous_observation"] == ""
    assert "看到 first" in recorder.calls[1]["previous_observation"]


def test_non_dict_model_result_is_replaced_instead_of_crashing_the_gateway():
    recorder = _Recorder(result=[{"not": "a dict"}])
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"frame", sequence=1))
            event = socket.receive_json()
    assert event["type"] == "video.observation"
    assert event["ok"] is False and "无效响应" in event["error"]


def test_response_object_body_is_unwrapped():
    class _Response:
        body = json.dumps({"ok": True, "observations": ["来自 Response"],
                           "anomalies": [], "audit": {"mode": "native"}}).encode("utf-8")

    with _gateway(_Recorder(result=_Response())) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"frame", sequence=1))
            event = socket.receive_json()
    assert event["observations"] == ["来自 Response"]


def test_unparsable_response_body_is_reported_as_an_error():
    class _Response:
        body = b"<html>gateway timeout</html>"

    with _gateway(_Recorder(result=_Response())) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"frame", sequence=1))
            event = socket.receive_json()
    assert event["type"] == "video.observation" and event["ok"] is False
    assert "无法解析" in event["error"]


def test_non_list_observations_are_dropped_from_the_wire():
    recorder = _Recorder(result={"ok": True, "observations": "not-a-list"})
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"frame", sequence=1))
            event = socket.receive_json()
    assert event["observations"] == []


# ---- 项目隔离与断线清理 ------------------------------------------------------

def test_concurrent_sessions_never_share_a_project_context():
    """两个项目同时开会话时，模型侧看到的项目 id 不能互相串。"""
    recorder = _Recorder(hold_on=b"a-frame")
    with _gateway(recorder, known=("proj-a", "proj-b")) as client:
        with _connect(client, "proj-a") as socket_a:
            _handshake(socket_a, "proj-a")
            socket_a.send_bytes(_packet(b"a-frame", sequence=1))
            assert recorder.entered.wait(3)
            with _connect(client, "proj-b") as socket_b:
                _handshake(socket_b, "proj-b")
                socket_b.send_bytes(_packet(b"b-frame", sequence=1))
                assert socket_b.receive_json()["observations"] == ["看到 b-frame"]
                recorder.release.set()
                assert socket_a.receive_json()["observations"] == ["看到 a-frame"]
    by_payload = {call["payload"]: call["project_id"] for call in recorder.calls}
    assert by_payload == {b"a-frame": "proj-a", b"b-frame": "proj-b"}


def test_closing_a_session_clears_its_registered_client_slot():
    recorder = _Recorder()
    with _gateway(recorder) as client:
        with api._LIVE_VISION_LOCK:
            api._LIVE_VISION_CLIENTS["stream-test"] = (0, object())
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
    with api._LIVE_VISION_LOCK:
        assert "stream-test" not in api._LIVE_VISION_CLIENTS, "断线后残留了项目客户端槽位"


def test_abrupt_disconnect_leaves_no_processor_behind():
    """关闭标签页后同项目立刻重连：上一个会话不能继续吃掉新会话的帧。"""
    recorder = _Recorder(hold_on=b"orphan")
    with _gateway(recorder) as client:
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"orphan", sequence=1))
            assert recorder.entered.wait(3)
        # 出了 with：客户端断开，网关在 finally 里取消处理器
        recorder.release.set()
        recorder.hold_on = None
        time.sleep(0.2)
        with _connect(client, "stream-test") as socket:
            _handshake(socket)
            socket.send_bytes(_packet(b"fresh", sequence=1))
            assert socket.receive_json()["observations"] == ["看到 fresh"]
    assert recorder.payloads() == [b"orphan", b"fresh"]
    with api._LIVE_VISION_LOCK:
        assert "stream-test" not in api._LIVE_VISION_CLIENTS
