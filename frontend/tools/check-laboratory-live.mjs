// Read-only browser acceptance against an existing execution; never starts jobs.
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { chromium } from 'playwright'

const id = process.env.MAESTRO_LAB_EXECUTION_ID
const output = process.env.MAESTRO_LAB_OUTPUT
assert.match(id ?? '', /^[a-f0-9]{32}$/)
assert.ok(output && process.env.MAESTRO_CHECK_TOKEN && process.env.MAESTRO_CHECK_USER)
fs.mkdirSync(output, { recursive: true })
const browser = await chromium.launch({ headless: true, channel: 'msedge' })
let debugPage
try {
  const context = await browser.newContext({ viewport: { width: 1500, height: 1050 } })
  await context.addCookies([
    { name: 'thisisjustarandomstring', value: JSON.stringify(process.env.MAESTRO_CHECK_TOKEN), url: 'http://127.0.0.1:5173' },
    { name: 'ems-user', value: JSON.stringify({ username: process.env.MAESTRO_CHECK_USER, role: 'teacher', testbed: 'local' }), url: 'http://127.0.0.1:5173' },
  ])
  const page = await context.newPage()
  debugPage = page
  const errors = []
  page.on('pageerror', error => errors.push(error.message))
  await page.goto('http://127.0.0.1:5173/laboratory')
  await page.getByRole('button', { name: /^Piloto vivo QoE ON\/OFF/ }).click()
  const identifier = page.getByText(`Ejecución: ${id}`, { exact: true })
  await identifier.waitFor({ timeout: 20000 })
  const card = identifier.locator('..')
  const text = await card.innerText()
  assert.match(text, /completad[oa]/i)
  assert.match(text, /Validez: inconclusive/)
  assert.match(text, /Recuperación: Verificada/)
  const chart = card.getByLabel('Comparación descriptiva de espera inicial')
  assert.equal(await chart.getByRole('img').count(), 2)
  assert.match(await chart.innerText(), /NWDAF OFF/)
  assert.match(await chart.innerText(), /NWDAF ON/)
  assert.match(text, /MOS estimado P.1203/)
  assert.deepEqual(errors, [])
  await card.screenshot({ path: path.join(output, 'laboratory-real.png') })
  fs.writeFileSync(path.join(output, 'report.json'), JSON.stringify({ execution_id: id,
    read_only: true, status: 'passed', chart_bars: 2, scientific_validity: 'inconclusive',
    browser_errors: errors, text }, null, 2))
  if (process.env.MAESTRO_LAB_AI_CHECK === '1') {
    await page.getByRole('tab', { name: 'Asistente local', exact: true }).click()
    await page.getByLabel('Expediente analizado').selectOption(process.env.MAESTRO_LAB_DATASET_ID)
    await page.getByRole('button', { name: 'Consultar asistente local', exact: true }).click()
    const panel = page.getByRole('region', { name: 'Investigador local' })
    const offline = process.env.MAESTRO_LAB_AI_EXPECT_OFFLINE === '1'
    if (offline) {
      await panel.getByText('Asistente de IA fuera de línea.', { exact: false }).waitFor({ timeout: 16000 })
    } else {
      const proposal = panel.getByLabel('Propuesta del investigador local')
      await proposal.waitFor({ timeout: 16000 })
      const links = await proposal.getByRole('link').evaluateAll(nodes => nodes.map(node => node.getAttribute('href')))
      assert.ok(links.length > 0 && links.every(link => /^#evidence-[a-f0-9]{32}$/.test(link)))
      await panel.getByRole('button', { name: 'Usar revisión en mi cuaderno' }).click()
      await panel.getByText('Revisión copiada al borrador.', { exact: false }).waitFor()
    }
    assert.deepEqual(errors, [])
    await panel.screenshot({ path: path.join(output, offline ? 'assistant-offline.png' : 'assistant-real.png') })
    fs.writeFileSync(path.join(output, 'assistant-report.json'), JSON.stringify({ execution_id: id,
      mode: offline ? 'server_offline' : 'local_gpu', status: 'passed',
      commands_executed: false, browser_errors: errors, text: await panel.innerText() }, null, 2))
  }
  console.log(JSON.stringify({ execution_id: id, status: 'passed', chart_bars: 2 }))
} catch (error) {
  if (debugPage) {
    await debugPage.screenshot({ path: path.join(output, 'failure.png') }).catch(() => {})
    fs.writeFileSync(path.join(output, 'failure.json'), JSON.stringify({ url: debugPage.url(),
      text: await debugPage.locator('body').innerText().catch(() => ''), error: error.message }, null, 2))
  }
  throw error
} finally {
  await browser.close()
}
