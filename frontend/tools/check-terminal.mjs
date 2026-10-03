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
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:5173/charging')
  await page.getByRole('button', { name: 'Abrir terminal 5G' }).click()
  await page.getByText('Sesión de datos', { exact: true }).waitFor({ timeout: 60000 })
  await page.screenshot({ path: '../.work/terminal-network.png', fullPage: true })
  await page.getByRole('button', { name: 'Mi bolsa', exact: true }).click()
  await page.getByText('Reservado por las sesiones', { exact: true }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Recargar 50 MB', exact: true }).isDisabled(), false)
  await page.screenshot({ path: '../.work/terminal-balance.png', fullPage: true })
  await page.getByRole('button', { name: 'Data Lab', exact: true }).click()
  await page.screenshot({ path: '../.work/terminal-data-lab.png', fullPage: true })
  const diagnostics = await page.getByText('Diagnóstico de red', { exact: true }).boundingBox()
  const navigation = await page.getByRole('navigation', { name: 'Aplicaciones del terminal' }).boundingBox()
  assert(diagnostics.y + diagnostics.height <= navigation.y, 'Collapsed diagnostics should fit above bottom navigation')
  assert.equal(await page.getByRole('button', { name: 'Iniciar', exact: true }).isVisible(), false)
  await page.getByText('Diagnóstico de red', { exact: true }).click()
  assert.equal(await page.getByRole('button', { name: 'Iniciar', exact: true }).isDisabled(), false)
  assert.equal(await page.getByRole('button', { name: 'Probar N6', exact: true }).isDisabled(), false)
  if (process.env.MAESTRO_CHECK_N6 === '1') {
    await page.getByRole('button', { name: 'Probar N6', exact: true }).click()
    await page.getByText('Descarga N6 completada.', { exact: true }).waitFor({ timeout: 30000 })
    await page.getByText(/Completada · 262/).waitFor()
    await page.screenshot({ path: '../.work/terminal-n6.png', fullPage: true })
  }
  await page.setViewportSize({ width: 390, height: 844 })
  await page.waitForTimeout(350)
  const box = await page.getByRole('dialog').boundingBox()
  await page.screenshot({ path: '../.work/terminal-mobile.png', fullPage: true })
  assert(box.x >= 0 && box.y >= 0 && box.x + box.width <= 391 && box.y + box.height <= 845, JSON.stringify(box))
  await page.keyboard.press('Escape')
  await page.getByRole('dialog').waitFor({ state: 'detached' })
  assert.equal(await page.getByRole('dialog').count(), 0)
  assert.deepEqual(errors, [])
  console.log('PASS: actual UE/CHF data, desktop/mobile phone, keyboard close, no browser fixtures')
} finally { await browser.close() }
