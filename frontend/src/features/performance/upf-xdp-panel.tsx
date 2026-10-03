import { useMutation, useQuery } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { api, apiErrorMessage } from '@/lib/api'
import { Button } from '@/components/ui/button'

type Status = {
  available: boolean
  mode: 'legacy' | 'xdp'
  ue?: string
  lease_seconds?: number
  reason?: string
  counters?: Record<string, { packets: number; bytes: number }>
}

export function UpfXdpPanel() {
  const user = useAuthStore((s) => s.auth.user)
  const allowed = user?.role === 'admin' || user?.role === 'teacher'
  const state = useQuery({
    queryKey: ['upf-xdp'],
    queryFn: async () => (await api.get<Status>('/upf-xdp/status')).data,
    refetchInterval: 10_000,
    retry: false,
    enabled: allowed,
  })
  const change = useMutation({
    mutationFn: (mode: 'legacy' | 'xdp') => api.post('/upf-xdp/mode', { mode }),
    onSuccess: () => state.refetch(),
  })
  const data = state.data
  if (!allowed) return null
  return (
    <div className='mb-3 rounded-md border px-4 py-3 text-sm'>
      <div className='flex flex-wrap items-center gap-3'>
        <strong>UPF · Comparación A/B</strong>
        <span>
          {data
            ? data.mode === 'xdp'
              ? 'XDP genérico'
              : 'Tradicional'
            : 'Estado no disponible'}
        </span>
        {data?.ue && <span>UE 001 · {data.ue}</span>}
        <Button
          size='sm'
          variant='outline'
          disabled={!data?.available || change.isPending}
          onClick={() => change.mutate('legacy')}
        >
          A · Tradicional
        </Button>
        <Button
          size='sm'
          variant='outline'
          disabled={!data?.available || !data.lease_seconds || change.isPending}
          onClick={() => change.mutate('xdp')}
        >
          B · XDP experimental
        </Button>
        <Button size='sm' variant='ghost' onClick={() => state.refetch()}>
          Actualizar
        </Button>
      </div>
      <p className='mt-2 text-muted-foreground'>
        Prueba IPv4 con registro temporal de sesión. El tráfico acelerado omite
        la contabilización y las políticas de Open5GS. La activación caduca con
        el registro.
      </p>
      {data?.counters && (
        <p className='mt-1'>
          Paquetes XDP: UL{' '}
          {data.counters.ul_redirect_requested?.packets.toLocaleString()} · DL{' '}
          {data.counters.dl_redirect_requested?.packets.toLocaleString()} ·
          Registro válido por {data.lease_seconds} s
        </p>
      )}
      {data?.reason && <p>{data.reason}</p>}
      {(state.error || change.error) && (
        <p className='mt-1 text-destructive'>
          {apiErrorMessage(
            state.error || change.error,
            'No se pudo consultar o cambiar el modo del UPF'
          )}
        </p>
      )}
    </div>
  )
}
