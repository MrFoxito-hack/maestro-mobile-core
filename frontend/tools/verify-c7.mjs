import assert from 'node:assert/strict'
import { writeFile } from 'node:fs/promises'
import { chromium } from 'playwright'

// C7 UI acceptance. Terminal actions, authority and measurements are fixtures.
// Only login and existing read-only page catalogs reach the local EMS.
const origin = 'http://127.0.0.1:5173'
const evidence = '../.work/c7-ux-mml/evidence/'
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
const errors = [], writes = [], checks = []
let phase = 'idle', version = 33, action = 'c7-fixture-action-33', connected = true, availableBytes = 40000000
const completedAt = new Date(Date.now() - 60000).toISOString()
const xdp = { available: true, effective_mode: 'kernel', confirmed: true, slice: 'urllc', namespace: 'maestro-urllc', interfaces: [], driver_mode: 'generic', session_generation: '42', ue: '10.47.0.3', counters: { ul_redirect_requested: { packets: 123, bytes: 4567 } } }
const devices = [1, 2, 3, 4, 5, 6].map(n => ({ id: String(n), supi: `imsi-99970000000000${n}`, kind: ['smartphone', 'vehicle', 'sensor'][(n - 1) % 3], label: `Terminal ${n}` }))
const measurement = (count) => ({ source_ip: '10.47.0.3', target: '172.31.48.2', sent: count, received: count, rtt_ms: 7.25, jitter_ms: 0.75, loss_pct: 0, samples_ms: [7, 7.5], observed_at: new Date().toISOString(), send_seconds: 0.1, sent_bytes: count * 92 })
try {
  const context = await browser.newContext({ viewport: { width: 1600, height: 1100 } })
  const login = await context.request.post('http://127.0.0.1:8000/api/v1/auth/login', { data: { username: process.env.EMS_VERIFY_USER || 'docente', password: process.env.EMS_VERIFY_PASSWORD || 'teacher-change-me' } })
  assert(login.ok(), 'Local login')
  const auth = await login.json()
  await context.addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(auth.access_token), url: origin },
    { name: 'ems-user', value: JSON.stringify(auth.user), url: origin },
  ])
  await context.route('**/api/v1/**', async route => {
    const request = route.request(), path = new URL(request.url()).pathname.replace('/api/v1', '')
    const respond = json => route.fulfill({ json })
    if (request.method() === 'POST' && path !== '/performance/query') writes.push({ path, payload: request.postDataJSON() })
    if (path === '/terminal-inventory') return respond({ devices })
    if (path === '/upf-xdp/status') return respond(xdp)
    if (path === '/operations/policy-authority') return respond({ version, phase })
    if (path === '/operations/history') return respond([{ id: 'c7-ui', operation_id: 'nwdaf.mode', completed_at: completedAt, status: 'success', parameters: { mode: 'MANUAL' }, data: { action_id: action, version, effective_policy_verified: true } }])
    if (path === '/upf-telemetry/snapshot') {
      const timestamp = Date.now() / 1000
      return respond({ slice_configuration: 'deployed', targets: ['embb', 'urllc', 'miot'].map((service, i) => ({ id: service, service, label: ['Smartphone · eMBB', 'Vehículo · URLLC', 'Sensor · MIoT'][i], dnn: ['internet', '5g-plus', 'corporate'][i], sst: i + 1, sd: `00000${i + 1}`, status: 'measured', source_timestamp: timestamp, metrics: { ul_pps: 37.5, dl_pps: 36.5, ul_bps: 1250000, dl_bps: 2500000, active_sessions: 2, pfcp_peers: 1 }, traffic_complete: true, sample_age_seconds: 1, xdp: 'not_applicable', history: [{ timestamp: timestamp - 5, ul_pps: 30, ul_bps: 1200000 }, { timestamp, ul_pps: 37.5, ul_bps: 1250000 }] })) })
    }
    if (path === '/terminal/topup') { availableBytes += 50000000; return respond({ status: 'success' }) }
    if (path === '/terminal/status') return respond({ active_apn: 'internet', apn_sessions: [{ apn: 'internet', interface: 'uesimtun0', address: '10.45.0.2', snssai: { sst: 1, sd: 1 } }], service: 'running', registered: connected, subscriber: '999700000000001', observed_at: new Date().toISOString(), interfaces: [], native_state: {}, charging_available: true, balance: { quota_bytes: 50000000, consumed_bytes: 10000000, reserved_bytes: 0, available_bytes: availableBytes } })
    if (path === '/terminal/devices/vehicle' || path === '/terminal/devices/sensor') return respond({ connected, problem: null, session: connected ? { address: '10.47.0.3', interface: 'uesimtun0' } : null, xdp })
    if (path === '/terminal/devices/vehicle/probe' || path === '/terminal/devices/vehicle/brake') {
      if (!connected) return route.fulfill({ status: 503, json: { detail: 'C7: enlace no disponible' } })
      await new Promise(resolve => setTimeout(resolve, 200))
      return respond(measurement(path.endsWith('/brake') ? 1 : 20))
    }
    if (path === '/terminal-devices/industrial/telemetry') {
      const { sensors } = request.postDataJSON(), ids = Array.from({ length: sensors }, (_, i) => i + 1)
      return respond({ ...measurement(sensors), virtual_sensors: sensors, radio_ues: 1, sent_sensor_ids: ids, acked_sensor_ids: ids })
    }
    if (request.method() !== 'GET' && path !== '/performance/query') {
      errors.push(`Unexpected write blocked: ${path}`)
      return route.abort()
    }
    return route.continue()
  })
  const page = await context.newPage()
  page.on('pageerror', error => errors.push(error.message))
  await page.goto(origin + '/services/verticals')
  await page.locator('.verticals-columns').waitFor()
  await page.getByLabel('Terminal vehicle', { exact: true }).selectOption(devices[4].supi)
  await page.getByRole('button', { name: 'Medir enlace MEC' }).click()
  await page.getByRole('button', { name: 'Detener monitor' }).click()
  await page.getByRole('button', { name: /Frenado de emergencia/ }).click()
  await page.getByText(/ACK en 7[.,]25 ms/).waitFor()
  assert(writes.some(w => w.path.endsWith('/probe') && w.payload.imsi === devices[4].supi))
  await page.getByLabel('Terminal sensor', { exact: true }).selectOption(devices[5].supi)
  await page.getByLabel('Densidad de flota masiva').selectOption('10')
  await page.getByRole('button', { name: /Transmitir ciclo masivo/ }).click()
  await page.waitForFunction(() => document.querySelectorAll('.sensor-cell.ack').length === 10)
  assert(writes.some(w => w.path.endsWith('/telemetry') && w.payload.imsi === devices[5].supi))
  checks.push('Vehicle 005 and sensor 006 selection, fixture RTT/ACK, burst counters and dispatch identity')
  await page.getByRole('button', { name: 'Abrir terminal 5G', exact: true }).click()
  const phone = page.getByRole('dialog', { name: 'Terminal Smartphone 5G SA' })
  await phone.waitFor()
  await phone.getByTitle('Safari', { exact: true }).click()
  await phone.getByTitle('Ir a la pantalla de inicio (Home)').click()
  await phone.getByTitle('Mi 5G', { exact: true }).click()
  await phone.getByRole('button', { name: /Recargar Paquete/ }).waitFor()
  await phone.getByRole('button', { name: /Recargar Paquete/ }).click()
  await phone.getByText('90', { exact: true }).waitFor()
  assert(writes.some(w => w.path === '/terminal/topup' && w.payload.request_id))
  await phone.getByTitle('Ir a la pantalla de inicio (Home)').click()
  await phone.getByTitle('Stream5G', { exact: true }).click()
  await page.screenshot({ path: evidence + 'smartphone.png' })
  await page.keyboard.press('Escape')
  checks.push('Smartphone Safari, Stream5G and CHF balance/recharge view render')
  await page.screenshot({ path: evidence + 'verticals.png', fullPage: true })
  connected = false
  await page.reload()
  await page.getByLabel('Terminal vehicle', { exact: true }).waitFor()
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.includes('Medir enlace MEC') && b.disabled))
  assert((await page.getByText('Sin enlace verificado', { exact: true }).count()) > 0)
  connected = true
  await page.reload()
  await page.waitForFunction(() => [...document.querySelectorAll('button')].some(b => b.textContent.includes('Medir enlace MEC') && !b.disabled))
  checks.push('Disconnected devices disable actions; reconnection restores them; phone recharge updates from fixture response')
  await page.goto(origin + '/performance')
  const telemetry = page.getByRole('region', { name: 'Telemetría de los planos de usuario' })
  await telemetry.getByRole('heading', { name: 'Smartphone · eMBB' }).waitFor()
  assert.equal(await telemetry.locator('article').count(), 3)
  const ab = page.getByRole('region', { name: 'Ventanas A/B correlacionadas' })
  await ab.getByRole('cell', { name: 'Estable', exact: true }).first().waitFor({ timeout: 15000 })
  await ab.getByRole('cell', { name: action, exact: true }).first().waitFor()
  phase = 'mutating'; version = 34; action = 'c7-fixture-action-34'
  await ab.getByRole('cell', { name: 'Transición', exact: true }).first().waitFor({ timeout: 15000 })
  assert((await ab.textContent()).includes('ul_redirect_requested: 123 / 4567'))
  checks.push('Three slice channels; Action ID, authority/version, generation, XDP counters; stable and transition rows')
  await page.screenshot({ path: evidence + 'performance.png', fullPage: true })
  for (const width of [1280, 768, 390]) {
    await page.setViewportSize({ width, height: 1000 })
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Performance overflow ${width}`)
    const nav = page.getByRole('navigation', { name: 'Navegación principal' })
    await page.mouse.move(0, 0)
    await nav.getByRole('button', { name: 'Servicios', exact: true }).focus()
    await page.keyboard.press('Enter')
    await page.getByRole('menuitem', { name: /Casos Verticales/ }).focus()
    await page.keyboard.press('Enter')
    await page.waitForURL('**/services/verticals')
    assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `Verticals overflow ${width}`)
    await nav.getByRole('button', { name: 'Operación', exact: true }).focus()
    await page.keyboard.press('Enter')
    await page.getByRole('menuitem', { name: /^Performance/ }).focus()
    await page.keyboard.press('Enter')
    await page.waitForURL('**/performance')
  }
  checks.push('Navigation and no page overflow at 1280, 768 and 390 pixels')
  assert.deepEqual(errors, [])
  await writeFile(evidence + 'ui-acceptance.json', JSON.stringify({ status: 'passed', measurement_source: 'controlled fixtures; no live campaigns', checks, intercepted_writes: writes, page_errors: errors }, null, 2))
  console.log('PASS C7:', checks.join('; '))
} catch (error) {
  console.error('C7 diagnostics:', errors)
  throw error
} finally { await browser.close() }
