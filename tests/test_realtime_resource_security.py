"""R9 可靠性/资源/安全审计（AI-A/AI-B 兼任）——对 `realtime_bridge` + `live-stream` 网关的
只读审计测试。**只新增测试，不改任何实现**（网关 `api.py` 归 /root 独占）。

与 `test_realtime_gateway_bridge.py`（AI-F，覆盖降级/上报/收尾/指标/会话劫持等）互补，
本文件专门审计 R9 验收里那些**AI-F 用例没测的资源韧性与隔离面**：

1. 快速开关会话的**引用/作用域不泄漏**（AI-F 只测了 2 个会话，这里压 8 轮 + 校验指标作用域清空）；
2. **provider.close() 外抛时收尾仍要落地**——时间线释放、指标会话作用域回收不能被异常打断；
3. 原生模式下**超限音频不得转发**给 provider（先于 take_audio 命中大小门）；
4. `send_frame` 返回 False 时帧必须**回落到抽帧单槽**，不能被静默丢弃；
5. **跨项目时间线隔离**：项目 B 的状态视图不得含项目 A 的帧/观察条目；
6. 已知缺口：网关对 provider 的 `interrupt()/send_frame()/take_audio()` 三处调用**没有 try 守卫**，
   而基类契约明确要求 provider「fail-closed，不外抛」。这与已加守卫的 `start()/pump` 不一致。
   本文件用「记录当前行为」的方式钉住这颗雷，/root 加守卫后应翻转成「会话存活」断言。

真实设备/真实模型联验属 R12；全部用注册进 `realtime_provider` 的假 provider 驱动。
"""
from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import api
from agent_runtime import realtime_bridge, realtime_metrics as metrics, realtime_provider

PROJECT_A = "r9-project-a"
PROJECT_B = "r9-project-b"
FAKE_NAME = "r9-fake"
FRAME = b"\xff\xd8r9-jpeg\xff\xd9"


# ---- 假 provider（可控：可让 close/interrupt/send_frame 外抛） -----------------

class R9FakeProvider(realtime_provider.RealtimeProvider):
    name = FAKE_NAME

    def __init__(self, *, raise_on: str = "", send_frame_ok: bool = True) -> None:
        super().__init__()
        self.raise_on = raise_on          # "close" | "interrupt" | "send_frame" | "take_audio"
        self.send_frame_ok = send_frame_ok
        self.closed = False
        self.frames: list[bytes] = []
        self.audio: list[bytes] = []
        self.interrupts = 0

    def capabilities(self) -> list[str]:
        return [realtime_provider.CAP_AUDIO_IN, realtime_provider.CAP_VIDEO_IN,
                realtime_provider.CAP_TEXT_OUT, realtime_provider.CAP_INTERRUPT]

    def availability(self) -> dict:
        return {"ok": True, "provider": FAKE_NAME}

    def start(self) -> bool:
        self._running = True
        return True

    def close(self) -> None:
        if self.raise_on == "close":
            raise RuntimeError("provider close boom")
        self.closed = True
        super().close()

    def send_frame(self, image: bytes, captured_at: int = 0) -> bool:
        if self.raise_on == "send_frame":
            raise RuntimeError("provider send_frame boom")
        if not self.send_frame_ok:
            return False
        self.frames.append(image)
        return True

    def send_audio(self, pcm: bytes, captured_at: int = 0) -> bool:
        if self.raise_on == "take_audio":
            raise RuntimeError("provider send_audio boom")
        self.audio.append(pcm)
        return True

    def interrupt(self) -> bool:
        if self.raise_on == "interrupt":
            raise RuntimeError("provider interrupt boom")
        self.interrupts += 1
        return True


def _register(instance):
    realtime_provider.register(FAKE_NAME, lambda: instance)


def _get_project(project_id):
    return {"root": f"root-{project_id}"} if project_id in (PROJECT_A, PROJECT_B) else None


@pytest.fixture(autouse=True)
def _clean_state():
    realtime_bridge.reset_state()
    yield
    _wait_for(lambda: realtime_bridge.active_timeline_projects() == [], timeout=10.0)
    realtime_bridge.reset_state()
    realtime_provider.unregister(FAKE_NAME)


def _gateway_client():
    """返回一个 TestClient 上下文（网关已就绪，抽帧路径用占位模型）。"""
    async def _analyze(file, previous_observation="", focused_region=""):
        await file.read()
        return {"ok": True, "observations": ["抽帧观察"], "anomalies": [], "audit": {}}

    stack = [
        patch.object(api.projects, "get_project", side_effect=_get_project),
        patch.object(api, "analyze_live_frame_ep", side_effect=_analyze),
    ]
    for p in stack:
        p.start()
    client = TestClient(api.app)
    client.__enter__()
    return client, stack


def _now_ms() -> int:
    return int(time.time() * 1000)


def _frame(sequence: int, project: str = PROJECT_A) -> bytes:
    header = {"v": api.PROTOCOL_VERSION, "type": "video.frame", "sequence": sequence,
              "captured_at": _now_ms()}
    return json.dumps(header).encode("utf-8") + b"\n" + FRAME


def _audio(sequence: int, payload: bytes = b"\x00\x01\x02\x03") -> bytes:
    header = {"v": api.PROTOCOL_VERSION, "type": "audio.chunk", "sequence": sequence,
              "captured_at": _now_ms()}
    return json.dumps(header).encode("utf-8") + b"\n" + payload


def _hello(project: str, session_id: str) -> str:
    return json.dumps({"v": api.PROTOCOL_VERSION, "type": "hello", "project_id": project,
                       "session_id": session_id, "capabilities": ["video.frame", "audio.chunk"]})


def _control(kind, **fields) -> str:
    return json.dumps({"v": api.PROTOCOL_VERSION, "type": kind, **fields})


def _wait_for(predicate, *, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def _handshake(socket, project: str, session_id: str) -> dict:
    socket.send_text(_hello(project, session_id))
    event = socket.receive_json()
    assert event.get("type") == "hello.ok", f"握手失败：{event}"
    return event


def _native(instance, **kwargs):
    """注册假 provider 并让网关选中它（原生通道）。"""
    _register(instance)
    return patch.dict("os.environ", {realtime_provider.DEFAULT_PROVIDER_ENV: FAKE_NAME})


# ---- 1. 快速开关会话：引用/作用域不泄漏 ---------------------------------------

def test_rapid_session_cycles_do_not_leak_timelines_or_metric_scopes():
    client, stack = _gateway_client()
    try:
        providers = []
        for index in range(8):
            instance = R9FakeProvider()
            providers.append(instance)
            with _native(instance):
                with client.websocket_connect(f"{api_app_path()}") as socket:
                    _handshake(socket, PROJECT_A, f"cycle-{index}")
                    socket.send_bytes(_frame(index + 1))
        # 所有会话结束后：项目时间线清空、每轮 provider 被关闭、无残留会话作用域。
        assert _wait_for(lambda: realtime_bridge.active_timeline_projects() == [])
        snapshot = realtime_bridge.metrics_snapshot()
        assert all(p.closed for p in providers), "有 provider 未随会话关闭"
        leftover = [sid for sid in snapshot.get("sessions", {}) if sid.startswith("cycle-")]
        assert leftover == [], f"指标会话作用域未回收（无界增长）：{leftover}"
        assert snapshot["global"]["counters"].get(metrics.CONNECTIONS, 0) == 8.0
    finally:
        for p in stack:
            p.stop()


def api_app_path() -> str:
    return "/api/vision/live-stream?project_id=" + PROJECT_A


# ---- 2. provider.close() 外抛时收尾仍要落地 -----------------------------------

def test_provider_close_exception_still_releases_timeline_and_metric_scope():
    client, stack = _gateway_client()
    try:
        instance = R9FakeProvider(raise_on="close")
        with _native(instance):
            with client.websocket_connect(api_app_path()) as socket:
                _handshake(socket, PROJECT_A, "boom-close")
                socket.send_bytes(_frame(1))
        # 即使 close 抛错，会话作用域指标仍被回收、时间线仍被释放（收尾异常不能阻断清理）。
        assert _wait_for(lambda: realtime_bridge.active_timeline_projects() == [], timeout=10.0)
        snapshot = realtime_bridge.metrics_snapshot()
        assert "boom-close" not in snapshot.get("sessions", {}), "close 抛错导致指标作用域泄漏"
    finally:
        for p in stack:
            p.stop()


# ---- 3. 原生模式超限音频不得转发给 provider -----------------------------------

def test_oversized_audio_is_rejected_before_reaching_native_provider():
    client, stack = _gateway_client()
    try:
        instance = R9FakeProvider()
        oversized = b"\x00" * (api._LIVE_VISION_MAX_BYTES + 1)
        with _native(instance):
            with client.websocket_connect(api_app_path()) as socket:
                hello = _handshake(socket, PROJECT_A, "big-audio")
                assert hello["mode"] == "native-realtime"
                socket.send_bytes(_audio(1, oversized))
                rejected = socket.receive_json()
        assert rejected["type"] == "error" and rejected["code"] == "frame_too_large"
        assert instance.audio == [], "超限音频仍被转发进了 provider"
    finally:
        for p in stack:
            p.stop()


# ---- 4. send_frame 返回 False 时帧回落到抽帧，不静默丢弃 ----------------------

def test_send_frame_falsy_falls_back_to_sampled_slot():
    client, stack = _gateway_client()
    try:
        instance = R9FakeProvider(send_frame_ok=False)
        with _native(instance):
            with client.websocket_connect(api_app_path()) as socket:
                _handshake(socket, PROJECT_A, "fallback")
                socket.send_bytes(_frame(1))
                # provider 没收下这帧 → 网关必须走抽帧产出 video.observation，而不是丢掉。
                observation = socket.receive_json()
        assert observation["type"] == "video.observation"
        assert observation["ok"] is True
        assert instance.frames == [], "send_frame 返回 False 却仍记为已转发"
    finally:
        for p in stack:
            p.stop()


# ---- 5. 跨项目时间线隔离 -----------------------------------------------------

def test_status_view_never_exposes_another_projects_timeline():
    client, stack = _gateway_client()
    try:
        instance_a = R9FakeProvider()
        with _native(instance_a):
            with client.websocket_connect("/api/vision/live-stream?project_id=" + PROJECT_A) as socket:
                _handshake(socket, PROJECT_A, "iso-a")
                socket.send_bytes(_frame(1))
                socket.send_text(_control("session.close"))
                socket.receive_json()
        # 项目 B 的状态视图：能列出存在过的项目 id，但 B 的时间线条目必须为空（不含 A 的帧）。
        snapshot = realtime_bridge.status_snapshot(PROJECT_B)
        b_timeline = snapshot["timeline"] or {}
        entries = b_timeline.get("entries", [])
        assert all(item.get("project_id", PROJECT_B) in (PROJECT_B, None) for item in entries)
        b_sequences = [item.get("sequence") for item in entries]
        assert 1 not in b_sequences, f"项目 B 时间线混入了 A 的帧条目：{entries}"
    finally:
        for p in stack:
            p.stop()


# ---- 6. 已知缺口：网关未守卫 provider 的外抛方法 ------------------------------

def test_known_gap_interrupt_is_not_guarded_and_kills_the_session():
    """已知缺口（已上报 R9/网关）：`cancel` 分支的 `bridge.interrupt()` 无 try 守卫。

    provider 基类契约写明「每个方法都应 fail-closed，返回 False 而非外抛」，且 `start()`
    与 pump 循环都加了异常守卫；但 `interrupt()`/`send_frame()`/`take_audio()` 三处调用没有。
    一个不守契约（网络抖动即抛）的适配器在用户抢话时会**击穿 receive 循环、拖垮整条会话**。
    这里记录**当前**行为以便可见；网关加守卫后请把断言翻转为「cancel.ok 仍返回、会话存活」。
    """
    client, stack = _gateway_client()
    try:
        instance = R9FakeProvider(raise_on="interrupt")
        with _native(instance):
            with client.websocket_connect(api_app_path()) as socket:
                _handshake(socket, PROJECT_A, "break")
                socket.send_bytes(_control("cancel", reason="用户抢话"))
                # 当前：interrupt 外抛 → 连接被服务端异常终止，收不到 cancel.ok。
                killed = False
                try:
                    reply = socket.receive_json()
                    killed = reply.get("type") != "cancel.ok"
                except Exception:
                    killed = True
        assert killed, (
            "网关已守卫 bridge.interrupt()，请翻转本测试为『cancel.ok 正常返回且会话存活』")
    finally:
        for p in stack:
            p.stop()
