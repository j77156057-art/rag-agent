// Edge/WebView2 smoke test: a copied sessionStorage value must not stay shared.
// Requires a running DocMind server, defaults to the UI fixture at :8011.
import { createRequire } from 'node:module'
import assert from 'node:assert/strict'
const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || `${process.env.USERPROFILE}/.workbuddy/binaries/node/workspace/node_modules/playwright-core`)
const base = process.env.DOCMIND_UI_TEST_URL || 'http://127.0.0.1:8011'
const browser = await chromium.launch({ channel: 'msedge', headless: true })
try {
  const context = await browser.newContext()
  const first = await context.newPage()
  await first.goto(base + '/workbench')
  await first.waitForTimeout(600)
  const copied = await first.evaluate(() => sessionStorage.getItem('docmind_session_id'))
  assert.ok(copied, 'first tab did not create a session id')
  const second = await context.newPage()
  await second.addInitScript((id) => sessionStorage.setItem('docmind_session_id', id), copied)
  await second.goto(base + '/workbench')
  await second.waitForTimeout(800)
  const ids = await Promise.all([
    first.evaluate(() => sessionStorage.getItem('docmind_session_id')),
    second.evaluate(() => sessionStorage.getItem('docmind_session_id')),
  ])
  assert.notEqual(ids[0], ids[1], `copied tabs still share ${ids[0]}`)
  console.log(`PASS copied Edge tabs isolated: ${ids[0]} != ${ids[1]}`)
} finally {
  await browser.close()
}
