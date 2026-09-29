export type VisualActionPhase = 'locating' | 'awaiting_confirmation' | 'needs_review' | 'awaiting_user' | 'completed' | 'stopped'
export type VisualActionStatus = 'met' | 'unmet' | 'uncertain' | 'unavailable'

export interface VisualActionStep {
  at: string
  target: string
  status: VisualActionStatus
  evidence: string
  nextTarget: string
}

export interface VisualActionLoop {
  id: string
  projectId: string
  workflowId: string
  goal: string
  phase: VisualActionPhase
  steps: VisualActionStep[]
  lastTarget: string
  note: string
}

export function beginVisualActionLoop(projectId: string, workflowId: string, goal: string, target: string): VisualActionLoop {
  return { id: globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`, projectId, workflowId, goal: goal.trim(), phase: 'locating',
    steps: [], lastTarget: target.trim(), note: '正在定位第一步。' }
}

/** A repeated suggestion requires review instead of another automatic proposal. No click happens here. */
export function advanceVisualActionLoop(loop: VisualActionLoop, step: VisualActionStep): VisualActionLoop {
  const steps = [...loop.steps, step]
  if (step.status === 'met') return { ...loop, steps, lastTarget: step.target,
    phase: 'awaiting_user', note: 'AI 认为画面达成目标，请你检查后验收。' }
  if (step.status !== 'unmet') return { ...loop, steps, lastTarget: step.target,
    phase: 'needs_review', note: '视觉证据不足，已停止自动建议；请检查画面或让 AI 排查。' }
  const nextTarget = step.nextTarget.trim()
  if (!nextTarget) return { ...loop, steps, lastTarget: step.target,
    phase: 'needs_review', note: '目标尚未达成，AI 没有可靠的下一控件建议。' }
  const normalized = nextTarget.replace(/\s+/g, '').toLowerCase()
  const repeated = steps.some(item => item.target.replace(/\s+/g, '').toLowerCase() === normalized)
  if (repeated) return { ...loop, steps, lastTarget: step.target,
    phase: 'needs_review', note: 'AI 连续建议已尝试的控件，已暂停以避免重复点击。' }
  return { ...loop, steps, lastTarget: nextTarget, phase: 'locating', note: `正在定位下一步：${nextTarget}` }
}

export function restoreVisualActionLoop(raw: string | null, projectId: string, workflowId: string): VisualActionLoop | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as VisualActionLoop
    if (value.projectId !== projectId || value.workflowId !== workflowId || !value.id || !value.goal || !Array.isArray(value.steps)) return null
    if (!['locating', 'awaiting_confirmation', 'needs_review', 'awaiting_user', 'completed', 'stopped'].includes(value.phase)) return null
    // Proposals expire and depend on a captured window. A reload always requires fresh localization.
    return value.phase === 'locating' || value.phase === 'awaiting_confirmation'
      ? { ...value, phase: 'needs_review', note: '页面已重载，请重新定位下一控件并确认。' } : value
  } catch { return null }
}
