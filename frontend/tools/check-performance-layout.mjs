// Read-only smoke test against the running EMS. No saved queries are modified.
import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const token = process.env.MAESTRO_CHECK_TOKEN
if (!token) throw new Error('MAESTRO_CHECK_TOKEN required')
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } })
  await context.addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(token), url: 'http://127.0.0.1:5173' },
    { name: 'ems-user', value: JSON.stringify({ username: 'docente', role: 'teacher', testbed: 'local' }), url: 'http://127.0.0.1:5173' },
  ])
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('http://127.0.0.1:5173/performance')
  await page.getByText('ogstun', { exact: true }).waitFor({ timeout: 60_000 })
  await page.getByText('Cargando mediciones...', { exact: true }).waitFor({ state: 'hidden', timeout: 60_000 })
  for (const name of ['Barras', 'Datos', 'Líneas']) {
    await page.getByRole('button', { name, exact: true }).click({ timeout: 60_000 })
  }
  await page.screenshot({ path: '../.work/performance-redesign.png', fullPage: true })
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.getByRole('button', { name: 'Registro y Movilidad 5G', exact: true }).click()
  await page.getByText('AMF-01', { exact: true }).waitFor()
  assert.equal(await page.getByText('Procedimiento Registration', { exact: true }).count(), 0)
  const radios = page.getByRole('radio')
  assert.ok(await radios.count() > 1, 'Native AMF counters available')
  await radios.nth(0).check()
  await radios.nth(1).check()
  assert.equal(await page.locator('input[name="performance-counter"]:checked').count(), 1)
  await page.getByRole('button', { name: 'Registration global', exact: true }).click()
  await page.getByText('Procedimiento Registration', { exact: true }).waitFor()
  assert.equal(await page.getByText('AMF-01', { exact: true }).count(), 0)
  assert.equal(await page.locator('input[name="performance-counter"]:checked').count(), 1)
  await page.getByRole('button', { name: 'Nueva consulta', exact: true }).click()
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false)
  assert.deepEqual(errors, [])
  console.log('PASS: chart modes, AMF/global separation, single counter, new query, responsive layout, no runtime errors')
} finally {
  await browser.close()
}
