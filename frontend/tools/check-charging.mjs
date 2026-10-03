// Read-only UI check. Supply a short-lived operator token in the environment.
// Fixture responses below are intercepted only inside this isolated browser.
import assert from 'node:assert/strict'
import { chromium } from 'playwright'

const token = process.env.MAESTRO_CHECK_TOKEN
if (!token) throw new Error('MAESTRO_CHECK_TOKEN required')
const browser = await chromium.launch({ headless: true, channel: process.env.MAESTRO_BROWSER_CHANNEL || 'msedge' })
try {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  await context.addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(token), url: 'http://127.0.0.1:5173' },
    { name: 'ems-user', value: JSON.stringify({ username: 'docente', role: 'teacher', testbed: 'compartido' }), url: 'http://127.0.0.1:5173' },
  ])
  const page = await context.newPage()
  const errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('http://127.0.0.1:5173/charging')
  await page.getByText('Gestión conectada', { exact: true }).waitFor()
  await page.getByRole('tab', { name: 'CDR', exact: true }).click()
  await page.getByText('Sin registros en el CHF conectado para esta consulta.').waitFor()
  console.log('PASS: live management connected; live empty CDR collection rendered')
  await page.screenshot({ path: '../.work/charging-live-review.png', fullPage: true })

  await page.route('**/api/v1/charging/accounts*', (route) => route.fulfill({ json: {
    items: [{ supi: '99970•••••••001', enabled: true, quota_bytes: 20000,
      consumed_bytes: 8224, reserved_bytes: 0, available_bytes: 11776 }], total: 1, limit: 25, offset: 0,
  } }))
  await page.getByRole('tab', { name: 'Cuentas', exact: true }).click()
  await page.getByRole('button', { name: 'Actualizar', exact: true }).click()
  await page.getByText('99970•••••••001', { exact: true }).waitFor()
  await page.getByRole('button', { name: 'Ver', exact: true }).click()
  await page.getByRole('region', { name: 'Detalle del registro' }).waitFor()
  assert.equal(await page.getByRole('button', { name: 'Anterior', exact: true }).isDisabled(), true)
  assert.equal(await page.getByRole('button', { name: 'Siguiente', exact: true }).isDisabled(), true)
  console.log('PASS: fixture table, masked identifier, detail and pagination')
  await page.unroute('**/api/v1/charging/accounts*')
  await page.route('**/api/v1/charging/accounts*', (route) => route.fulfill({ status: 503, json: { detail: 'offline fixture' } }))
  await page.getByRole('button', { name: 'Actualizar', exact: true }).click()
  await page.getByRole('alert').filter({ hasText: 'No se pudo consultar el CHF' }).waitFor()
  assert.equal(await page.getByText('99970•••••••001', { exact: true }).count(), 0)
  console.log('PASS: unavailable source does not display stale records')
  await page.evaluate(() => localStorage.setItem('ems-scenario', JSON.stringify({ state: { scenario: '4g-epc' }, version: 0 })))
  await page.reload()
  await page.getByText('Esta integración Nchf corresponde al escenario 5G SA, no a 4G EPC.').waitFor()
  console.log('PASS: 4G scenario does not masquerade as Nchf')
  assert.deepEqual(errors, [])
} finally {
  await browser.close()
}
