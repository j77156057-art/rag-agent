import assert from 'node:assert/strict'
import { test } from 'node:test'
import { focusCropRect, normalizeFocusRegion } from '../src/workbench/liveVisionFocus.ts'

test('selection stays inside the displayed frame and maps to source pixels', () => {
  const region = normalizeFocusRegion(
    { left: 100, top: 50, width: 400, height: 200 },
    { x: 500, y: 250 }, { x: 300, y: 100 },
  )
  assert.deepEqual(region, { x: 0.5, y: 0.25, width: 0.5, height: 0.75 })
  assert.deepEqual(focusCropRect(region, 1280, 720), { x: 640, y: 180, width: 640, height: 540 })
})

test('tiny or invalid selection is rejected', () => {
  const bounds = { left: 0, top: 0, width: 400, height: 200 }
  assert.equal(normalizeFocusRegion(bounds, { x: 30, y: 40 }, { x: 32, y: 41 }), null)
  assert.equal(normalizeFocusRegion({ ...bounds, width: 0 }, { x: 0, y: 0 }, { x: 20, y: 20 }), null)
})
