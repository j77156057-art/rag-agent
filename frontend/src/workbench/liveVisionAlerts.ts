export interface VisionAnomaly {
  type: 'error_message' | 'crash' | 'render_failure' | 'layout_breakage' | 'unexpected_state'
  target: string
  evidence: string
  confidence: number
}

export interface PendingVisionAlert { key: string; count: number; candidate: VisionAnomaly }

/** A single model response is a lead; a matching later frame makes it a reminder. */
export function advanceVisionAlert(
  previous: PendingVisionAlert | null,
  candidates: VisionAnomaly[],
): { pending: PendingVisionAlert | null; confirmed: VisionAnomaly | null; key: string } {
  const candidate = candidates.find(item => item.confidence >= 0.75 && item.evidence.trim() && item.target.trim())
  if (!candidate) return { pending: null, confirmed: null, key: '' }
  const key = `${candidate.type}:${candidate.target.trim().toLocaleLowerCase()}`
  const count = previous?.key === key ? previous.count + 1 : 1
  return { pending: { key, count, candidate }, confirmed: count >= 2 ? candidate : null, key }
}
