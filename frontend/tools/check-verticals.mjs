import assert from 'node:assert/strict'
import { writeFile } from 'node:fs/promises'
import { chromium } from 'playwright'

const origin = 'http://127.0.0.1:5173'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
const results = {}, errors = []
try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } })
  await context.addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(process.env.MAESTRO_CHECK_TOKEN), url: origin },
    { name: 'ems-user', value: JSON.stringify({ username: process.env.MAESTRO_CHECK_USER, role: 'teacher', testbed: 'local' }), url: origin },
  ])
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (['warning', 'error'].includes(message.type())) errors.push(message.text()) })
  await page.goto(origin + '/services/verticals')
  await page.locator('.verticals-columns').waitFor()
  await page.getByRole('button', { name: 'Servicios', exact: true }).hover()
  await page.getByRole('menuitem', { name: /Casos Verticales/ }).waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Abrir terminal 5G', exact: true }).click()
  const phone = page.getByRole('dialog', { name: 'Terminal Smartphone 5G SA' })
  await phone.waitFor()
  results.phone_width = (await phone.boundingBox()).width
  assert(results.phone_width <= 380)
  assert.equal(await phone.locator('select').count(), 0, 'Phone must not select other IMSIs')
  assert.equal(await phone.getByRole('button', { name: /Vehículo|Sensor|CorpNet/ }).count(), 0)
  await page.screenshot({ animations: 'disabled', path: '../.work/verticals-smartphone.png' })
  await phone.getByTitle('Configuración', { exact: true }).click()
  await phone.getByRole('button', { name: /Punto de acceso \(APN\)/ }).click()
  await phone.getByRole('button', { name: /Smartphone eMBB/ }).waitFor()
  assert.equal(await phone.getByRole('button', { name: /Vehículo|Sensor|corporate|5g-plus/ }).count(), 0)
  await page.mouse.click(200, 200)
  await phone.waitFor({ state: 'hidden' })
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.includes('Medir enlace MEC') && !b.disabled), { timeout: 45000 })
  await page.getByLabel('Acelerador', { exact: false }).fill('60')
  const measured = page.waitForResponse(r => r.url().endsWith('/terminal/devices/vehicle/probe') && r.request().method() === 'POST')
  await page.getByRole('button', { name: 'Medir enlace MEC' }).click()
  results.vehicle = await (await measured).json()
  assert.equal(results.vehicle.received, 20)
  await page.getByRole('button', { name: 'Detener monitor' }).click()
  const braked = page.waitForResponse(r => r.url().endsWith('/terminal/devices/vehicle/brake'))
  await page.getByRole('button', { name: /Frenado de emergencia/ }).click()
  results.brake = await (await braked).json()
  assert.equal(results.brake.received, 1)
  await page.getByLabel('Densidad de flota masiva').selectOption('1000')
  assert.equal(await page.locator('.sensor-cell').count(), 1000)
  const response = page.waitForResponse(r => r.url().endsWith('/terminal-devices/industrial/telemetry'))
  const started = Date.now() / 1000
  await page.getByRole('button', { name: /Transmitir ciclo masivo de telemetría/ }).click()
  await page.screenshot({ animations: 'disabled', path: '../.work/verticals-transmitting.png', fullPage: true })
  const received = await response
  assert(received.ok(), await received.text())
  results.fleet = await received.json()
  assert.equal(results.fleet.sent, 1000)
  assert.equal(results.fleet.radio_ues, 1)
  assert(results.fleet.received > 0)
  await page.waitForFunction(n => document.querySelectorAll('.sensor-cell.ack').length === n, results.fleet.received)
  assert.equal(results.fleet.acked_sensor_ids.length, results.fleet.received)
  await page.screenshot({ animations: 'disabled', path: '../.work/verticals-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.screenshot({ animations: 'disabled', path: '../.work/verticals-mobile.png', fullPage: true })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'Page overflows mobile viewport')
  await page.getByRole('button', { name: 'Abrir terminal 5G', exact: true }).click()
  await phone.waitFor()
  const bounds = await phone.boundingBox()
  assert(bounds.x >= 0 && bounds.x + bounds.width <= 390)
  await page.keyboard.press('Escape')
  for (const density of ['10', '50', '100']) {
    await page.getByLabel('Densidad de flota masiva').selectOption(density)
    assert.equal(await page.locator('.sensor-cell').count(), +density)
    assert.equal(await page.locator('.sensor-cell.ack').count(), 0)
  }
  await page.waitForTimeout(6000)
  const telemetry = await context.request.get(origin + '/api/v1/upf-telemetry/snapshot', {
    headers: { Authorization: 'Bearer ' + process.env.MAESTRO_CHECK_TOKEN },
  })
  assert(telemetry.ok())
  const upf = (await telemetry.json()).targets.find(t => t.service === 'miot')
  const points = upf.history.filter(p => p.timestamp >= started)
  results.peak_ul_pps = Math.max(...points.map(p => p.ul_pps ?? 0))
  assert(results.peak_ul_pps > 20, 'MIoT UPF did not observe the UDP cycle')
  assert.deepEqual(errors, [])
  await writeFile('../.work/verticals-browser.json', JSON.stringify(results, null, 2))
  console.log(`PASS: compact eMBB phone; Services navigation; V2X RTT/ACK; ${results.fleet.received}/1000 sensor ACKs; ${results.peak_ul_pps.toFixed(1)} UPF pps; mobile layout; no console warnings/errors`)
} catch (error) {
  console.error('Browser diagnostics:', errors)
  throw error
} finally { await browser.close() }
