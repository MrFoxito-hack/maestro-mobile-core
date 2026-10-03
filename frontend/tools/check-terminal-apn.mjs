import assert from 'node:assert/strict'
import { chromium } from 'playwright'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
const api = 'http://127.0.0.1:8000/api/v1'
const auth = { Authorization: `Bearer ${process.env.MAESTRO_CHECK_TOKEN}` }
let previous
let context
try {
  context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  const state = await context.request.get(`${api}/terminal/status`, { headers: auth })
  assert.equal(state.status(), 200)
  previous = (await state.json()).active_apn
  await context.addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(process.env.MAESTRO_CHECK_TOKEN), url: 'http://127.0.0.1:5173' },
    { name: 'ems-user', value: JSON.stringify({ username: process.env.MAESTRO_CHECK_USER, role: 'teacher', testbed: 'local' }), url: 'http://127.0.0.1:5173' },
  ])
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:5173/charging')
  await page.getByRole('button', { name: 'Abrir terminal 5G' }).click()
  await page.getByText('Sesión de datos', { exact: true }).waitFor({ timeout: 30000 })
  const choose = async (apn) => {
    await page.getByRole('button', { name: 'Conexión', exact: true }).click()
    const response = page.waitForResponse(r => r.url().endsWith('/terminal/apn') && r.request().method() === 'POST')
    await page.getByLabel('DNN / APN de Data Lab').selectOption(apn)
    assert.equal((await response).status(), 200)
    await page.getByRole('button', { name: 'Data Lab', exact: true }).click()
  }
  await choose(previous === 'corporate' ? 'internet' : 'corporate')
  if (previous === 'corporate') await choose('corporate')
  await page.getByRole('button', { name: 'Abrir Intranet', exact: true }).click()
  await page.getByRole('article', { name: 'Portal corporativo' }).waitFor()
  assert.equal(await page.locator('video').count(), 0)
  assert.equal((await context.request.get(`${api}/terminal/media/720p/index.m3u8`, { headers: auth })).status(), 403)
  await page.screenshot({ path: '../.work/terminal-corporate.png', fullPage: true })
  await page.reload()
  await page.getByRole('button', { name: 'Abrir terminal 5G' }).click()
  await page.waitForFunction(() => document.querySelector('#terminal-apn')?.value === 'corporate')
  await choose('internet')
  assert(await page.getByRole('button', { name: 'Intranet · Solo red corporativa' }).isDisabled())
  assert.equal((await context.request.get(`${api}/terminal/corporate/intranet`, { headers: auth })).status(), 403)
  await page.getByRole('button', { name: 'Ver video', exact: true }).click()
  await page.waitForFunction(() => document.querySelector('video')?.currentTime > 3, null, { timeout: 45000 })
  await page.getByRole('button', { name: 'Detener video', exact: true }).click()
  await page.screenshot({ path: '../.work/terminal-internet.png', fullPage: true })
  assert.deepEqual(errors, [])
  console.log('PASS: APN persistence, corporate portal, server-side denials, real video after switching back')
} finally {
  if (context && previous) await context.request.post(`${api}/terminal/apn`, { headers: auth, data: { apn: previous } })
  await browser.close()
}
