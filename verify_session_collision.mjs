// Real Edge checks; isolated browser profile and fixture server, no LLM requests.
import { createRequire } from 'node:module'
import assert from 'node:assert/strict'
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || `${process.env.USERPROFILE}/.workbuddy/binaries/node/workspace/node_modules/playwright-core`)
const base = process.env.DOCMIND_UI_TEST_URL || 'http://127.0.0.1:8011'
const browser = await chromium.launch({ channel: 'msedge', headless: true })
let passed = 0
const ok = text => { passed++; console.log('PASS ' + text) }
async function ready(page) {
  await page.waitForFunction(() => window.DocMindSession?.get())
  return page.evaluate(() => window.DocMindSession.ready)
}
try {
  const context = await browser.newContext()
  const errors = []
  context.on('page', p => p.on('pageerror', e => errors.push(e.message)))
  // A popup really copies opener sessionStorage in Chromium; no fake channel responses.
  for (const route of ['/workbench', '/']) {
    const first = await context.newPage()
    await first.goto(base + route)
    const original = await ready(first)
    const popup = first.waitForEvent('popup')
    await first.evaluate(route => { window.open(route) }, route)
    const second = await popup
    const duplicate = await ready(second)
    assert.notEqual(original, duplicate)
    assert.equal(await first.evaluate(() => window.DocMindSession.get()), original)
    ok(route + ': copied storage isolates newcomer and preserves original id')
    for (const [page, id] of [[first, original], [second, duplicate]]) {
      await page.reload()
      assert.equal(await ready(page), id)
      await page.goto(base + (route === '/' ? '/workbench' : '/'))
      assert.equal(await ready(page), id)
      await page.goBack()
      assert.equal(await ready(page), id)
    }
    ok(route + ': refresh, question/workbench navigation and back preserve both ids')
    assert.equal(await second.evaluate(id => window.DocMindSession.set(id), original), false)
    assert.equal(await second.evaluate(() => window.DocMindSession.get()), duplicate)
    ok(route + ': active history cannot be claimed by another page')
    await first.close()
    assert.equal(await second.evaluate(id => window.DocMindSession.set(id), original), true)
    ok(route + ': closed page releases history for explicit continuation')
    await second.close()
  }
  const fallback = await browser.newContext()
  await fallback.addInitScript(() => Object.defineProperty(navigator, 'locks', { value: undefined }))
  const p = await fallback.newPage()
  await p.goto(base + '/workbench')
  const one = await ready(p)
  await p.reload()
  assert.notEqual(await ready(p), one)
  ok('missing Web Locks allocates an isolated document session')
  assert.deepEqual(errors, [])
  ok('no browser errors')
  console.log(`${passed} checks passed; Edge ${browser.version()}. WebView2 tested separately.`)
} finally { await browser.close() }
