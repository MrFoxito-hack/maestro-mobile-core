import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { api } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { TerminalScope } from './terminal-scope'

afterEach(() => vi.restoreAllMocks())
const sensor = (n: number) => ({ id: String(n), supi: `imsi-99970000000000${n}`, label: `Sensor ${n}`, kind: 'sensor' })
function Actions({ imsi }: { imsi: string }) {
  const [count, setCount] = useState(0)
  return <button onClick={() => {
    setCount((value) => value + 1)
    void api.post('/terminal/devices/sensor/burst', { imsi })
  }}>Acciones {count}</button>
}
async function setup(role: 'teacher' | 'student') {
  useAuthStore.setState((s) => ({ auth: { ...s.auth, user: { username: role, role, testbed: 'local' } } }))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const screen = await render(<QueryClientProvider client={client}>
    <TerminalScope kind='sensor'>{(device, selector) => <>{selector}<Actions imsi={device.supi} /></>}</TerminalScope>
  </QueryClientProvider>)
  return { screen, client }
}
it('shows only the authorized group and passes its selected IMSI', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({ data: { devices: [sensor(3)] } })
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
  const { screen, client } = await setup('student')
  await expect.element(screen.getByRole('option', { name: 'Sensor 3 · 003' })).toBeInTheDocument()
  expect(screen.getByRole('option', { name: 'Sensor 6 · 006' }).elements()).toHaveLength(0)
  await userEvent.click(screen.getByRole('button', { name: 'Acciones 0' }))
  expect(post).toHaveBeenCalledWith('/terminal/devices/sensor/burst', { imsi: sensor(3).supi })
  client.clear()
})
it('teacher switches terminals and discards the previous device local state', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({ data: { devices: [sensor(3), sensor(6)] } })
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
  const { screen, client } = await setup('teacher')
  await userEvent.click(screen.getByRole('button', { name: 'Acciones 0' }))
  await expect.element(screen.getByRole('button', { name: 'Acciones 1' })).toBeInTheDocument()
  await userEvent.selectOptions(screen.getByRole('combobox'), sensor(6).supi)
  await userEvent.click(screen.getByRole('button', { name: 'Acciones 0' }))
  expect(post).toHaveBeenLastCalledWith('/terminal/devices/sensor/burst', { imsi: sensor(6).supi })
  client.clear()
})
it('never invents a primary terminal when the authorized catalog is empty', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({ data: { devices: [] } })
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
  const { screen, client } = await setup('student')
  await expect.element(screen.getByText('Sin terminales autorizados')).toBeInTheDocument()
  expect(post).not.toHaveBeenCalled()
  client.clear()
})
