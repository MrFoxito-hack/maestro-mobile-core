import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { useAuthStore } from '@/stores/auth-store'
import { api } from '@/lib/api'
import { UpfXdpPanel } from './upf-xdp-panel'
import { urllcKey, type UrllcStatus } from './use-urllc-xdp'

const baseline: UrllcStatus = {
  available: true,
  effective_mode: 'kernel',
  slice: 'urllc',
  namespace: 'maestro-urllc',
  interfaces: ['murllc-n3', 'murllc-mec'],
  driver_mode: 'generic',
  confirmed: false,
  ue: '10.47.0.3',
  session_generation: '123',
}
afterEach(() => vi.restoreAllMocks())
async function setup(count = 1) {
  useAuthStore.setState((s) => ({
    auth: {
      ...s.auth,
      user: { username: 'operator', role: 'teacher', testbed: 'local' },
    },
  }))
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  })
  const screen = await render(
    <QueryClientProvider client={client}>
      {Array.from({ length: count }, (_, i) => (
        <UpfXdpPanel key={i} />
      ))}
    </QueryClientProvider>
  )
  return { ...screen, client }
}
describe('URLLC shared network control', () => {
  it('targets URLLC and shares pending and confirmed state between panels', async () => {
    let current = baseline
    const get = vi
      .spyOn(api, 'get')
      .mockImplementation(async () => ({ data: current }))
    let complete!: (value: { data: UrllcStatus }) => void
    const post = vi.spyOn(api, 'post').mockImplementation(
      () =>
        new Promise((resolve) => {
          complete = resolve
        })
    )
    const screen = await setup(2)
    await expect
      .element(screen.getByRole('button', { name: 'B · XDP' }).first())
      .toBeEnabled()
    await userEvent.click(
      screen.getByRole('button', { name: 'B · XDP' }).first()
    )
    expect(post).toHaveBeenCalledWith('/upf-xdp/mode', {
      mode: 'xdp',
      slice: 'urllc',
    })
    await expect
      .element(screen.getByRole('button', { name: 'B · XDP' }).last())
      .toBeDisabled()
    await expect
      .element(screen.getByRole('status').last())
      .toHaveTextContent('Conmutando')
    current = { ...baseline, effective_mode: 'xdp', confirmed: true }
    complete({ data: current })
    await expect
      .element(screen.getByRole('status').first())
      .toHaveTextContent('Modo efectivo: xdp')
    await expect
      .element(screen.getByRole('status').last())
      .toHaveTextContent('Modo efectivo: xdp')
    expect(
      get.mock.calls.every(
        ([url, options]) =>
          url === '/upf-xdp/status' && options?.params?.slice === 'urllc'
      )
    ).toBe(true)
    screen.client.clear()
  })
  it('refetches a rollback after a failed canary and allows kernel recovery', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({ data: baseline })
    vi.spyOn(api, 'post').mockRejectedValue(
      new Error('Canary falló; retorno a kernel')
    )
    const screen = await setup()
    await expect
      .element(screen.getByRole('button', { name: 'B · XDP' }))
      .toBeEnabled()
    await userEvent.click(screen.getByRole('button', { name: 'B · XDP' }))
    await expect
      .element(screen.getByRole('alert'))
      .toHaveTextContent('Canary falló')
    await expect
      .element(screen.getByRole('status'))
      .toHaveTextContent('Modo efectivo: kernel')
    vi.mocked(api.get).mockResolvedValue({
      data: { ...baseline, available: false, reason: 'Política incompatible' },
    })
    await screen.client.invalidateQueries({ queryKey: urllcKey })
    await expect
      .element(screen.getByRole('button', { name: 'B · XDP' }))
      .toBeDisabled()
    await expect
      .element(screen.getByRole('button', { name: 'A · Kernel' }))
      .toBeEnabled()
    screen.client.clear()
  })
  it('does not present provisional XDP or failed observations as confirmed', async () => {
    vi.spyOn(api, 'get').mockResolvedValue({
      data: { ...baseline, effective_mode: 'xdp', confirmed: false },
    })
    const screen = await setup()
    await expect
      .element(screen.getByRole('status'))
      .toHaveTextContent('Conmutando')
    vi.mocked(api.get).mockRejectedValue(new Error('Agente inaccesible'))
    await screen.client.invalidateQueries({ queryKey: urllcKey })
    await expect
      .element(screen.getByRole('status'))
      .toHaveTextContent('Estado no verificado')
    await expect
      .element(screen.getByRole('button', { name: 'B · XDP' }))
      .toBeDisabled()
    await expect
      .element(screen.getByRole('button', { name: 'A · Kernel' }))
      .toBeEnabled()
    screen.client.clear()
  })
})
