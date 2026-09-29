import assert from 'node:assert/strict'
import { test } from 'node:test'
import { advanceVisionAlert } from '../src/workbench/liveVisionAlerts.ts'

const candidate = { type: 'error_message', target: 'Error 404', evidence: '弹窗显示 Error 404', confidence: 0.94 }

test('a single anomaly is only a lead; a matching second frame confirms it for reminder', () => {
  const first = advanceVisionAlert(null, [candidate])
  assert.equal(first.confirmed, null)
  const second = advanceVisionAlert(first.pending, [candidate])
  assert.deepEqual(second.confirmed, candidate)
})

test('a clean or different frame breaks the confirmation chain', () => {
  const first = advanceVisionAlert(null, [candidate])
  assert.equal(advanceVisionAlert(first.pending, []).pending, null)
  assert.equal(advanceVisionAlert(first.pending, [{ ...candidate, target: 'Error 500' }]).confirmed, null)
})
