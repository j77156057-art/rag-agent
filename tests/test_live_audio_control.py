"""语音闭环纯逻辑（`liveAudioControl.ts`）的行为契约测试（执行式，只读实现）。

沿用本仓库既定套路：项目自带 tsc 编译 → node 真跑断言（见 `test_live_stream_control.py`）。
钉死的是 R2 的数学与状态机——采集重采样、int16le 封字节、能量 VAD 的
idle→speech→tail→idle 时序，尤其 AI-D 真机结论的契约面：

* **停止说话 ≠ 停止推流**：说完（ended）后进入 tail，`sending` 仍必须为 True，
  直到整 1 秒静音尾推完（tailDone）才允许停——这条写反就是"转写完整却零回复"事故；
* 抢话（aiSpeaking 时起说）必须报 bargeIn，接线层据此停播+cancel；
* audio.chunk 包封沿用 R0 媒体包头（v/type/sequence/captured_at + '\\n' + 载荷）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "frontend" / "src" / "workbench" / "liveAudioControl.ts"
TSC = ROOT / "frontend" / "node_modules" / "typescript" / "bin" / "tsc"
NODE = __import__("shutil").which("node")

pytestmark = pytest.mark.skipif(
    not (MODULE.exists() and TSC.exists() and NODE),
    reason="node / 前端 typescript 工具链不可用，跳过前端逻辑行为测试",
)

_HARNESS = r"""
import assert from 'node:assert';
import { clampInt16, floatToInt16Le, resample, frameRms,
  createVoiceGate, createChunkPump, encodeAudioPacket,
  resolveLiveModelAudioFormat, TARGET_SAMPLE_RATE, CHUNK_MS } from './liveAudioControl.js';

// ---- 采集数学 ----------------------------------------------------------------
assert.strictEqual(clampInt16(1), 32767);
assert.strictEqual(clampInt16(-1), -32767, '对称满量程映射（×32767）：-1 → -32767');
assert.strictEqual(clampInt16(-2), -32768, '低于 -1 削底到 -32768');
assert.strictEqual(clampInt16(2), 32767, '超出满量程必须削顶而不是溢出');
assert.strictEqual(clampInt16(0), 0);

// ---- model.audio wire 格式 --------------------------------------------------
const omniAudio = resolveLiveModelAudioFormat('pcm24');
assert.deepStrictEqual(omniAudio, { sampleRate: 24000, bitsPerSample: 16, source: 'wire_encoding' },
  'DashScope pcm24 是 24kHz 的 PCM16，不是 24-bit 样本');
const explicitAudio = resolveLiveModelAudioFormat('pcm24', '16000');
assert.deepStrictEqual(explicitAudio, { sampleRate: 16000, bitsPerSample: 16, source: 'wire_sample_rate' },
  '未来显式 sample_rate 应覆盖格式名');
assert.strictEqual(resolveLiveModelAudioFormat('opus'), null,
  '未知编码必须拒播，不能静默按 PCM 播放');
assert.strictEqual(resolveLiveModelAudioFormat('pcm16'), null,
  '只有 pcm16 没有采样率时不能把输入格式猜成输出格式');
assert.strictEqual(resolveLiveModelAudioFormat(undefined), null,
  '缺失编码必须拒播，避免采样率漂移');

const bytes = floatToInt16Le(new Float32Array([1, -1, 0]));
assert.deepStrictEqual([...bytes], [0xff, 0x7f, 0x01, 0x80, 0x00, 0x00],
  '必须是小端 int16：+1 → ff 7f，-1 → 01 80');

const down = resample(new Float32Array([0.5, 0.5, 0.5, 0.5]), 32000, 16000);
assert.strictEqual(down.length, 2, '32k→16k 长度减半');
assert.ok(Math.abs(down[0] - 0.5) < 1e-6, '常数信号重采样后仍为常数');
const same = resample(new Float32Array([0.25, 0.75]), 16000, 16000);
assert.deepStrictEqual([...same], [0.25, 0.75], '同采样率是保真拷贝');
const ramp = resample(new Float32Array([0, 0.2, 0.4, 0.6, 0.8, 1.0]), 16000, 8000);
assert.ok(ramp.length === 3 && ramp[2] > ramp[0], '降采样保持单调趋势');

assert.strictEqual(frameRms(new Float32Array(0)), 0, '空帧不得 NaN');
const rmsHalf = frameRms(new Float32Array([0.5, -0.5]));
assert.ok(Math.abs(rmsHalf - 0.5) < 1e-9);

// ---- 能量 VAD 状态机 ---------------------------------------------------------
const gate = createVoiceGate({ startRms: 0.02, endRms: 0.012, speechHoldMs: 300, tailMs: 1000, startFrames: 2 });
let t = 0;
let s = gate.feed(0.05, t);
assert.strictEqual(s.started, false, '单帧超限不去抖立即起说');
s = gate.feed(0.05, t += 20);
assert.strictEqual(s.started, true, '连续 2 帧超限应起说');
assert.strictEqual(s.state, 'speech');
assert.strictEqual(s.sending, true);
assert.strictEqual(s.bargeIn, false, 'AI 没在说话时不算抢话');

// 说 500ms 后安静：300ms 迟滞内仍是 speech
for (let i = 0; i < 25; i++) s = gate.feed(0.05, t += 20);
assert.strictEqual(s.state, 'speech');
for (let i = 0; i < 10; i++) s = gate.feed(0.0, t += 20);   // 200ms 安静
assert.strictEqual(s.ended, false, '200ms 安静不足以判说完');
let endSignal = null;
for (let i = 0; i < 8 && !endSignal; i++) {
  const sig = gate.feed(0.0, t += 20);
  if (sig.ended) endSignal = sig;
}
assert.ok(endSignal, '安静满 speechHoldMs 必须判说完');
s = gate.feed(0.0, t += 20);
assert.strictEqual(s.state, 'tail');

// 核心契约：说完进 tail 后仍必须推流（静音尾），直到 1 秒推满
assert.strictEqual(s.sending, true, '停止说话 ≠ 停止推流：tail 期间必须继续发');
for (let i = 0; i < 45; i++) { s = gate.feed(0.0, t += 20); assert.strictEqual(s.sending, true, '静音尾未满 1 秒不得停'); }
let doneSignal = null;
for (let i = 0; i < 20 && !doneSignal; i++) {
  const sig = gate.feed(0.0, t += 20);
  if (sig.tailDone) doneSignal = sig;
}
assert.ok(doneSignal, '静音尾推满 1 秒必须报 tailDone');
assert.strictEqual(doneSignal.state, 'idle');
assert.strictEqual(doneSignal.sending, false, '静音尾推完后才允许停流');

// 抢话：AI 在说时用户开口 → bargeIn（tail 期重新开口同样算起说）
const talking = createVoiceGate();
assert.strictEqual(talking.feed(0.05, 0, true).started, false);
const interrupt = talking.feed(0.05, 20, true);
assert.strictEqual(interrupt.started, true);
assert.strictEqual(interrupt.bargeIn, true, 'AI 说话期间起说必须报抢话');
for (let i = 0; i < 20; i++) talking.feed(0.05, 40 + i * 20, true);
for (let i = 0; i < 20; i++) talking.feed(0.0, 440 + i * 20, true);  // 说完进 tail
assert.strictEqual(talking.feed(0.05, 840, true).started, true, 'tail 中再开口应重入 speech');
talking.reset();
assert.strictEqual(talking.state(), 'idle', 'reset 后回 idle（关麦/断线复用）');

// ---- 定长分片泵 ---------------------------------------------------------------
const pump = createChunkPump();
assert.strictEqual(pump.chunkSamples(), 1600, '100ms @16k = 1600 样本');
assert.deepStrictEqual(pump.push(new Float32Array(500)), [], '不足一片不得产出碎片');
const out = pump.push(new Float32Array(1200));
assert.strictEqual(out.length, 1);
assert.strictEqual(out[0].length, 3200, '一片 = 1600×2 字节 int16le');
assert.strictEqual(pump.pending(), 100);
const silence = pump.silenceChunk();
assert.strictEqual(silence.length, 3200);
assert.ok(silence.every(byte => byte === 0), '静音尾必须是全零片');
pump.reset();
assert.strictEqual(pump.pending(), 0);

// ---- audio.chunk 包封（沿用 R0 媒体包头） --------------------------------------
const packet = encodeAudioPacket({ version: 1, sequence: 42, capturedAt: 1_700_000_000_000,
  payload: new Uint8Array([1, 2, 3, 4]) });
const view = new Uint8Array(packet);
const split = view.indexOf(0x0a);
const header = JSON.parse(new TextDecoder().decode(view.subarray(0, split)));
assert.strictEqual(header.v, 1);
assert.strictEqual(header.type, 'audio.chunk');
assert.strictEqual(header.sequence, 42);
assert.strictEqual(header.captured_at, 1_700_000_000_000);
assert.deepStrictEqual([...view.subarray(split + 1)], [1, 2, 3, 4], '载荷必须逐字节原样');
assert.throws(() => encodeAudioPacket({ version: 1, sequence: -1, capturedAt: 1, payload: new Uint8Array([1]) }));
assert.throws(() => encodeAudioPacket({ version: 1, sequence: 1, capturedAt: 1, payload: new Uint8Array(0) }));
assert.throws(() => encodeAudioPacket({ version: 1, sequence: 1.5, capturedAt: 1, payload: new Uint8Array([1]) }));
assert.throws(() => encodeAudioPacket({ version: 1, sequence: 1, capturedAt: 0, payload: new Uint8Array([1]) }),
  undefined, '', 'captured_at 必须是正毫秒');

console.log('OK ' + JSON.stringify({ checks: 34 }));
"""


@pytest.fixture(scope="module")
def compiled_module(tmp_path_factory) -> Path:
    out_dir = tmp_path_factory.mktemp("live_audio_control")
    compile = subprocess.run(
        [NODE, str(TSC), str(MODULE), "--target", "es2020", "--module", "esnext",
         "--moduleResolution", "node", "--strict", "--outDir", str(out_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if compile.returncode != 0:
        pytest.fail(f"liveAudioControl.ts 编译失败：\n{compile.stdout}\n{compile.stderr}")
    (out_dir / "package.json").write_text('{"type": "module"}', encoding="utf-8")
    return out_dir


def test_audio_capture_vad_and_packeting_behave_as_contracted(compiled_module: Path):
    harness = compiled_module / "harness.mjs"
    harness.write_text(_HARNESS, encoding="utf-8")
    run = subprocess.run([NODE, str(harness)], capture_output=True, text=True,
                         encoding="utf-8", errors="replace")
    assert run.returncode == 0, (
        f"语音闭环行为契约失败：\n{run.stdout}\n{run.stderr}")
    assert run.stdout.startswith("OK"), run.stdout


def test_module_stays_browser_free(compiled_module: Path):
    source = MODULE.read_text(encoding="utf-8")
    for forbidden in ("getUserMedia", "AudioContext", "AudioWorklet", "WebSocket(",
                      "window.", "document.", "from 'vue'"):
        assert forbidden not in source, f"liveAudioControl.ts 混入浏览器依赖：{forbidden}"
