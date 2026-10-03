import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import { useScenarioStore } from '@/stores/scenario-store'
import { api } from '@/lib/api'
import { NwdafPage } from './index'

beforeEach(() => {
  useAuthStore
    .getState()
    .auth.setUser({ username: 'operator', role: 'teacher', testbed: 'testbed' })
  useScenarioStore.getState().setScenario('5g-sa')
})
afterEach(() => {
  vi.restoreAllMocks()
  useAuthStore.getState().auth.reset()
})

test('shows honest empty states and no invented MOS or interventions', async () => {
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data:
      path === '/nwdaf/status'
        ? {
            connected: true,
            contract: 'TS 29.520 V16.7.0',
            closed_loop_enabled: false,
            ingestion: {},
          }
        : { items: [] },
  }))
  const screen = await render(
    <QueryClientProvider client={new QueryClient()}>
      <NwdafPage />
    </QueryClientProvider>
  )
  await expect.element(screen.getByText('NWDAF conectado')).toBeVisible()
  await expect
    .element(screen.getByText('Bucle cerrado desactivado'))
    .toBeVisible()
  await expect
    .element(
      screen.getByText(
        'Aún no hay una serie histórica suficiente para mostrar el pronóstico.'
      )
    )
    .toBeVisible()
  await expect
    .element(screen.getByRole('meter'))
    .not.toHaveAttribute('aria-valuenow')
})

test('does not query NWDAF in the EPC scenario', async () => {
  useScenarioStore.getState().setScenario('4g-epc')
  const get = vi.spyOn(api, 'get')
  const screen = await render(
    <QueryClientProvider client={new QueryClient()}>
      <NwdafPage />
    </QueryClientProvider>
  )
  await expect
    .element(screen.getByText('NWDAF está disponible en el escenario 5G SA.'))
    .toBeVisible()
  expect(get).not.toHaveBeenCalled()
})

test('consults a numeric IMSI, explains no data, and lets the user query it again', async () => {
  const get = vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data: path === '/nwdaf/status'
      ? { connected: true, ingestion: {} }
      : path.includes('/analytics/') ? { status: 'no_data' } : { items: [] },
  }))
  const screen = await render(
    <QueryClientProvider client={new QueryClient()}><NwdafPage /></QueryClientProvider>
  )
  await screen.getByRole('textbox', { name: 'IMSI o SUPI' }).fill('999700000000001')
  await screen.getByRole('button', { name: 'Consultar', exact: true }).click()
  await expect.element(screen.getByText(/Consulta completada: no hay mediciones/)).toBeVisible()
  expect(get).toHaveBeenCalledWith('/nwdaf/analytics/SERVICE_EXPERIENCE', {
    params: { supi: 'imsi-999700000000001', app_id: 'stream5g' },
  })
  const count = get.mock.calls.filter(([path]) => path.includes('SERVICE_EXPERIENCE')).length
  await screen.getByRole('button', { name: 'Consultar', exact: true }).click()
  await expect.poll(() => get.mock.calls.filter(([path]) => path.includes('SERVICE_EXPERIENCE')).length).toBe(count + 1)
})

test('shows playback measurements without presenting them as MOS', async () => {
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data: path === '/nwdaf/status' ? { connected: true, ingestion: {} }
      : path.includes('SERVICE_EXPERIENCE') ? { status: 'no_data', player_observation: {
        fresh: true, received_at: Date.now() / 1000, state: 'playing', startup_seconds: 1.2,
        played_seconds: 12, rebuffer_count: 2, rebuffer_seconds: 0.8, received_bytes: 2000000,
      } } : { items: [] },
  }))
  const screen = await render(
    <QueryClientProvider client={new QueryClient()}><NwdafPage /></QueryClientProvider>
  )
  await screen.getByRole('textbox', { name: 'IMSI o SUPI' }).fill('imsi-999700000000001')
  await screen.getByRole('button', { name: 'Consultar', exact: true }).click()
  await expect.element(screen.getByText('Reproducido: 12.0 s')).toBeVisible()
  await expect.element(screen.getByText('Interrupciones por búfer: 2 · 0.80 s')).toBeVisible()
  await expect.element(screen.getByRole('meter')).not.toHaveAttribute('aria-valuenow')
})
