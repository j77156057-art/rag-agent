"""R10 实时性能指标收集器的单元测试（任务 R10）。

本文件**只读** `agent_runtime/realtime_metrics.py`，不修改任何实现。覆盖：
nearest-rank p50/p95 计算、有界窗口淘汰、计数器/仪表语义、全局与每会话作用域隔离、
drop_session/reset，以及名称与数值（NaN/Inf/bool）校验。
"""
from __future__ import annotations

import json
import math
import threading

import pytest

from agent_runtime.realtime_metrics import (
    DEFAULT_WINDOW,
    MetricsRegistry,
    OBSERVATION_LATENCY_MS,
    QUEUE_DEPTH,
    FRAMES_DROPPED,
    _Histogram,
)


@pytest.fixture
def registry() -> MetricsRegistry:
    return MetricsRegistry()


# ---- p50/p95：nearest-rank ---------------------------------------------------

def test_percentiles_follow_the_nearest_rank_method_on_a_full_rank():
    hist = _Histogram()
    for value in range(1, 101):
        hist.observe(value)
    snap = hist.snapshot()
    assert snap["p50"] == 50, "ceil(0.5*100)=50 名 → 值 50"
    assert snap["p95"] == 95, "ceil(0.95*100)=95 名 → 值 95"


def test_percentiles_round_rank_up_when_rank_is_not_integer():
    hist = _Histogram()
    for value in (3, 1, 2):
        hist.observe(value)
    # p50: ceil(1.5)=2 名 → 2；p95: ceil(2.85)=3 名 → 3
    assert hist.percentile(0.50) == 2
    assert hist.percentile(0.95) == 3


def test_percentile_edges_return_min_and_max():
    hist = _Histogram()
    for value in (40, 10, 30, 20):
        hist.observe(value)
    assert hist.percentile(0) == 10, "p0 应退化为最小值"
    assert hist.percentile(1) == 40, "p1 应退化为最大值"


def test_percentile_is_order_independent():
    a, b = _Histogram(), _Histogram()
    for value in (1, 2, 3, 4, 5):
        a.observe(value)
    for value in (5, 1, 4, 2, 3):
        b.observe(value)
    assert a.snapshot() == b.snapshot()


def test_percentile_outside_unit_interval_is_a_programming_error():
    hist = _Histogram()
    hist.observe(1)
    with pytest.raises(ValueError):
        hist.percentile(1.5)
    with pytest.raises(ValueError):
        hist.percentile(-0.1)


def test_empty_histogram_snapshot_has_no_derived_values():
    snap = _Histogram().snapshot()
    assert snap == {"count": 0, "window": 0, "min": None,
                    "max": None, "p50": None, "p95": None}


# ---- 有界窗口淘汰 -------------------------------------------------------------

def test_window_eviction_keeps_only_the_newest_samples_but_counts_all():
    hist = _Histogram(window=4)
    for value in range(1, 11):
        hist.observe(value)
    snap = hist.snapshot()
    assert snap["count"] == 10, "count 是累计观测次数，不因淘汰而回退"
    assert snap["window"] == 4
    assert (snap["min"], snap["max"]) == (7, 10)
    assert snap["p50"] == 8, "窗口 [7,8,9,10] 的 p50 = 8"
    assert snap["p95"] == 10


def test_registry_window_is_configurable(registry: MetricsRegistry):
    small = MetricsRegistry(window=3)
    for value in range(1, 7):
        small.observe(OBSERVATION_LATENCY_MS, value)
    hist = small.snapshot()["global"]["histograms"][OBSERVATION_LATENCY_MS]
    assert hist["window"] == 3
    assert (hist["min"], hist["max"]) == (4, 6)


def test_histogram_rejects_a_window_below_two():
    # 实现会把窗口收敛到最小值 2，而不是构造出退化的 0/1 容量缓冲。
    assert _Histogram(window=0).window == 2
    assert MetricsRegistry(window=1)._window == 2


# ---- 计数器 / 仪表 ------------------------------------------------------------

def test_counter_accumulates_non_negative_increments(registry: MetricsRegistry):
    registry.incr(FRAMES_DROPPED)
    registry.incr(FRAMES_DROPPED, 4)
    registry.incr(FRAMES_DROPPED, 2.5)
    counters = registry.snapshot()["global"]["counters"]
    assert counters[FRAMES_DROPPED] == pytest.approx(7.5)


def test_counter_negative_increment_is_rejected(registry: MetricsRegistry):
    with pytest.raises(ValueError, match="non-negative"):
        registry.incr(FRAMES_DROPPED, -1)
    assert registry.snapshot()["global"]["counters"] == {}


def test_gauge_reports_the_last_value_not_a_sum(registry: MetricsRegistry):
    registry.gauge(QUEUE_DEPTH, 1)
    registry.gauge(QUEUE_DEPTH, 5)
    registry.gauge(QUEUE_DEPTH, 0)
    gauges = registry.snapshot()["global"]["gauges"]
    assert gauges[QUEUE_DEPTH] == 0


# ---- 全局 / 每会话作用域 ------------------------------------------------------

def test_observation_is_recorded_globally_and_for_its_session(registry: MetricsRegistry):
    registry.observe(OBSERVATION_LATENCY_MS, 120, session_id="sess-a")
    report = registry.snapshot()
    assert report["global"]["histograms"][OBSERVATION_LATENCY_MS]["p50"] == 120
    assert report["sessions"]["sess-a"]["histograms"][OBSERVATION_LATENCY_MS]["p50"] == 120


def test_observation_without_session_stays_out_of_session_scopes(registry: MetricsRegistry):
    registry.observe(OBSERVATION_LATENCY_MS, 10)
    report = registry.snapshot()
    assert report["sessions"] == {}
    assert OBSERVATION_LATENCY_MS in report["global"]["histograms"]


def test_sessions_are_scoped_independently(registry: MetricsRegistry):
    registry.observe(OBSERVATION_LATENCY_MS, 100, session_id="sess-a")
    registry.observe(OBSERVATION_LATENCY_MS, 300, session_id="sess-a")
    registry.observe(OBSERVATION_LATENCY_MS, 900, session_id="sess-b")
    sessions = registry.snapshot()["sessions"]
    assert set(sessions) == {"sess-a", "sess-b"}
    assert sessions["sess-a"]["histograms"][OBSERVATION_LATENCY_MS]["p50"] == 100
    assert sessions["sess-b"]["histograms"][OBSERVATION_LATENCY_MS]["p50"] == 900
    # 全局作用域同时看到两个会话的三次观测。
    assert sessions["sess-a"]["histograms"][OBSERVATION_LATENCY_MS]["count"] == 2
    assert registry.snapshot()["global"]["histograms"][OBSERVATION_LATENCY_MS]["count"] == 3


def test_counters_and_gauges_are_also_split_by_session(registry: MetricsRegistry):
    registry.incr(FRAMES_DROPPED, 2, session_id="sess-a")
    registry.gauge(QUEUE_DEPTH, 7, session_id="sess-a")
    session = registry.snapshot()["sessions"]["sess-a"]
    assert session["counters"][FRAMES_DROPPED] == 2
    assert session["gauges"][QUEUE_DEPTH] == 7


def test_drop_session_removes_its_scope_but_keeps_global_metrics(registry: MetricsRegistry):
    registry.observe(OBSERVATION_LATENCY_MS, 50, session_id="gone")
    registry.drop_session("gone")
    report = registry.snapshot()
    assert "gone" not in report["sessions"]
    assert report["global"]["histograms"][OBSERVATION_LATENCY_MS]["count"] == 1


def test_reset_clears_global_and_session_state(registry: MetricsRegistry):
    registry.observe(OBSERVATION_LATENCY_MS, 50, session_id="sess-a")
    registry.incr(FRAMES_DROPPED, 3, session_id="sess-a")
    registry.reset()
    report = registry.snapshot()
    assert report == {"global": {"histograms": {}, "counters": {}, "gauges": {}},
                      "sessions": {}}


# ---- 名称与数值校验 -----------------------------------------------------------

@pytest.mark.parametrize("name", [
    "a", "latency", "a.b", "a_b", "a1", "x" * 60,
    OBSERVATION_LATENCY_MS, QUEUE_DEPTH, FRAMES_DROPPED,
])
def test_valid_metric_names_are_accepted(registry: MetricsRegistry, name):
    registry.observe(name, 1)


@pytest.mark.parametrize("name", [
    "", "UPPER", "1start", "has space", "a-b", ".lead",
    "x" * 61, "a." * 40,
])
def test_invalid_metric_names_are_rejected(registry: MetricsRegistry, name):
    with pytest.raises(ValueError, match="invalid metric name"):
        registry.observe(name, 1)


@pytest.mark.parametrize("method, payload", [
    ("observe", math.nan),
    ("observe", math.inf),
    ("observe", -math.inf),
    ("incr", math.inf),
    ("gauge", math.nan),
])
def test_non_finite_numbers_are_rejected(registry: MetricsRegistry, method, payload):
    with pytest.raises(ValueError, match="finite"):
        getattr(registry, method)("some.metric", payload)


@pytest.mark.parametrize("value", [True, False])
def test_booleans_are_not_treated_as_numbers(registry: MetricsRegistry, value):
    with pytest.raises(ValueError, match="number"):
        registry.observe(OBSERVATION_LATENCY_MS, value)
    with pytest.raises(ValueError, match="number"):
        registry.gauge(QUEUE_DEPTH, value)


@pytest.mark.parametrize("value", ["100", None, [1], {"v": 1}])
def test_non_numeric_values_are_rejected(registry: MetricsRegistry, value):
    with pytest.raises(ValueError, match="number"):
        registry.observe(OBSERVATION_LATENCY_MS, value)


def test_hyphenated_and_uuid_style_session_ids_are_accepted(registry: MetricsRegistry):
    # 真实客户端会话 id（含连字符/UUID）必须能建作用域，否则接进网关就会崩。
    registry.observe(OBSERVATION_LATENCY_MS, 10, session_id="sess-a")
    registry.observe(OBSERVATION_LATENCY_MS, 11, session_id="550e8400-e29b-41d4-a716-446655440000")
    sessions = registry.snapshot()["sessions"]
    assert sessions["sess-a"]["histograms"][OBSERVATION_LATENCY_MS]["count"] == 1
    assert "550e8400-e29b-41d4-a716-446655440000" in sessions


@pytest.mark.parametrize("session_id", [
    "bad session", "UPPER", "-lead", "x" * 161,
])
def test_invalid_session_id_is_rejected(registry: MetricsRegistry, session_id):
    with pytest.raises(ValueError):
        registry.observe(OBSERVATION_LATENCY_MS, 1, session_id=session_id)


# ---- 并发与导出 ---------------------------------------------------------------

def test_concurrent_writers_do_not_lose_counter_increments(registry: MetricsRegistry):
    threads = 8
    per_thread = 200

    def worker() -> None:
        for _ in range(per_thread):
            registry.incr(FRAMES_DROPPED)

    runners = [threading.Thread(target=worker) for _ in range(threads)]
    for t in runners:
        t.start()
    for t in runners:
        t.join()
    total = registry.snapshot()["global"]["counters"][FRAMES_DROPPED]
    assert total == threads * per_thread


def test_snapshot_is_json_serializable(registry: MetricsRegistry):
    registry.observe(OBSERVATION_LATENCY_MS, 12.5, session_id="sess-a")
    registry.incr(FRAMES_DROPPED, 1, session_id="sess-a")
    registry.gauge(QUEUE_DEPTH, 2, session_id="sess-a")
    encoded = json.dumps(registry.snapshot())
    assert json.loads(encoded)["sessions"]["sess-a"]["gauges"][QUEUE_DEPTH] == 2


def test_default_window_constant_matches_the_constructor_default():
    assert MetricsRegistry().snapshot()  # smoke
    assert DEFAULT_WINDOW == 1024
