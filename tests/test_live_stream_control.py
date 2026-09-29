"""AI-A/AI-B：前端媒体模块 liveStreamControl.ts 的行为契约测试（只读实现）。

沿用仓库既有约定（`test_realtime_protocol_contract.py` 直接读前端源码）：用项目自己的
typescript 编译器把纯逻辑模块编译成 ESM，再由 node 执行断言脚本。只验证三件事：

1. R1 自适应：变慢/缓冲升高 → 逐级降频并压缩分辨率/码率；持续健康 → 逐级恢复；
   服务端 throttled/retry_after 抬底；档位区间有界。
2. R6 状态归类：正在看/正在回答/连接中/已断线/已暂停/未开始 的判定与标签齐备。
3. 在飞帧账本：端到端延迟计算、较新帧回帧即取代更旧帧、积压年龄检测。

node 或前端依赖缺失时整个文件跳过（与浏览器联验测试同样的降级方式）。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "frontend" / "src" / "workbench" / "liveStreamControl.ts"
TSC = ROOT / "frontend" / "node_modules" / "typescript" / "bin" / "tsc"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    not (MODULE.exists() and TSC.exists() and NODE),
    reason="node / 前端 typescript 工具链不可用，跳过前端逻辑行为测试",
)

_HARNESS = """\
import assert from 'node:assert';
import { createAdaptiveSender, classifyLivePhase, LIVE_PHASE_LABELS, createInFlightLedger } from './liveStreamControl.js';

const now = () => Date.now();

// ---- R1 自适应发送 -----------------------------------------------------------
const a = createAdaptiveSender();
const p0 = a.plan();
assert.strictEqual(p0.intervalMs, 500, '默认档位应与接入前固定行为一致（500ms）');
assert.strictEqual(p0.maxEdge, 1280);
assert.ok(Math.abs(p0.quality - 0.8) < 1e-9);

// 单次高延迟即降一级
const p1 = a.feed({ bufferedAmount: 0, latencyMs: 9000 });
assert.ok(p1.intervalMs > 500 && p1.maxEdge < 1280, '模型变慢必须降频并压缩分辨率: ' + JSON.stringify(p1));

// 缓冲堆积也降压
a.reset();
assert.strictEqual(a.feed({ bufferedAmount: 400_000 }).intervalMs > 500, true);

// 连续健康样本逐级恢复，未攒够不回升
a.reset();
a.feed({ bufferedAmount: 500_000 });            // 降一级
const midPlan = a.plan();
assert.strictEqual(a.feed({ bufferedAmount: 1000, latencyMs: 900 }).intervalMs, midPlan.intervalMs, '单个健康样本不应立即回升');
assert.strictEqual(a.feed({ bufferedAmount: 1000, latencyMs: 900 }).intervalMs, midPlan.intervalMs);
assert.strictEqual(a.feed({ bufferedAmount: 1000, latencyMs: 900 }).intervalMs, midPlan.intervalMs);
const back = a.feed({ bufferedAmount: 1000, latencyMs: 900 });
assert.strictEqual(back.intervalMs, 500, '连续 4 个健康样本后应恢复一档');

// 极端压力一路降到最底档，且永不越界
const floor = createAdaptiveSender();
let last = floor.plan();
for (let i = 0; i < 12; i++) {
  last = floor.feed({ bufferedAmount: 3_000_000, latencyMs: 60_000, encodeOverrun: true });
  assert.ok(last.intervalMs >= 300 && last.intervalMs <= 8000, '间隔必须落在有界档位表: ' + last.intervalMs);
  assert.ok(last.maxEdge >= 480 && last.quality >= 0.5, '分辨率/码率必须保底: ' + JSON.stringify(last));
}
assert.strictEqual(last.intervalMs, 4000, '持续压力应收敛到最保守档 4s');

// 服务端明确限流：retry_after 直接抬到间隔下限
const throttled = createAdaptiveSender();
const tp = throttled.feed({ bufferedAmount: 0, throttled: true, retryAfterS: 3 });
assert.ok(tp.intervalMs >= 3000, 'retry_after 3s 期间不得发新帧: ' + tp.intervalMs);
const capped = createAdaptiveSender();
assert.ok(capped.feed({ bufferedAmount: 0, throttled: true, retryAfterS: 9999 }).intervalMs <= 8000, '退避上限必须封顶 8s');

// encodeOverrun（采集快于发送）单独构成压力
const overrun = createAdaptiveSender();
assert.ok(overrun.feed({ bufferedAmount: 0, encodeOverrun: true }).intervalMs > 500);

// ---- R6 状态归类 -------------------------------------------------------------
const base = { active: true, socketState: 'open', paused: false, reconnectScheduled: false, lastObservationAgeS: null };
assert.strictEqual(classifyLivePhase({ ...base, active: false }), 'idle');
assert.strictEqual(classifyLivePhase({ ...base, paused: true }), 'paused');
assert.strictEqual(classifyLivePhase({ ...base, socketState: 'closed' }), 'offline');
assert.strictEqual(classifyLivePhase({ ...base, socketState: 'closed', reconnectScheduled: true }), 'connecting');
assert.strictEqual(classifyLivePhase({ ...base, lastObservationAgeS: 12 }), 'viewing');
assert.strictEqual(classifyLivePhase({ ...base, lastObservationAgeS: 3 }), 'answering');
for (const key of ['idle', 'connecting', 'viewing', 'answering', 'paused', 'offline']) {
  assert.ok(LIVE_PHASE_LABELS[key] && LIVE_PHASE_LABELS[key].length > 0, '每个相位都要有中文标签: ' + key);
}
// 断线优先于「正在回答」：旧观察不能粉饰当前连接
assert.strictEqual(classifyLivePhase({ ...base, socketState: 'closed', lastObservationAgeS: 1 }), 'offline');

// ---- 在飞帧账本 ---------------------------------------------------------------
const ledger = createInFlightLedger();
const t0 = now();
ledger.onSent(1, t0 - 1000, t0);
ledger.onSent(2, t0 - 500, t0 + 100);
ledger.onSent(3, t0, t0 + 200);
assert.strictEqual(ledger.pendingCount(), 3);
assert.ok(ledger.oldestPendingAge(t0 + 1200) >= 1200, '积压年龄应能从最老在飞帧读出');
const latency = ledger.onObserved(3, t0 + 800);
assert.ok(latency && latency.e2eMs >= 800, '端到端延迟 = 观察返回 - 采集时刻: ' + JSON.stringify(latency));
assert.strictEqual(ledger.pendingCount(), 0, '背压只处理最新帧：3 号回帧后 1/2 号视为被取代');
assert.strictEqual(ledger.onObserved(9, t0), null, '未登记序号不得编造延迟');
ledger.onSent(4, now(), now()); ledger.clear();
assert.strictEqual(ledger.pendingCount(), 0, 'clear 后不得残留（会话重连卫生）');

console.log('OK ' + JSON.stringify({ checks: 28 }));
"""


@pytest.fixture(scope="module")
def compiled_module(tmp_path_factory) -> Path:
    out_dir = tmp_path_factory.mktemp("live_stream_control")
    compile = subprocess.run(
        [NODE, str(TSC), str(MODULE), "--target", "es2020", "--module", "esnext",
         "--moduleResolution", "node", "--strict", "--outDir", str(out_dir)],
        capture_output=True, text=True,
    )
    if compile.returncode != 0:
        pytest.fail(f"liveStreamControl.ts 编译失败：\n{compile.stdout}\n{compile.stderr}")
    return out_dir


def _write_package(dir_path: Path) -> None:
    (dir_path / "package.json").write_text('{"type": "module"}', encoding="utf-8")


def test_adaptive_sender_and_phase_logic_behave_as_contracted(compiled_module: Path):
    _write_package(compiled_module)
    harness = compiled_module / "harness.mjs"
    harness.write_text(_HARNESS, encoding="utf-8")
    run = subprocess.run([NODE, str(harness)], capture_output=True, text=True)
    assert run.returncode == 0, (
        f"前端自适应/相位/账本行为契约失败：\n{run.stdout}\n{run.stderr}")
    assert run.stdout.startswith("OK"), run.stdout


def test_module_exports_stay_pure_for_browserless_execution(compiled_module: Path):
    """模块必须保持无浏览器依赖的纯逻辑，否则本文件的执行式契约会退化成正则。"""
    source = MODULE.read_text(encoding="utf-8")
    for forbidden in ("import Vue", "from 'vue'", "WebSocket(", "document.", "window."):
        assert forbidden not in source, f"liveStreamControl.ts 混入了浏览器/Vue 依赖：{forbidden}"


def test_cockpit_wiring_uses_the_control_module():
    """接线回归：AutonomousCockpit 必须真的消费自适应计划与相位，而不是留着魔法数字。"""
    cockpit = (ROOT / "frontend" / "src" / "workbench" / "components" / "AutonomousCockpit.vue")
    source = cockpit.read_text(encoding="utf-8")
    assert "createAdaptiveSender" in source and "liveAdaptive.plan()" in source
    assert "classifyLivePhase" in source and "LIVE_PHASE_LABELS" in source
    assert "realtimeCancel" in source, "打断按钮必须通过 R0 的 cancel 控制包实现"
    assert "1280 / video.videoWidth, 720" not in source, "固定 1280/720 采集应已被自适应档位取代"
