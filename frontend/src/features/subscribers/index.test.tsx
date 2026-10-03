import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import { api } from '@/lib/api'
import { SubscribersPage } from './index'

afterEach(() => {
  vi.restoreAllMocks()
  useAuthStore.getState().auth.reset()
})

const profile = (
  suffix: string,
  registered: boolean | null,
  available: boolean | null
) => ({
  imsi: `9997000000000${suffix}`,
  terminal_label: suffix === '01' ? 'Equipo 1' : 'UE',
  security: { k: 'masked', opc: 'masked' },
  slice: [{ sst: 1, session: [{ name: 'internet' }] }],
  live_status: {
    registered,
    terminal_available: available,
    rm_state:
      registered == null
        ? null
        : registered
          ? 'RM-REGISTERED'
          : 'RM-DEREGISTERED',
    cm_state: registered == null ? null : 'CM-IDLE',
    pdu_sessions: [],
  },
})

async function mount() {
  useAuthStore
    .getState()
    .auth.setUser({ username: 'student', role: 'student', testbed: 'lab' })
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <SubscribersPage />
    </QueryClientProvider>
  )
}

test('unknown observations are not counted as deregistered and terminal filter keeps detected UEs', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({
    data: [
      profile('01', true, true),
      profile('04', false, true),
      profile('91', null, false),
      profile('92', null, null),
    ],
  })
  const screen = await mount()
  await expect
    .element(
      screen.getByRole('button', { name: 'Perfiles SIM (4)', exact: true })
    )
    .toBeVisible()
  await expect
    .element(
      screen.getByRole('button', { name: 'Registrados (1)', exact: true })
    )
    .toBeVisible()
  await screen
    .getByRole('button', { name: 'No registrados (1)', exact: true })
    .click()
  await expect
    .element(screen.getByText('999700000000004', { exact: true }))
    .toBeVisible()
  await expect
    .element(screen.getByText('999700000000091', { exact: true }))
    .not.toBeInTheDocument()
  await screen
    .getByRole('button', { name: 'Sin observación (2)', exact: true })
    .click()
  await expect
    .element(screen.getByText('999700000000091', { exact: true }))
    .toBeVisible()
  await expect
    .element(screen.getByText('DEREGISTERED', { exact: true }))
    .not.toBeInTheDocument()
  await screen
    .getByRole('button', { name: 'Terminales detectados (2)', exact: true })
    .click()
  await expect
    .element(screen.getByText('999700000000001', { exact: true }))
    .toBeVisible()
  await expect
    .element(screen.getByText('999700000000004', { exact: true }))
    .toBeVisible()
  await expect
    .element(screen.getByText('999700000000092', { exact: true }))
    .not.toBeInTheDocument()
})

test('open detail follows refreshed telemetry instead of retaining a stale status', async () => {
  let data = [profile('01', null, null)]
  vi.spyOn(api, 'get').mockImplementation(async () => ({ data }))
  const screen = await mount()
  await screen.getByRole('button', { name: 'Detalle', exact: true }).click()
  await expect
    .element(
      screen.getByText('Estado de registro desconocido', { exact: true })
    )
    .toBeVisible()
  data = [profile('01', true, true)]
  // Polling uses the same query while the detail dialog stays open.
  await expect
    .element(screen.getByText('Registrado en la Red 5G', { exact: true }))
    .toBeVisible()
  await expect
    .element(
      screen.getByText('Estado de registro desconocido', { exact: true })
    )
    .not.toBeInTheDocument()
})
