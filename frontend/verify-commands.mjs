import assert from 'node:assert/strict'
import { chromium } from 'playwright'

// Isolated UI verification: all operational API calls are intercepted.
// No command is sent to the VM, including the restart confirmation test.
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
try {
  const page = await browser.newPage({ viewport: { width: 1600, height: 950 } })
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  const login = await page.request.post('http://127.0.0.1:8000/api/v1/auth/login', {
    data: { username: process.env.EMS_VERIFY_USER || 'docente', password: process.env.EMS_VERIFY_PASSWORD || 'teacher-change-me' },
  })
  assert.ok(login.ok(), 'Login required')
  const auth = await login.json()
  await page.context().addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(auth.access_token), url: 'http://127.0.0.1:5173' },
    { name: 'ems-user', value: JSON.stringify(auth.user), url: 'http://127.0.0.1:5173' },
  ])
  const operations = [
    { id: 'system.status', label: 'Consultar estado', description: 'Estado del servicio.', category: 'Diagnóstico', allowed: true, mutating: false, parameters: [] },
    { id: 'system.logs', label: 'Ver logs', description: 'Logs del servicio.', category: 'Diagnóstico', allowed: true, mutating: false, parameters: [{ id: 'lines', label: 'Líneas', type: 'number', default: 100, minimum: 1, maximum: 500 }] },
    { id: 'system.restart', label: 'Reiniciar', description: 'Reinicio del servicio.', category: 'Acciones', allowed: true, mutating: true, parameters: [] },
  ]
  const components = ['amf', 'udm'].map(id => ({ id, label: id.toUpperCase(), node_id: 'test-vm', unit: `open5gs-${id}d`, status: 'running', operations }))
  components[1].operations = [...operations, { id: 'subscriber.create', label: 'ADD 5G-SUB', description: 'Crear suscriptor', category: 'UDM', allowed: true, mutating: true, parameters: [
    { id: 'imsi', label: 'IMSI', type: 'text', required: true },
    { id: 'sst', label: 'SST', type: 'number', required: true },
    { id: 'sd', label: 'SD', type: 'text', required: true },
    { id: 'apn_dnn', label: 'DNN', type: 'text', required: true },
  ] }]
  let calls = 0
  await page.route('**/api/v1/operations/**', async route => {
    const url = route.request().url()
    if (url.includes('/catalog/')) return route.fulfill({ json: { scenario_id: '5g-sa', execution_mode: 'simulated', components, native_discovery: { available: false, message: null } } })
    if (url.includes('/history')) return route.fulfill({ json: [] })
    if (url.endsWith('/execute')) {
      calls++
      const payload = route.request().postDataJSON()
      if (payload.operation_id === 'subscriber.create') return route.fulfill({ status: 409, json: { detail: 'El IMSI ya existe', result: { ...payload, id: 'c7-conflict-run-id', component_label: 'UDM', operation_label: 'ADD 5G-SUB', status: 'failed', source: 'UI TEST', output: 'El IMSI ya existe', error: 'El IMSI ya existe', data: null, username: 'docente', role: 'teacher', duration_ms: 1, started_at: new Date().toISOString() } } })
      assert.equal(payload.component_id, 'udm')
      assert.equal(payload.operation_id, 'system.status')
      return route.fulfill({ json: { ...payload, id: 'ui-test-only', component_label: 'UDM', operation_label: 'Consultar estado', status: 'success', source: 'UI TEST', output: 'TEST RESULT', parameters: {}, username: 'docente', role: 'teacher', duration_ms: 1, started_at: new Date().toISOString() } })
    }
    return route.abort()
  })
  await page.goto('http://127.0.0.1:5173/commands')
  await page.getByRole('button', { name: /^UDM/ }).click()
  assert.equal(await page.getByText('Modo experto', { exact: true }).count(), 0)
  assert.equal(await page.getByText('Comando generado', { exact: true }).count(), 0)
  await page.getByRole('button', { name: 'Manual', exact: true }).click()
  await page.getByRole('heading', { name: 'Manual de comandos' }).waitFor()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: 'Catálogo', exact: true }).click()
  await page.getByRole('button', { name: /DSP NF-STATUS/ }).click()
  const input = page.getByRole('textbox', { name: 'Comando MML' })
  assert.match(await input.inputValue(), /NF="UDM"/)
  await input.fill('DSP NF-STATUS;')
  await input.press('Control+Enter')
  await page.getByLabel('Salida del comando').filter({ hasText: 'TEST RESULT' }).waitFor()
  assert.equal(calls, 1, 'Keyboard shortcut must execute only once')
  await input.fill('DSP NF-STATUS: NF="AMF";')
  await page.getByRole('button', { name: 'Ejecutar', exact: true }).click()
  await page.getByText('El destino del comando no coincide con el nodo seleccionado', { exact: true }).waitFor()
  assert.equal(calls, 1)
  await input.fill('DSP NF-STATUS: BAD=1;')
  await page.getByRole('button', { name: 'Ejecutar', exact: true }).click()
  await page.getByText('Parámetros no soportados: bad.', { exact: true }).waitFor()
  assert.equal(calls, 1)
  await input.fill('RST NF;')
  await input.press('Control+Enter')
  await page.getByRole('heading', { name: 'Confirmar operación' }).waitFor()
  assert.equal(calls, 1, 'Mutating operation must not run before confirmation')
  await page.getByRole('button', { name: 'Cancelar', exact: true }).click()
  assert.equal(calls, 1)
  await page.getByRole('button', { name: 'Catálogo', exact: true }).click()
  await page.getByRole('button', { name: /LST NF-LOG/ }).click()
  await page.locator('input[type=number]').fill('50')
  assert.match(await input.inputValue(), /LINES=50/)
  await input.fill('ADD 5G-SUB: IMSI="999700000000006", SST=3, SD="000003", DNN="corporate";')
  await page.getByText(/ADD rechaza un IMSI existente/).waitFor()
  await input.press('Control+Enter')
  await page.getByRole('button', { name: 'Confirmar ejecución', exact: true }).click()
  const conflict = page.getByLabel('Salida del comando')
  await conflict.filter({ hasText: 'c7-conflict-run-id' }).waitFor()
  assert.match(await conflict.textContent(), /RETCODE = 1001/)
  assert.equal(calls, 2)
  await page.setViewportSize({ width: 1100, height: 800 })
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), 'No horizontal page overflow')
  assert.deepEqual(errors, [])
  console.log('PASS: clean layout, manual, catalog, parameters, single execution, target validation, invalid parameters, confirmation cancellation, responsive width.')
} finally {
  await browser.close()
}
