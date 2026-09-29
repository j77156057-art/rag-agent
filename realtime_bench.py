"""R10 实时性能基准工具：驱动真实网关，产出可复核的 p50/p95 与丢帧率。

任务表 R10 的验收条件是「给出 p50/p95 指标」。指标库 `agent_runtime/realtime_metrics.py`
已经把指标名（`observation_latency_ms` / `end_to_end_ms` / `frames_sent` / `frames_dropped` …）
和 `MetricsRegistry` 都定义好了，但在此之前没有任何东西真的往里写过数——本工具补的就是这一环：
用进程内夹具驱动 `api.py` 的 `/api/vision/live-stream`，把真实测量值喂进那张注册表。

**不重复造轮子**：协议编解码走 `agent_runtime/realtime_protocol.py`，指标聚合走
`realtime_metrics.MetricsRegistry`，网关是 `api.py` 里的真实处理器；本文件只负责
「发帧、配时、归因、汇总」。

## 两种模式，测的是不同的东西

* `rtt`：一帧一收（发一帧，等它的观察回来再发下一帧）。测的是**空载单帧往返**，
  即网关开销 + 模型耗时，没有排队成分。
* `stream`：按固定间隔连发，不等待。测的是**有负载时的端到端**（含排队与合并）。
  因为网关 `pending` 是单槽，模型忙时中间帧会被最新帧覆盖——这批帧就是丢帧率的来源。

## 归因方式

`video.observation` 事件会回带该帧的 `sequence` 与 `captured_at`，所以每一个观察都能
精确对回它是哪一帧，延迟和丢失都不需要估算。

## 诚实的边界（不要把这些数字当成 R12 的验收数据）

* 进程内 `TestClient` **没有真实网络**，也**没有真实模型**——`--model-ms` 只是一个
  可控的占位延时。所以：
  - `model_ms=0` 时，rtt 的 p50/p95 约等于**网关自身开销**；
  - `model_ms>0` 时，`overhead = rtt - model_ms` 才是网关开销，工具会把两者分开报。
* stream 模式的延迟**含排队时间**，是上界而非纯处理时间。
* 真实摄像头/麦克风/屏幕共享 + 真实实时模型的联验属于 R12，本工具不能替代它。

用法：
    .\\.venv\\Scripts\\python.exe realtime_bench.py                     # 两种模式都跑
    .\\.venv\\Scripts\\python.exe realtime_bench.py --model-ms 120      # 模拟慢模型，看合并
    .\\.venv\\Scripts\\python.exe realtime_bench.py --json bench.json   # 存证据
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from unittest.mock import patch

from fastapi.testclient import TestClient

import api
from agent_runtime import realtime_metrics as metrics

ENDPOINT = "/api/vision/live-stream"
PROJECT_ID = "bench-project"
SESSION_ID = "bench-session"

# 排空的最后期限（秒）。网关只处理最新帧，等"最后一帧的观察"就是等排空。
DRAIN_TIMEOUT = 30.0
PAYLOAD = b"\xff\xd8bench-jpeg\xff\xd9"  # 内容无所谓，网关不解析帧体


def _now_ms() -> int:
    return int(time.time() * 1000)


def _packet(sequence: int, captured_at: int, *, focused: bool = False) -> bytes:
    """按 v1 线格式打包一帧：JSON 头 + b"\\n" + 帧体。"""
    header = {"v": api.PROTOCOL_VERSION, "type": "video.frame",
              "sequence": sequence, "captured_at": captured_at}
    if focused:
        header["focused"] = True
    return json.dumps(header).encode("utf-8") + b"\n" + PAYLOAD


def _hello() -> str:
    return json.dumps({"v": api.PROTOCOL_VERSION, "type": "hello", "project_id": PROJECT_ID,
                       "session_id": SESSION_ID,
                       "capabilities": ["video.frame", "audio.chunk", "interrupt"]})


class _StubAnalyzer:
    """占位视觉模型：按 `model_ms` 睡固定时长。

    必须是**绑定异步方法**——`AsyncMock` 不会 await 一个"可调用实例"返回的协程，
    会把协程对象当成结果静默传下去（本仓库已踩过这个坑）。
    """

    def __init__(self, model_ms: float = 0.0) -> None:
        self.model_ms = max(0.0, float(model_ms))
        self.calls = 0

    async def analyze(self, file, previous_observation: str = "", focused_region: str = ""):
        await file.read()
        self.calls += 1
        if self.model_ms:
            await asyncio.sleep(self.model_ms / 1000.0)
        return {"ok": True, "observations": ["基准观察"], "anomalies": [],
                "audit": {"mode": "bench"}}


class _Deadline:
    """看门狗：到点还没完工就关掉 socket，让阻塞中的 receive 抛错而不是把进程挂死。"""

    def __init__(self, socket, seconds: float) -> None:
        self._socket = socket
        self._seconds = seconds
        self._done = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        if not self._done.wait(self._seconds):
            try:
                self._socket.close()
            except Exception:  # noqa: BLE001 - 收尾路径，失败无所谓
                pass

    def __enter__(self) -> "_Deadline":
        return self

    def __exit__(self, *exc) -> None:
        self._done.set()


@dataclass
class RunResult:
    mode: str
    frames: int
    model_ms: float
    observed: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    peak_backlog: int = 0
    wall_ms: float = 0.0
    side_events: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def dropped(self) -> int:
        return self.frames - self.observed

    @property
    def drop_rate(self) -> float:
        return self.dropped / self.frames if self.frames else 0.0

    @property
    def frames_per_sec(self) -> float:
        return self.observed / (self.wall_ms / 1000.0) if self.wall_ms > 0 else 0.0


@contextmanager
def gateway(analyzer: _StubAnalyzer):
    """把真实网关挂上占位模型和项目表；不修改 `api.py` 的任何实现。"""
    def get_project(project_id):
        return {"root": f"root-{project_id}"} if project_id == PROJECT_ID else None

    with patch.object(api.projects, "get_project", side_effect=get_project), \
            patch.object(api, "analyze_live_frame_ep", side_effect=analyzer.analyze):
        with TestClient(api.app) as client:
            yield client


@contextmanager
def _session(client):
    """完成 hello 的一条实时会话；退出时正常关闭，避免留下半开连接。"""
    with client.websocket_connect(f"{ENDPOINT}?project_id={PROJECT_ID}") as socket:
        socket.send_text(_hello())
        hello = socket.receive_json()
        assert hello.get("type") == "hello.ok", f"握手失败：{hello}"
        yield socket


def _receive_observation(socket, side_events: list[str]) -> dict:
    """跳过心跳/错误等旁路事件，直到拿到一条 video.observation。"""
    while True:
        event = socket.receive_json()
        kind = event.get("type")
        if kind == "video.observation":
            return event
        side_events.append(str(kind))


def run_rtt(client, *, frames: int, model_ms: float, registry) -> RunResult:
    """一帧一收：空载单帧往返延迟，无排队成分。"""
    result = RunResult(mode="rtt", frames=frames, model_ms=model_ms)
    with _session(client) as socket:
        with _Deadline(socket, DRAIN_TIMEOUT):
            started = time.perf_counter()
            for index in range(frames):
                registry.incr(metrics.FRAMES_SENT, 1, SESSION_ID)
                socket.send_bytes(_packet(index, _now_ms()))
                sent_at = time.perf_counter()
                event = _receive_observation(socket, result.side_events)
                if event.get("sequence") != index:
                    raise AssertionError(
                        f"第 {index} 帧收到的观察序号是 {event.get('sequence')}，与发出的 {index} 不符")
                elapsed = (time.perf_counter() - sent_at) * 1000.0
                result.latencies_ms.append(elapsed)
                registry.observe(metrics.OBSERVATION_LATENCY_MS, elapsed, SESSION_ID)
                result.peak_backlog = max(result.peak_backlog, 1)
            result.wall_ms = (time.perf_counter() - started) * 1000.0
    result.observed = len(result.latencies_ms)
    return result


def run_stream(client, *, frames: int, model_ms: float, interval_ms: float, registry) -> RunResult:
    """连发不等：有负载时的端到端（含排队）与丢帧率。

    读必须在**另一个线程**里做。若先发完再统一读，早期帧的观察会堆在客户端队列里空等，
    测出来的就成了"我的读取延迟"（实测 p50 683ms，纯属夹具伪影），而不是网关延迟。
    读线程在观察到达的那一刻打时间戳，延迟才是真的。
    """
    result = RunResult(mode="stream", frames=frames, model_ms=model_ms)
    last = frames - 1
    arrivals: dict[int, float] = {}
    with _session(client) as socket:
        with _Deadline(socket, DRAIN_TIMEOUT):
            stop = threading.Event()

            def reader() -> None:
                try:
                    while not stop.is_set():
                        event = socket.receive_json()
                        kind = event.get("type")
                        if kind != "video.observation":
                            result.side_events.append(str(kind))
                            continue
                        arrivals.setdefault(event.get("sequence"), time.perf_counter())
                        if event.get("sequence") == last:
                            return
                except Exception as exc:  # noqa: BLE001 - 记录而不是吞掉
                    result.errors.append(f"{type(exc).__name__}: {exc}")

            thread = threading.Thread(target=reader, daemon=True)
            thread.start()

            sent_at: dict[int, float] = {}
            started = 0.0
            for index in range(frames):
                registry.incr(metrics.FRAMES_SENT, 1, SESSION_ID)
                sent_at[index] = time.perf_counter()
                if index == 0:
                    started = sent_at[index]
                socket.send_bytes(_packet(index, _now_ms()))
                # 真正的排队深度：已发出去但还没等到观察的帧数。
                result.peak_backlog = max(result.peak_backlog, index + 1 - len(arrivals))
                if interval_ms:
                    time.sleep(interval_ms / 1000.0)

            # 网关只保留最新帧，"最后一帧的观察"就是排空信号：它之后不会再产生 pending，
            # 先发的存活帧必然已经发过观察了。
            thread.join(DRAIN_TIMEOUT)
            stop.set()
            finished = max(arrivals.values(), default=sent_at[last])
            result.wall_ms = (finished - started) * 1000.0

            for sequence in sorted(arrivals):
                if sequence in sent_at:
                    elapsed = (arrivals[sequence] - sent_at[sequence]) * 1000.0
                    result.latencies_ms.append(elapsed)
                    registry.observe(metrics.END_TO_END_MS, elapsed, SESSION_ID)

    result.observed = len(result.latencies_ms)
    registry.incr(metrics.FRAMES_DROPPED, result.dropped, SESSION_ID)
    registry.gauge(metrics.QUEUE_DEPTH, float(result.peak_backlog), SESSION_ID)
    return result


def _session_histogram(snapshot: dict, name: str) -> dict:
    """会话作用域里的某个直方图；没采到样本时返回空壳而不是抛 KeyError。"""
    return snapshot.get("sessions", {}).get(SESSION_ID, {}).get("histograms", {}).get(
        name, {"count": 0, "window": 0, "min": None, "max": None, "p50": None, "p95": None})


def report(results: list[RunResult], registry, model_calls: int) -> dict:
    snapshot = registry.snapshot()
    payload = {"global": snapshot["global"], "sessions": snapshot["sessions"],
               "model_calls": model_calls, "runs": []}
    for result in results:
        name = metrics.OBSERVATION_LATENCY_MS if result.mode == "rtt" else metrics.END_TO_END_MS
        histogram = _session_histogram(snapshot, name)
        entry = {
            "mode": result.mode,
            "frames": result.frames,
            "observed": result.observed,
            "dropped": result.dropped,
            "drop_rate": round(result.drop_rate, 4),
            "model_ms": result.model_ms,
            "wall_ms": round(result.wall_ms, 1),
            "frames_per_sec": round(result.frames_per_sec, 1),
            "peak_backlog": result.peak_backlog,
            "latency_metric": name,
            "latency_ms": {"count": histogram["count"], "p50": histogram["p50"],
                           "p95": histogram["p95"]},
            "side_events": sorted(set(result.side_events)),
            "errors": result.errors,
        }
        # 模拟模型耗时可以被精确扣除，所以网关自身开销是可报的；真实模型下扣不掉。
        if result.mode == "rtt" and result.model_ms and histogram["p50"] is not None:
            entry["gateway_overhead_ms"] = {
                "p50": round(histogram["p50"] - result.model_ms, 2),
                "p95": round((histogram["p95"] or histogram["p50"]) - result.model_ms, 2),
            }
        payload["runs"].append(entry)
    return payload


def _print(payload: dict) -> None:
    print(f"{'模式':<8}{'发送':>6}{'观察':>6}{'丢弃':>6}{'丢帧率':>9}"
          f"{'p50ms':>9}{'p95ms':>9}{'峰值积压':>10}{'帧/秒':>9}")
    for run in payload["runs"]:
        p50 = run["latency_ms"]["p50"]
        p95 = run["latency_ms"]["p95"]
        print(f"{run['mode']:<8}{run['frames']:>6}{run['observed']:>6}{run['dropped']:>6}"
              f"{run['drop_rate'] * 100:>8.1f}%"
              f"{'n/a' if p50 is None else format(p50, '.1f'):>9}"
              f"{'n/a' if p95 is None else format(p95, '.1f'):>9}"
              f"{run['peak_backlog']:>10}{run['frames_per_sec']:>9.1f}")
        if "gateway_overhead_ms" in run:
            overhead = run["gateway_overhead_ms"]
            print(f"         └ 扣除模拟模型 {run['model_ms']:.0f}ms 后的网关开销："
                  f"p50 {overhead['p50']}ms / p95 {overhead['p95']}ms")
        for run_error in run["errors"]:
            print(f"         ! {run_error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="R10 实时网关性能基准（进程内夹具，非真机联验）")
    parser.add_argument("--mode", choices=("rtt", "stream", "both"), default="both")
    parser.add_argument("--frames", type=int, default=60, help="每种模式发送的帧数")
    parser.add_argument("--model-ms", type=float, default=0.0,
                        help="占位模型的单帧耗时；调大可观察网关的中间帧合并")
    parser.add_argument("--interval-ms", type=float, default=33.0,
                        help="stream 模式的发帧间隔（33ms ≈ 30fps）")
    parser.add_argument("--window", type=int, default=metrics.DEFAULT_WINDOW,
                        help="指标环形缓冲窗口；样本多于窗口时只有最近的会被计入百分位")
    parser.add_argument("--json", dest="json_path", default="", help="把 JSON 证据写到该路径")
    args = parser.parse_args(argv)

    if args.frames < 1:
        parser.error("--frames 至少为 1")
    if args.frames > args.window:
        parser.error(f"--frames({args.frames}) 不得超过 --window({args.window})，"
                     "否则较早的样本会被环形缓冲挤掉，百分位不再代表整轮")

    registry = metrics.MetricsRegistry(window=max(args.window, 2))
    analyzer = _StubAnalyzer(args.model_ms)
    results: list[RunResult] = []

    with gateway(analyzer) as client:
        if args.mode in ("rtt", "both"):
            results.append(run_rtt(client, frames=args.frames,
                                   model_ms=args.model_ms, registry=registry))
        if args.mode in ("stream", "both"):
            results.append(run_stream(client, frames=args.frames, model_ms=args.model_ms,
                                      interval_ms=args.interval_ms, registry=registry))

    payload = report(results, registry, analyzer.calls)
    _print(payload)
    total = sum(run.frames for run in results)
    print(f"\n占位模型实际被调用 {analyzer.calls} 次 / 共发 {total} 帧"
          f"（差值 {total - analyzer.calls} 即网关合并掉的帧）")
    print("注意：进程内夹具，无真实网络与真实模型，数字只反映网关自身开销；真机联验属 R12。")

    if args.json_path:
        with open(args.json_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"证据已写入 {args.json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
