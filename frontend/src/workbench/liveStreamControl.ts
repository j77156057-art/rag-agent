/**
 * R1/R6 前端媒体模块：实时视频流的自适应发送与会话状态归类。
 *
 * 不包含任何协议包字段（那在 realtimeProtocol.ts，属 R0 冻结范围）。这里全部是
 * 纯逻辑，由 AutonomousCockpit.vue 驱动，可脱离浏览器直接做行为契约测试。
 * 「正在听」属 R2 音频链路，本模块暂不产生该状态。
 */

export interface LiveSendSample {
  /** WebSocket 发送缓冲中未发出的字节数 */
  bufferedAmount: number
  /** 帧采集 → 其观察结果返回的端到端延迟 ms（仅已观测帧提供） */
  latencyMs?: number | null
  /** 服务端本帧明确限流（video.observation.throttled） */
  throttled?: boolean
  /** 服务端限流退避秒数（retry_after） */
  retryAfterS?: number | null
  /** 在飞帧滞留超过两个发送周期：编码/网络跟不上采集 */
  encodeOverrun?: boolean
}

export interface LiveSendPlan {
  /** 下一帧的发送间隔，ms */
  intervalMs: number
  /** 采集缩放的长边上限，px */
  maxEdge: number
  /** JPEG 质量，0-1 */
  quality: number
}

const INTERVALS = [300, 500, 900, 1500, 2600, 4000]
const EDGES = [1600, 1280, 960, 640, 480]
const QUALITIES = [0.85, 0.8, 0.72, 0.62, 0.5]

const DEFAULT_LEVEL = 1 // 500ms / 1280px / 0.8：与本模块接入前的固定行为一致
const PRESSURE_LATENCY_MS = 6000
const PRESSURE_BUFFER_BYTES = 250_000
const HEALTHY_LATENCY_MS = 2500
const HEALTHY_BUFFER_BYTES = 60_000
const HEALTHY_STREAK = 4 // 恢复要连续健康 4 次才升一档，避免抖动
const THROTTLE_FLOOR_MAX_MS = 8000

export interface AdaptiveSender {
  plan(): LiveSendPlan
  feed(sample: LiveSendSample): LiveSendPlan
  reset(): void
  level(): number
}

/**
 * 背压自适应：网络或模型变慢 → 逐级降频并压缩分辨率/码率；持续恢复 → 逐级升回。
 * 降级看单次压力信号即可生效；恢复需要连续健康样本。服务端 throttled/retry_after
 * 在此期间直接抬高发送间隔下限。
 */
export function createAdaptiveSender(): AdaptiveSender {
  let level = DEFAULT_LEVEL
  let healthyStreak = 0
  let throttleFloorUntil = 0

  const step = (delta: number) => { level = Math.max(0, Math.min(INTERVALS.length - 1, level + delta)) }

  return {
    level() { return level },
    plan() {
      const floor = Math.max(0, throttleFloorUntil - Date.now())
      return {
        intervalMs: Math.min(8000, Math.max(INTERVALS[level], floor)),
        maxEdge: EDGES[Math.min(level, EDGES.length - 1)],
        quality: QUALITIES[Math.min(level, QUALITIES.length - 1)],
      }
    },
    feed(sample) {
      const pressured = sample.throttled === true
        || (typeof sample.latencyMs === 'number' && sample.latencyMs >= PRESSURE_LATENCY_MS)
        || sample.bufferedAmount >= PRESSURE_BUFFER_BYTES
        || sample.encodeOverrun === true
      if (sample.throttled) {
        const backoffS = typeof sample.retryAfterS === 'number' && sample.retryAfterS > 0
          ? Math.min(sample.retryAfterS, THROTTLE_FLOOR_MAX_MS / 1000) : 1
        throttleFloorUntil = Math.max(throttleFloorUntil, Date.now() + backoffS * 1000)
      }
      if (pressured) {
        healthyStreak = 0
        step(1)
      } else if (typeof sample.latencyMs === 'number' && sample.latencyMs <= HEALTHY_LATENCY_MS
        && sample.bufferedAmount <= HEALTHY_BUFFER_BYTES) {
        healthyStreak += 1
        if (healthyStreak >= HEALTHY_STREAK) { step(-1); healthyStreak = 0 }
      }
      return this.plan()
    },
    reset() { level = DEFAULT_LEVEL; healthyStreak = 0; throttleFloorUntil = 0 },
  }
}

export type LivePhase = 'idle' | 'connecting' | 'viewing' | 'answering' | 'paused' | 'offline'

export const LIVE_PHASE_LABELS: Record<LivePhase, string> = {
  idle: '未开始', connecting: '连接中', viewing: '正在看', answering: '正在回答',
  paused: '已暂停', offline: '已断线',
}

export interface LivePhaseInput {
  active: boolean
  socketState: 'closed' | 'connecting' | 'open'
  paused: boolean
  reconnectScheduled: boolean
  /** 距最近一条观察结果的秒数；从未收到为 null */
  lastObservationAgeS: number | null
}

/** R6 验收门槛：用户要能一眼看懂“正在看、正在听、正在回答、已断线”。 */
export function classifyLivePhase(input: LivePhaseInput): LivePhase {
  if (!input.active) return 'idle'
  if (input.paused) return 'paused'
  if (input.socketState !== 'open') return input.reconnectScheduled ? 'connecting' : 'offline'
  if (input.lastObservationAgeS !== null && input.lastObservationAgeS <= 5) return 'answering'
  return 'viewing'
}

export interface ObservationLatency {
  /** 画面采集 → 观察返回（含排队+模型耗时） */
  e2eMs: number
  /** 发出 → 观察返回（服务端+模型耗时） */
  analyzeMs: number
  /** ledger 中仍未等到观察的在飞帧数 */
  queueDepth: number
}

export interface InFlightLedger {
  onSent(sequence: number, capturedAt: number, at: number): void
  onObserved(sequence: number, at: number): ObservationLatency | null
  pendingCount(): number
  /** 最老在飞帧的滞留时间 ms（无在飞帧为 0） */
  oldestPendingAge(now: number): number
  clear(): void
}

/** 在飞帧账本：服务端背压只会为最新帧产出观察，较新帧回帧即视为更早帧已被取代。 */
export function createInFlightLedger(): InFlightLedger {
  const frames = new Map<number, { capturedAt: number; sentAt: number }>()
  return {
    onSent(sequence, capturedAt, at) { frames.set(sequence, { capturedAt, sentAt: at }) },
    onObserved(sequence, at) {
      const frame = frames.get(sequence)
      if (!frame) return null
      frames.delete(sequence)
      for (const key of frames.keys()) { if (key < sequence) frames.delete(key) }
      return {
        e2eMs: Math.max(0, at - frame.capturedAt),
        analyzeMs: Math.max(0, at - frame.sentAt),
        queueDepth: frames.size,
      }
    },
    pendingCount() { return frames.size },
    oldestPendingAge(now) {
      let oldest = 0
      for (const frame of frames.values()) oldest = Math.max(oldest, now - frame.sentAt)
      return oldest
    },
    clear() { frames.clear() },
  }
}
