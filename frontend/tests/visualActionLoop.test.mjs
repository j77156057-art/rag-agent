import assert from 'node:assert/strict'
import { test } from 'node:test'
import { advanceVisualActionLoop, beginVisualActionLoop, restoreVisualActionLoop } from '../src/workbench/visualActionLoop.ts'

const step = (target, status, nextTarget = '') => ({ at: new Date().toISOString(), target, status,
  evidence: '可见状态', nextTarget })

test('unmet action proposes a fresh next target but never executes it', () => {
  const started = beginVisualActionLoop('project', 'workflow', '看到完成提示', '保存')
  const next = advanceVisualActionLoop(started, step('保存', 'unmet', '确认'))
  assert.equal(next.phase, 'locating')
  assert.equal(next.lastTarget, '确认')
  assert.equal(next.steps.length, 1)
  assert.equal(advanceVisualActionLoop(next, step('确认', 'met')).phase, 'awaiting_user')
})

test('uncertain judgement and repeated suggestion stop the loop', () => {
  const started = beginVisualActionLoop('project', '', '完成', '保存')
  assert.equal(advanceVisualActionLoop(started, step('保存', 'uncertain', '确认')).phase, 'needs_review')
  const first = advanceVisualActionLoop(started, step('保存', 'unmet', '确认'))
  const second = advanceVisualActionLoop(first, step('确认', 'unmet', '保存'))
  assert.equal(second.phase, 'needs_review')
  assert.match(second.note, /重复点击/)
})

test('restored proposals require fresh localization and stay project scoped', () => {
  const started = beginVisualActionLoop('project', 'workflow', '完成', '保存')
  const raw = JSON.stringify({ ...started, phase: 'awaiting_confirmation' })
  assert.equal(restoreVisualActionLoop(raw, 'project', 'workflow').phase, 'needs_review')
  assert.equal(restoreVisualActionLoop(raw, 'other', 'workflow'), null)
  assert.equal(restoreVisualActionLoop(raw, 'project', 'other'), null)
})
