import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { useAuthStore } from '@/stores/auth-store'
import { useScenarioStore } from '@/stores/scenario-store'
import { api, canOperate } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { EmsPage } from '@/features/ems-page'

type Prediction = {
  snssai: { sst: number; sd?: string }
  created: number
  fresh: boolean
  input_hash: string
  evidence: {
    status?: string
    capacity_units?: number
    model: string
    nominal_coverage: number
    coverage_guaranteed: boolean
    history?: { timestamp: number; value: number }[]
    points: {
      horizon_seconds: number
      value: number
      lower: number
      upper: number
    }[]
  }
  observation?: Omit<Prediction, 'snssai' | 'observation'>
}
type Status = {
  connected: boolean
  contract: string | null
  closed_loop_enabled: boolean
  ingestion: { status?: string }
}
type Decision = {
  id: number
  received_at: number
  supi: string
  policy_id: string
  action: string
  stage: string
  elapsed_ms?: number
}
type Analytics = {
  status?: string
  message?: string
  player_observation?: {
    fresh: boolean
    received_at: number
    state: string
    startup_seconds: number | null
    played_seconds: number
    rebuffer_count: number
    rebuffer_seconds: number
    received_bytes: number
  } | null
  expiry?: string
  timeStampGen?: string
  abnorBehavrs?: { excep: { excepId: string } }[]
  svcExps?: { svcExprc: { mos: number } }[]
}
const time = (stamp: number) =>
  new Date(stamp * 1000).toLocaleTimeString('es-PE', {
    hour: '2-digit',
    minute: '2-digit',
  })

export function NwdafPage() {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const scenario = useScenarioStore((s) => s.scenario)
  const user = useAuthStore((s) => s.auth.user)
  const enabled = scenario === '5g-sa' && canOperate(user?.role)
  const [slice, setSlice] = useState('')
  const [series, setSeries] = useState<'live' | 'forecast'>('live')
  const ledger = useQuery({
    queryKey: ['nwdaf', 'ledger'],
    queryFn: async () =>
      (await api.get<{ items: Decision[] }>('/nwdaf/closed-loop/history')).data,
    enabled,
    refetchInterval: 15000,
    retry: false,
  })
  const [supiInput, setSupiInput] = useState('')
  const [appInput, setAppInput] = useState('stream5g')
  const [target, setTarget] = useState({ supi: '', app: '' })
  const status = useQuery({
    queryKey: ['nwdaf', 'status'],
    queryFn: async () => (await api.get<Status>('/nwdaf/status')).data,
    enabled,
    refetchInterval: 15000,
    retry: false,
  })
  const predictions = useQuery({
    queryKey: ['nwdaf', 'predictions'],
    queryFn: async () =>
      (await api.get<{ items: Prediction[] }>('/nwdaf/predictions')).data,
    enabled,
    refetchInterval: 5000,
    retry: false,
  })
  const anomaly = useQuery({
    queryKey: ['nwdaf', 'anomaly', target.supi],
    queryFn: async () =>
      (
        await api.get<Analytics>('/nwdaf/analytics/ABNORMAL_BEHAVIOUR', {
          params: { supi: target.supi },
        })
      ).data,
    enabled: enabled && !!target.supi,
    refetchInterval: 15000,
    retry: false,
  })
  const experience = useQuery({
    queryKey: ['nwdaf', 'experience', target],
    queryFn: async () =>
      (
        await api.get<Analytics>('/nwdaf/analytics/SERVICE_EXPERIENCE', {
          params: { supi: target.supi, app_id: target.app },
        })
      ).data,
    enabled: enabled && !!target.supi && !!target.app,
    refetchInterval: 15000,
    retry: false,
  })
  const items = predictions.isError ? [] : (predictions.data?.items ?? [])
  const selected =
    items.find((item) => JSON.stringify(item.snssai) === slice) ?? items[0]
  const online = !status.isError && status.data?.connected === true
  const current = selected?.observation ?? selected
  const currentHistory = current?.evidence.history ?? []
  const currentPoint = currentHistory[currentHistory.length - 1]
  const evidence = series === 'live' ? current?.evidence : selected?.evidence
  const history = evidence?.history ?? []
  const last = history[history.length - 1]
  const valid = (value?: Analytics) =>
    !!value?.expiry && Date.parse(value.expiry) > now
  const mos =
    online && !experience.isError && valid(experience.data)
      ? experience.data?.svcExps?.[0]?.svcExprc.mos
      : undefined
  const exceptions =
    online && !anomaly.isError && valid(anomaly.data)
      ? anomaly.data?.abnorBehavrs
      : undefined
  const playback = !experience.isError ? experience.data?.player_observation : undefined
  const playbackFresh = playback?.fresh && now / 1000 - playback.received_at <= 60
  const experienceMessage = !target.supi
    ? 'Introduce el IMSI o SUPI y pulsa Consultar.'
    : experience.isFetching
      ? 'Consultando experiencia del suscriptor…'
      : experience.isError
        ? 'No se pudo consultar la experiencia. Pulsa Consultar para reintentar.'
        : mos !== undefined
          ? 'MOS publicado por NWDAF a partir de metadatos audiovisuales y pausas.'
          : playback
            ? 'Stream5G publica tiempos y pausas del reproductor; estas mediciones no son un MOS P.1203.'
            : experience.data?.status === 'no_data'
              ? `Consulta completada: no hay mediciones para ${target.supi} / ${target.app}. Inicia Stream5G en ese terminal; los tiempos y pausas aparecerán aquí.`
              : 'El reporte de experiencia venció. Consulta nuevamente o inicia una reproducción.'
  const rows = [
    ...history.map((point, index) => ({
      timestamp: point.timestamp,
      observed: point.value,
      projected: index === history.length - 1 ? point.value : undefined,
      band: undefined as number[] | undefined,
    })),
    ...(last && series === 'forecast' ? (selected?.evidence.points ?? []) : []).map((point) => ({
      timestamp: last!.timestamp + point.horizon_seconds,
      observed: undefined,
      projected: point.value,
      band: [point.lower, point.upper],
    })),
  ]
  if (!enabled)
    return (
      <EmsPage title='NWDAF'>
        <p className='p-6 text-sm text-muted-foreground'>
          {scenario !== '5g-sa'
            ? 'NWDAF está disponible en el escenario 5G SA.'
            : 'Esta vista requiere permisos de operador.'}
        </p>
      </EmsPage>
    )
  return (
    <EmsPage
      title='Analítica NWDAF'
      headerLeft={
        <div className='flex items-center gap-3 text-sm'>
          <span
            className={`size-2 rounded-full ${online ? 'bg-emerald-500' : 'bg-muted-foreground'}`}
          />
          <span>
            {online
              ? 'NWDAF conectado'
              : status.isPending
                ? 'Consultando NWDAF'
                : 'NWDAF sin conexión'}
          </span>
          <span className='hidden text-xs text-muted-foreground sm:inline'>
            {status.data?.contract}
          </span>
        </div>
      }
      actions={
        <Button
          size='sm'
          variant='outline'
          disabled={status.isFetching || predictions.isFetching}
          onClick={() => {
            void status.refetch()
            void predictions.refetch()
            if (target.supi) {
              void anomaly.refetch()
              void experience.refetch()
            }
          }}
        >
          <RefreshCw className='size-4' />
          Actualizar
        </Button>
      }
    >
      <div className='grid gap-3 md:grid-cols-3'>
        <Metric
          label='Carga del slice'
          value={
            online && current?.fresh && currentPoint
              ? `${currentPoint.value.toFixed(1)} %`
              : '—'
          }
          detail={
            selected
              ? `SST ${selected.snssai.sst}${selected.snssai.sd ? ` · SD ${selected.snssai.sd}` : ''}${!current?.fresh ? ' · dato vencido' : ''}${current?.evidence.capacity_units ? ` · capacidad ${(current.evidence.capacity_units / 1000000).toFixed(0)} Mbps` : ''}`
              : 'Sin observaciones publicadas'
          }
        />
        <Metric
          label='Comportamiento anómalo'
          value={exceptions ? `${exceptions.length} detección` : '—'}
          detail={
            !target.supi
              ? 'Seleccione un suscriptor'
              : anomaly.isError
                ? 'Consulta no disponible'
                : (exceptions?.[0]?.excep.excepId ??
                  'Sin reporte vigente; no implica ausencia de anomalías')
          }
        />
        <Metric
          label='Experiencia de servicio'
          value={mos !== undefined ? `${mos.toFixed(2)} / 5` : '—'}
          detail={
            mos !== undefined
              ? 'MOS · ITU-T P.1203'
              : playbackFresh ? 'Hay mediciones del reproductor; consulta el detalle abajo' : 'Sin MOS publicado; consulta el detalle abajo'
          }
        />
      </div>
      <div className='mt-4 grid gap-4 xl:grid-cols-[minmax(0,1fr)_320px]'>
        <section className='min-w-0 rounded-lg border'>
          <div className='flex flex-wrap items-center justify-between gap-3 border-b p-4'>
            <h2 className='text-sm font-medium'>Carga y pronóstico</h2>
            <select aria-label='Serie temporal' value={series}
              className='rounded-md border bg-background px-3 py-1.5 text-xs'
              onChange={(e) => setSeries(e.target.value as 'live' | 'forecast')}>
              <option value='live'>Carga en vivo</option>
              <option value='forecast'>Pronóstico 15 / 30 min</option>
            </select>
            <select
              aria-label='Slice analizado'
              className='max-w-full rounded-md border bg-background px-3 py-1.5 text-xs'
              value={selected ? JSON.stringify(selected.snssai) : ''}
              onChange={(e) => setSlice(e.target.value)}
              disabled={!items.length}
            >
              <option value='' disabled>
                Sin slices disponibles
              </option>
              {items.map((item) => (
                <option
                  key={JSON.stringify(item.snssai)}
                  value={JSON.stringify(item.snssai)}
                >
                  {item.snssai.sd === '000001' ? 'Internet · ' : item.snssai.sd === '000002' ? 'Corporate · ' : ''}SST {item.snssai.sst} · SD {item.snssai.sd ?? 'sin SD'}
                </option>
              ))}
            </select>
          </div>
          {predictions.isError ? (
            <Empty text='No se pudo consultar la evidencia del pronóstico.' />
          ) : !rows.length ? (
            <Empty
              text={
                predictions.isPending
                  ? 'Consultando series…'
                  : 'Aún no hay una serie histórica suficiente para mostrar el pronóstico.'
              }
            />
          ) : (
            <>
              <div className='h-[360px] p-4 md:h-[430px]'>
                <ResponsiveContainer width='100%' height='100%'>
                  <ComposedChart
                    data={rows}
                    margin={{ top: 15, right: 10, bottom: 10, left: 0 }}
                  >
                    <CartesianGrid
                      strokeDasharray='3 3'
                      stroke='var(--border)'
                    />
                    <XAxis
                      dataKey='timestamp'
                      type='number'
                      domain={['dataMin', 'dataMax']}
                      tickFormatter={time}
                      tick={{ fontSize: 11 }}
                    />
                    <YAxis
                      domain={[0, 100]}
                      unit='%'
                      tick={{ fontSize: 11 }}
                      width={45}
                    />
                    <Tooltip
                      labelFormatter={(value) => time(Number(value))}
                      contentStyle={{
                        background: 'var(--background)',
                        borderColor: 'var(--border)',
                        fontSize: 12,
                      }}
                    />
                    <Area
                      dataKey='band'
                      name='Banda predictiva nominal 95%'
                      stroke='none'
                      fill='#0ea5e9'
                      fillOpacity={0.12}
                      isAnimationActive={false}
                    />
                    <Line
                      dataKey='observed'
                      name='Observado'
                      stroke='#0ea5e9'
                      dot={history.length === 1 ? { r: 3 } : false}
                      strokeWidth={2}
                      isAnimationActive={false}
                    />
                    <Line
                      dataKey='projected'
                      name='Pronóstico'
                      stroke='#a78bfa'
                      strokeDasharray='5 4'
                      dot={{ r: 3 }}
                      isAnimationActive={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <div className='flex flex-wrap gap-4 border-t px-4 py-3 text-xs text-muted-foreground'>
                <span className='text-sky-500'>Observado</span>
                {series === 'live' ? (
                  <span>Muestras reales del UPF · últimos minutos · actualización cada 5 s. El video descarga por ráfagas y puede quedar en búfer.</span>
                ) : selected?.evidence.points.length ? (
                  <><span className='text-violet-400'>Pronóstico · 15 / 30 min</span><span>Banda nominal 95%; cobertura no garantizada.</span></>
                ) : <span>Pronóstico aún no disponible: se muestra únicamente la carga observada.</span>}
                {!(series === 'live' ? current?.fresh : selected?.fresh) && <span>Serie histórica vencida</span>}
              </div>
            </>
          )}
        </section>
        <section className='rounded-lg border p-4'>
          <h2 className='text-sm font-medium'>Experiencia del suscriptor</h2>
          <form
            className='mt-4 space-y-3'
            onSubmit={(e) => {
              e.preventDefault()
              const input = supiInput.trim()
              const supi = input.startsWith('imsi-') ? input : `imsi-${input}`
              const app = appInput.trim()
              if (target.supi === supi && target.app === app) {
                void anomaly.refetch()
                void experience.refetch()
              } else setTarget({ supi, app })
            }}
          >
            <label className='block text-xs text-muted-foreground'>
              IMSI o SUPI
              <Input
                className='mt-1'
                required
                pattern='(?:imsi-)?[0-9]{14,15}'
                placeholder='999700000000001'
                value={supiInput}
                onChange={(e) => setSupiInput(e.target.value)}
              />
            </label>
            <label className='block text-xs text-muted-foreground'>
              Aplicación
              <Input
                className='mt-1'
                required
                maxLength={100}
                value={appInput}
                onChange={(e) => setAppInput(e.target.value)}
              />
            </label>
            <Button type='submit' variant='outline' size='sm' disabled={experience.isFetching}>
              {experience.isFetching ? 'Consultando…' : 'Consultar'}
            </Button>
          </form>
          <div className='mt-8 text-center'>
            <div className='text-4xl font-light tabular-nums'>
              {mos?.toFixed(2) ?? '—'}
            </div>
            <p className='mt-1 text-xs text-muted-foreground'>MOS · P.1203</p>
            <div
              role='meter'
              aria-label='Calidad de experiencia MOS'
              aria-valuemin={1}
              aria-valuemax={5}
              aria-valuenow={mos}
              className='mt-5 h-2 overflow-hidden rounded-full bg-muted'
            >
              <div
                className='h-full rounded-full bg-sky-500 transition-[width] duration-500 motion-reduce:transition-none'
                style={{
                  width: mos === undefined ? '0%' : `${((mos - 1) / 4) * 100}%`,
                }}
              />
            </div>
            <div className='mt-2 flex justify-between text-xs text-muted-foreground'>
              <span>1 · Baja</span>
              <span>5 · Alta</span>
            </div>
            <p role='status' aria-live='polite' className='mt-5 text-xs text-muted-foreground'>{experienceMessage}</p>
            {playback && (
              <div className='mt-4 space-y-2 rounded-md bg-muted/40 p-3 text-left text-xs'>
                <p className='font-medium'>Mediciones de Stream5G {playbackFresh ? '· recientes' : '· última reproducción, dato vencido'}</p>
                <p>Inicio: {playback.startup_seconds === null ? 'esperando primer fotograma' : `${playback.startup_seconds.toFixed(2)} s`}</p>
                <p>Reproducido: {playback.played_seconds.toFixed(1)} s</p>
                <p>Interrupciones por búfer: {playback.rebuffer_count} · {playback.rebuffer_seconds.toFixed(2)} s</p>
                <p>Descargado: {(playback.received_bytes / 1000000).toFixed(2)} MB</p>
                <p>Reporte del navegador · {time(playback.received_at)}</p>
              </div>
            )}
          </div>
        </section>
      </div>
      <section className='mt-4 overflow-hidden rounded-lg border'>
        <div className='flex flex-wrap justify-between gap-2 border-b px-4 py-3'>
          <h2 className='text-sm font-medium'>Intervenciones de política</h2>
          <span className='text-xs text-muted-foreground'>
            {!online
              ? 'Estado no disponible'
              : status.data?.closed_loop_enabled
                ? 'Habilitado · sin verificación de actuación'
                : 'Bucle cerrado desactivado'}
          </span>
        </div>
        <div className='overflow-x-auto'>
          <table className='w-full text-left text-xs'>
            <thead className='text-muted-foreground'>
              <tr>
                {[
                  'Recepción UTC',
                  'SUPI',
                  'Regla PCC',
                  'Acción',
                  'Estado',
                  'Intervalo medido',
                ].map((label) => (
                  <th key={label} className='px-4 py-3 font-normal'>
                    {label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {!ledger.isError &&
                ledger.data?.items?.map((row) => (
                  <tr key={row.id} className='border-t'>
                    <td className='px-4 py-3'>
                      {new Date(row.received_at * 1000).toISOString()}
                    </td>
                    <td className='px-4 py-3 font-mono'>{row.supi}</td>
                    <td className='px-4 py-3'>{row.policy_id}</td>
                    <td className='px-4 py-3'>
                      {row.action === 'mitigate' ? 'Mitigar' : 'Restaurar'}
                    </td>
                    <td className='px-4 py-3'>{row.stage}</td>
                    <td className='px-4 py-3'>
                      {row.elapsed_ms === undefined
                        ? '—'
                        : `${row.elapsed_ms.toFixed(1)} ms`}
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
        <p className='border-t px-4 py-6 text-center text-sm text-muted-foreground'>
          {ledger.isError
            ? 'No se pudo consultar la bitácora.'
            : ledger.data?.items?.length
              ? 'La confirmación N7 no demuestra enforcement en el UPF.'
              : 'Sin intervenciones registradas. No se infieren acciones a partir del pronóstico.'}
        </p>
      </section>
    </EmsPage>
  )
}

function Metric({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail: string
}) {
  return (
    <section className='rounded-lg border p-4'>
      <h2 className='text-xs text-muted-foreground'>{label}</h2>
      <p className='mt-3 text-2xl font-medium tabular-nums'>{value}</p>
      <p className='mt-2 text-xs break-words text-muted-foreground'>{detail}</p>
    </section>
  )
}
function Empty({ text }: { text: string }) {
  return (
    <div
      role='status'
      className='flex min-h-[360px] items-center justify-center p-8 text-center text-sm text-muted-foreground'
    >
      {text}
    </div>
  )
}
