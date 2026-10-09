import { useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, apiErrorMessage } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'

export type Device = {
  id: string
  supi: string
  label: string
  kind: 'smartphone' | 'vehicle' | 'sensor'
}

export type TerminalScopeMeta = {
  devices: Device[]
  selected: string
  setSelected: (supi: string) => void
}

export function TerminalScope({ kind, children }: {
  kind: Device['kind']
  children: (device: Device, selector: ReactNode, meta?: TerminalScopeMeta) => ReactNode
}) {
  const username = useAuthStore((s) => s.auth.user?.username)
  const [selected, setSelected] = useState('')
  const query = useQuery({
    queryKey: ['terminal-inventory', username],
    queryFn: async ({ signal }) => (await api.get<{ devices: Device[] }>('/terminal-inventory', { signal })).data,
    enabled: !!username,
    refetchInterval: 15000,
    retry: false,
  })
  if (!username) return null
  if (query.isError) return <p role='alert'>{apiErrorMessage(query.error, 'Catálogo no disponible')}</p>
  const devices = query.data?.devices.filter((d) => d.kind === kind) ?? []
  const device = devices.find((d) => d.supi === selected) ?? devices[0]
  if (!device) return <p>{query.isPending ? 'Cargando terminales…' : 'Sin terminales autorizados'}</p>
  const selector = <label className='relative z-40 block bg-background p-2 text-xs text-foreground'>
    Terminal
    <select aria-label={`Terminal ${kind}`} className='ml-2 max-w-full rounded border bg-background p-1'
      value={device.supi} onChange={(e) => setSelected(e.target.value)}>
      {devices.map((d) => <option key={d.id} value={d.supi}>{d.label} · {d.supi.slice(-3)}</option>)}
    </select>
  </label>
  // Remount local histories, streams and mutation observers on identity/account change.
  return <div key={`${username}:${device.supi}`} className='contents'>{children(device, selector, { devices, selected: device.supi, setSelected })}</div>
}
