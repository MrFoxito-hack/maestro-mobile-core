import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { chromium } from 'playwright'

const origin = 'http://127.0.0.1:5173'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  await context.addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(process.env.MAESTRO_CHECK_TOKEN), url: origin },
    { name: 'ems-user', value: JSON.stringify({ username: process.env.MAESTRO_CHECK_USER, role: 'teacher', testbed: 'local' }), url: origin },
  ])
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (['error', 'warning'].includes(message.type())) errors.push(message.text())
  })
  await page.goto(origin + '/performance')
  const template = page.getByRole('button', { name: 'Rendimiento por Slice (eMBB / URLLC / MIoT)', exact: true })
  await template.waitFor({ timeout: 45000 })
  await template.click()
  const jsonDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Exportar JSON', exact: true }).click({ timeout: 45000 })
  const downloaded = await jsonDownload
  const data = JSON.parse(await readFile(await downloaded.path(), 'utf8'))
  assert.equal(data.series.length, 3)
  assert.deepEqual(data.series.map((s) => s.object_id).sort(), ['nf:upf', 'nf:upf2', 'nf:upf3'])
  assert(data.series.every((s) => s.counter_id === 'upf.triad.dl.mbps' && s.points.length > 0))
  const csvDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Exportar CSV', exact: true }).click()
  const csv = await readFile(await (await csvDownload).path(), 'utf8')
  assert(csv.includes('nf:upf3') && csv.includes('Mbps'))
  assert.equal(await page.getByRole('region', { name: 'Telemetría de los planos de usuario' }).count(), 0)
  await page.screenshot({ path: '../.work/multi-upf-dashboard.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ path: '../.work/multi-upf-dashboard-mobile.png', fullPage: true })
  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.getByRole('button', { name: 'Abrir terminal 5G' }).click()
  await page.getByTitle('Configuración', { exact: true }).click()
  await page.getByRole('button', { name: /Punto de acceso \(APN\)/ }).click()
  const vehicle = page.getByRole('button', { name: /Vehículo URLLC/ })
  await vehicle.waitFor()
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(
    (button) => button.textContent.includes('Vehículo URLLC') && !button.disabled
  ), null, { timeout: 30000 })
  assert.equal(await vehicle.isDisabled(), false)
  await page.screenshot({ path: '../.work/triad-terminal-enabled.png', fullPage: true })
  assert.deepEqual(errors, [])
  console.log('PASS: native UPF template, 3 live series, CSV/JSON, desktop/mobile, vehicle enabled, no console warnings/errors')
} finally { await browser.close() }
