import assert from 'node:assert/strict'
import { chromium } from 'playwright'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
  await context.addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(process.env.MAESTRO_CHECK_TOKEN), url: 'http://127.0.0.1:5173' },
    { name: 'ems-user', value: JSON.stringify({ username: process.env.MAESTRO_CHECK_USER, role: 'teacher', testbed: 'local' }), url: 'http://127.0.0.1:5173' },
  ])
  const page = await context.newPage()
  const responses = []
  const errors = []
  page.on('response', response => { if (response.url().includes('/terminal/media/')) responses.push({ path: new URL(response.url()).pathname, status: response.status() }) })
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:5173/charging')
  await page.getByRole('button', { name: 'Abrir terminal 5G' }).click()
  await page.getByText('Sesión de datos', { exact: true }).waitFor({ timeout: 30000 })
  await page.getByRole('button', { name: 'Data Lab', exact: true }).click()
  const video = page.getByLabel('Video real por N6')
  const quality = process.env.MAESTRO_VIDEO_QUALITY || '1080p'
  assert(['720p', '1080p'].includes(quality))
  await page.getByLabel('Calidad del video').selectOption(quality)
  await page.getByRole('button', { name: 'Ver video', exact: true }).click()
  try {
    await page.waitForFunction(() => document.querySelector('video')?.currentTime > 5, null, { timeout: 45000 })
  } catch (error) {
    console.log(JSON.stringify({ responses, errors, player: await video.evaluate(el => ({ time: el.currentTime, ready: el.readyState, paused: el.paused, error: el.error?.message })), text: await page.getByRole('dialog').innerText() }))
    await page.screenshot({ path: '../.work/terminal-video-error.png', fullPage: true })
    throw error
  }
  await page.screenshot({ path: '../.work/terminal-video.png', fullPage: true })
  console.log('PASS: real ' + quality + ' frames playing, ' + responses.length + ' authenticated media responses')
  if (process.env.MAESTRO_VIDEO_EXHAUST === '1') {
    const deadline = Date.now() + 300000
    while (Date.now() < deadline && !responses.some(response => response.status === 502)) {
      const exhausted = await page.getByText('Bolsa agotada: reproducción detenida.', { exact: true }).count()
      if (exhausted) break
      if (await video.evaluate(el => el.ended)) await page.getByRole('button', { name: 'Ver video', exact: true }).click()
      await page.waitForTimeout(1000)
    }
    await page.waitForTimeout(1000)
    assert(await video.evaluate(el => el.paused), 'Video did not pause at network/quota cut')
    assert(responses.some(response => response.status === 502) || await page.getByText('Bolsa agotada: reproducción detenida.', { exact: true }).count(), 'No actual quota/network cut observed')
    await page.screenshot({ path: '../.work/terminal-video-cut.png', fullPage: true })
    console.log('PASS: player halted on real transfer failure / zero balance; correlate CHF to establish cause')
  }
  await page.getByRole('button', { name: 'Detener video', exact: true }).click()
  assert.deepEqual(errors, [])
  console.log(JSON.stringify({ media: responses }))
} finally { await browser.close() }
