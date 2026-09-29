import assert from 'node:assert/strict'
import { test } from 'node:test'
import { matchVoiceVision, retainVisionFrame } from '../src/workbench/voiceVisionSync.ts'

const frame = (projectId, capturedAt, timeline = []) => ({
  projectId, capturedAt, image: new Blob(['frame']), focusImage: null, timeline,
})

test('speech keeps the frame from its own interval when transcription finishes later', () => {
  const atSpeech = frame('p1', 10_000, [
    { at: '10:00:00', capturedAt: 9_000, observedAt: 9_500, observation: 'button visible' },
    { at: '10:00:01', capturedAt: 10_000, observedAt: 14_000, observation: 'late analysis' },
  ])
  const later = frame('p1', 30_000)
  const evidence = matchVoiceVision([atSpeech, later], 'p1', 10_200, 11_000)
  assert.equal(evidence.frame, atSpeech)
  assert.deepEqual(evidence.timeline.map(item => item.observation), ['button visible'])
  assert.equal(evidence.startedAt, 10_200)
})

test('old and cross-project frames cannot be attached to speech', () => {
  assert.equal(matchVoiceVision([frame('p1', 1_000)], 'p1', 20_000, 21_000).frame, null)
  assert.equal(matchVoiceVision([frame('other', 20_000)], 'p1', 20_000, 21_000).frame, null)
  assert.equal(matchVoiceVision([], 'p1', 20_000, 21_000, frame('other', 20_000)).frame, null)
})

test('recording can retain its start frame while STT and sampling continue', () => {
  const startFrame = frame('p1', 10_000)
  const later = frame('p1', 50_000)
  assert.equal(matchVoiceVision([later], 'p1', 10_100, 11_000, startFrame).frame, startFrame)
  let frames = []
  for (let i = 0; i < 30; i++) frames = retainVisionFrame(frames, frame('p1', i * 1000))
  assert.equal(frames.length, 20)
  frames = retainVisionFrame(frames, frame('p2', 31_000))
  assert.equal(frames.length, 1)
  assert.equal(frames[0].projectId, 'p2')
})
