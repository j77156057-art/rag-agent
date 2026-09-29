export interface TimedObservation {
  at: string
  observation: string
  capturedAt?: number
  observedAt?: number
}

export interface TimedVisionFrame {
  projectId: string
  capturedAt: number
  image: Blob
  focusImage: Blob | null
  timeline: TimedObservation[]
}

export interface VoiceVisionEvidence {
  projectId: string
  startedAt: number
  endedAt: number
  frame: TimedVisionFrame | null
  timeline: TimedObservation[]
}

const MAX_FRAMES = 20
const RETAIN_MS = 60_000
const MAX_FRAME_DISTANCE_MS = 6_000
const MAX_OBSERVATION_AGE_MS = 15_000

/** Keep a bounded, project-scoped record; a delayed STT response never reselects the latest frame. */
export function retainVisionFrame(frames: TimedVisionFrame[], frame: TimedVisionFrame): TimedVisionFrame[] {
  return [...frames.filter(item => item.projectId === frame.projectId && item.capturedAt !== frame.capturedAt
    && frame.capturedAt - item.capturedAt <= RETAIN_MS), frame].slice(-MAX_FRAMES)
}

export function matchVoiceVision(
  frames: TimedVisionFrame[], projectId: string, startedAt: number, endedAt: number,
  fallback: TimedVisionFrame | null = null,
): VoiceVisionEvidence {
  const start = Math.min(startedAt, endedAt)
  const end = Math.max(startedAt, endedAt)
  const midpoint = start + (end - start) / 2
  const valid = frames.filter(frame => frame.projectId === projectId
    && frame.capturedAt <= end + 500
    && frame.capturedAt >= start - MAX_FRAME_DISTANCE_MS)
  if (fallback?.projectId === projectId && !valid.includes(fallback)) valid.push(fallback)
  const distance = (frame: TimedVisionFrame) => frame.capturedAt < start
    ? start - frame.capturedAt : frame.capturedAt > end ? frame.capturedAt - end : 0
  const selected = valid.sort((a, b) => distance(a) - distance(b)
    || Math.abs(a.capturedAt - midpoint) - Math.abs(b.capturedAt - midpoint))[0]
  const frame = selected && distance(selected) <= MAX_FRAME_DISTANCE_MS ? selected : null
  const timeline = frame ? frame.timeline.filter(item => {
    const observedAt = item.observedAt ?? item.capturedAt ?? 0
    const capturedAt = item.capturedAt ?? observedAt
    return observedAt > 0 && observedAt <= end && capturedAt <= end
      && end - capturedAt <= MAX_OBSERVATION_AGE_MS
  }).slice(-3) : []
  return { projectId, startedAt: start, endedAt: end, frame, timeline }
}
