import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const token = process.env.MAESTRO_CHECK_TOKEN
if (!token) throw new Error('MAESTRO_CHECK_TOKEN required')
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const context = await browser.newContext({ viewport: { width: 1920, height: 1080 } })
  await context.addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(token), url: 'http://127.0.0.1:5173' },
    { name: 'ems-user', value: JSON.stringify({ username: process.env.MAESTRO_CHECK_USER, role: 'teacher', testbed: 'local' }), url: 'http://127.0.0.1:5173' },
  ])
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto('http://127.0.0.1:5173/traces')
  await page.getByRole('tab', { name: /Tareas/ }).click()
  await page.getByText('CHF - aceptación operativa Nchf', { exact: true }).click()
  await page.getByText('CHF', { exact: true }).first().waitFor({ timeout: 60000 })
  assert(await page.getByText(/Nchf_/).count() > 0)
  await page.getByPlaceholder(/Mensaje, protocolo/).fill('Nchf')
  await page.getByText(/Nchf_ConvergedCharging_Create/).first().click()
  await page.getByText('Mensaje seleccionado', { exact: true }).waitFor()
  await page.screenshot({ path: '../.work/chf-live-trace.png', fullPage: true })
  await page.goto('http://127.0.0.1:5173/charging')
  await page.getByText('Gestión conectada', { exact: true }).waitFor()
  await page.getByRole('tab', { name: 'CDR', exact: true }).click()
  await page.getByRole('button', { name: 'Ver', exact: true }).first().waitFor()
  await page.screenshot({ path: '../.work/chf-live-accounting.png', fullPage: true })
  assert.deepEqual(errors, [])
  console.log('PASS: actual CHF lifeline, Nchf messages and live CDR list; no browser fixtures')
} finally {
  await browser.close()
}
