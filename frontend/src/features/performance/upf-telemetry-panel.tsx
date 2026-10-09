import { useQuery } from '@tanstack/react-query'
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useAuthStore } from '@/stores/auth-store'
import { api, apiErrorMessage } from '@/lib/api'
import { useUrllcXdp } from './use-urllc-xdp'

type Metrics = {
  ul_bps?: number
  dl_bps?: number
  ul_pps?: number
  dl_pps?: number
  active_sessions?: number
  pfcp_peers?: number
}
type Target = {
  id: string
  service: string
  label: string
  dnn: string
  sst: number
  sd: string
  status: string
  metrics: Metrics
  traffic_complete: boolean
  xdp: string
  sample_age_seconds: number | null
  history: Array<Metrics & { timestamp: number }>
}
type Snapshot = { slice_configuration: string; targets: Target[] }
const timeLabel = (value: number) =>
  new Date(value * 1000).toLocaleTimeString('es-PE')
const number = (value?: number) =>
  value === undefined
    ? '—'
    : value.toLocaleString('es-PE', { maximumFractionDigits: 2 })
const states: Record<string, string> = {
  measured: 'Telemetría activa',
  warming_up: 'Primera muestra',
  unavailable: 'Sin telemetría',
  stale: 'Datos antiguos',
}

export function UpfTelemetryPanel() {
  const urllc = useUrllcXdp()
  const user = useAuthStore((s) => s.auth.user)
  const allowed = user?.role === 'admin' || user?.role === 'teacher'
  const query = useQuery({
    queryKey: ['upf-telemetry'],
    queryFn: async () =>
      (await api.get<Snapshot>('/upf-telemetry/snapshot')).data,
    refetchInterval: 5_000,
    retry: false,
    enabled: allowed,
  })
  if (!allowed) return null
  return (
    <section
      aria-label='Telemetría de los planos de usuario'
      className='mb-5 space-y-3'
    >
      <div className='flex flex-wrap items-baseline justify-between gap-2'>
        <h2 className='text-base font-semibold'>
          Servicios 5G · Planos de usuario
        </h2>
        <span className='text-xs text-muted-foreground'>
          Ventana de 15 minutos · Actualización cada 5 s
        </span>
      </div>
      {query.data?.slice_configuration === 'planned' && (
        <p className='text-xs text-amber-600'>
          S-NSSAI de destino planificados; migración del core pendiente de
          validar.
        </p>
      )}
      {query.error && (
        <p role='alert' className='text-sm text-destructive'>
          {apiErrorMessage(query.error, 'Telemetría no disponible')}
        </p>
      )}
      {query.isPending && (
        <p className='text-sm text-muted-foreground'>
          Consultando planos de usuario…
        </p>
      )}
      <div className='grid gap-3 xl:grid-cols-3'>
        {query.data?.targets.map((target) => {
          const pps = target.service === 'miot'
          const rows = target.history.map((p) => ({
            timestamp: p.timestamp,
            ul: pps
              ? p.ul_pps
              : p.ul_bps === undefined
                ? undefined
                : p.ul_bps / 1e6,
            dl: pps
              ? p.dl_pps
              : p.dl_bps === undefined
                ? undefined
                : p.dl_bps / 1e6,
          }))
          return (
            <article
              key={target.id}
              className='min-w-0 rounded-lg border bg-card p-4'
            >
              <div className='flex items-start justify-between gap-2'>
                <h3 className='text-sm font-semibold'>{target.label}</h3>
                <span
                  className={`text-xs ${target.status === 'measured' ? 'text-emerald-600' : 'text-amber-600'}`}
                >
                  {states[target.status] ?? target.status}
                </span>
              </div>
              <p className='mt-1 text-xs text-muted-foreground'>
                {target.dnn} · SST {target.sst} / SD {target.sd}
              </p>
              <div className='my-4 grid grid-cols-3 gap-2 text-xs'>
                <div>
                  <span className='text-muted-foreground'>
                    Subida {pps ? 'pps' : 'Mbps'}
                  </span>
                  <div className='mt-1 text-lg tabular-nums'>
                    {number(
                      pps
                        ? target.metrics.ul_pps
                        : target.metrics.ul_bps === undefined
                          ? undefined
                          : target.metrics.ul_bps / 1e6
                    )}
                  </div>
                </div>
                <div>
                  <span className='text-muted-foreground'>
                    Bajada {pps ? 'pps' : 'Mbps'}
                  </span>
                  <div className='mt-1 text-lg tabular-nums'>
                    {number(
                      pps
                        ? target.metrics.dl_pps
                        : target.metrics.dl_bps === undefined
                          ? undefined
                          : target.metrics.dl_bps / 1e6
                    )}
                  </div>
                </div>
                <div>
                  <span className='text-muted-foreground'>Sesiones</span>
                  <div className='mt-1 text-lg tabular-nums'>
                    {number(target.metrics.active_sessions)}
                  </div>
                </div>
              </div>
              <div className='h-40' aria-label={`Tráfico de ${target.dnn}`}>
                {rows.some((p) => p.ul !== undefined || p.dl !== undefined) ? (
                  <ResponsiveContainer width='100%' height='100%'>
                    <LineChart data={rows}>
                      <CartesianGrid strokeDasharray='3 3' opacity={0.2} />
                      <XAxis
                        dataKey='timestamp'
                        tickFormatter={timeLabel}
                        minTickGap={45}
                        tick={{ fontSize: 10 }}
                      />
                      <YAxis width={40} tick={{ fontSize: 10 }} />
                      <Tooltip labelFormatter={(v) => timeLabel(Number(v))} />
                      <Legend wrapperStyle={{ fontSize: 11 }} />
                      <Line
                        name={`Subida (${pps ? 'pps' : 'Mbps'})`}
                        dataKey='ul'
                        stroke='#0284c7'
                        dot={false}
                        isAnimationActive={false}
                        connectNulls={false}
                      />
                      <Line
                        name={`Bajada (${pps ? 'pps' : 'Mbps'})`}
                        dataKey='dl'
                        stroke='#059669'
                        dot={false}
                        isAnimationActive={false}
                        connectNulls={false}
                      />
                    </LineChart>
                  </ResponsiveContainer>
                ) : (
                  <div className='flex h-full items-center justify-center text-xs text-muted-foreground'>
                    Esperando dos muestras continuas
                  </div>
                )}
              </div>
              <p className='mt-3 text-xs text-muted-foreground'>
                PFCP: {number(target.metrics.pfcp_peers)} peers · Muestra:{' '}
                {target.sample_age_seconds === null
                  ? '—'
                  : `${number(target.sample_age_seconds)} s`}
              </p>
              {target.service !== 'urllc' &&
                target.xdp === 'pending_integration' && (
                  <p className='mt-1 text-xs text-amber-600'>
                    Camino tradicional · Aceleración pendiente de integrar con
                    charging
                  </p>
                )}
              {target.xdp === 'attached_unverified' && (
                <p className='mt-1 text-xs text-amber-600'>
                  XDP detectado · Los contadores TUN pueden ser parciales
                </p>
              )}
              {target.service === 'urllc' && (
                <p className='mt-1 text-xs text-muted-foreground'>
                  {urllc.transitioning
                    ? 'Conmutando… canary en curso.'
                    : urllc.state.isError
                      ? 'Modo URLLC no verificado.'
                      : `Modo URLLC: ${urllc.state.data?.effective_mode ?? 'no verificado'}.`}{' '}
                  Estos gráficos muestran el UPF tradicional; durante XDP no
                  representan el tráfico total. RTT y ACK UE–MEC disponibles en
                  el Cockpit V2X de Casos Verticales.
                </p>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}
