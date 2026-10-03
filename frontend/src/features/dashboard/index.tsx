import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Activity,
  Cpu,
  Network,
  Radio,
  RotateCcw,
} from 'lucide-react'
import { toast } from 'sonner'
import { useScenarioStore } from '@/stores/scenario-store'
import {
  api,
  type Experiment,
  type Metrics,
  type RuntimeSnapshot,
  type ScenarioStatus,
} from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Main } from '@/components/layout/main'
import { EmsTopology } from '@/features/topology/ems-topology'

export function Dashboard() {
  const scenario = useScenarioStore((state) => state.scenario)

  const status = useQuery({
    queryKey: ['status', scenario],
    queryFn: async () =>
      (await api.get<ScenarioStatus>(`/scenarios/${scenario}/status`)).data,
    refetchInterval: 3000,
  })

  const runtime = useQuery({
    queryKey: ['runtime', scenario],
    queryFn: async () =>
      (await api.get<RuntimeSnapshot>(`/runtime/${scenario}`)).data,
    refetchInterval: 3000,
  })

  const alarmCenter = useQuery({
    queryKey: ['alarm-center', scenario],
    queryFn: async () =>
      (
        await api.get<{
          items: {
            id: string
            component: string
            node_id?: string
            network_function: string
            severity: string
            message: string
            evidence?: string
            procedures?: string[]
            first_seen: number
            masked?: boolean
            silenced_until?: number
          }[]
          counts: Record<string, number>
          total: number
        }>(`/alarm-center/${scenario}`, {
          params: { visibility: 'visible' },
        })
      ).data,
    refetchInterval: 3000,
  })

  const metrics = useQuery({
    queryKey: ['metrics', scenario],
    queryFn: async () =>
      (await api.get<Metrics>(`/metrics?scenario_id=${scenario}`)).data,
    refetchInterval: 2500,
  })

  const experiments = useQuery({
    queryKey: ['experiments', scenario],
    queryFn: async () =>
      (await api.get<Experiment[]>(`/experiments/catalog/${scenario}`)).data,
    refetchInterval: 3000,
  })

  const [injectingId, setInjectingId] = useState<string | null>(null)

  const handleExperimentToggle = async (exp: Experiment) => {
    setInjectingId(exp.id)
    const isInjected = exp.state?.status === 'injected'
    const action = isInjected ? 'recover' : 'inject'
    try {
      const resp = await api.post(
        `/experiments/${exp.id}/${action}?scenario_id=${scenario}`
      )
      if (action === 'inject') {
        toast.error(`Falla inyectada: ${exp.title}`, {
          description:
            resp.data.state?.message || 'Condición de falla activada.',
        })
      } else {
        toast.success(`Servicio restablecido: ${exp.title}`, {
          description: resp.data.state?.message || 'Estado nominal recuperado.',
        })
      }
      await Promise.all([
        experiments.refetch(),
        status.refetch(),
        alarmCenter.refetch(),
        runtime.refetch(),
        metrics.refetch(),
      ])
    } catch {
      toast.error(`Error al ejecutar acción sobre ${exp.id}`)
    } finally {
      setInjectingId(null)
    }
  }

  const active =
    status.data?.components.filter((item) => item.status === 'running')
      .length ?? 0
  const total = status.data?.components.length ?? 0

  const ogstunTraffic = metrics.data?.interfaces?.ogstun
  const totalUserPlaneKbps =
    (ogstunTraffic?.rx_kbps ?? 0) + (ogstunTraffic?.tx_kbps ?? 0)

  const activeNFs = metrics.data?.telco?.active_nfs ?? active
  const totalNFs = metrics.data?.telco?.total_nfs ?? total
  const isHealthy = activeNFs === totalNFs && totalNFs > 0

  return (
    <Main className='overflow-y-auto space-y-4 pb-12 pt-4 px-4 sm:px-6'>
      <h1 className='sr-only'>Resumen</h1>

      {/* METRICS ROW (4 Cards) */}
      <div className='grid gap-3 sm:grid-cols-2 xl:grid-cols-4'>
        <MetricCard
          title='Funciones de Red'
          value={`${activeNFs}/${totalNFs}`}
          status={isHealthy ? 'good' : 'warning'}
          icon={Activity}
        />
        <MetricCard
          title='Sesiones PDU / UE'
          value={`${metrics.data?.telco?.pdu_sessions ?? 0} activa(s)`}
          status={metrics.data?.telco?.ue_registered ? 'good' : 'neutral'}
          icon={Radio}
        />
        <MetricCard
          title='Tráfico Plano Usuario (N6)'
          value={`${totalUserPlaneKbps.toFixed(1)} Kbps`}
          status={totalUserPlaneKbps > 0 ? 'good' : 'neutral'}
          icon={Network}
        />
        <MetricCard
          title='Recursos VM Host'
          value={`CPU ${metrics.data?.cpu_percent ?? 0}%`}
          status='neutral'
          icon={Cpu}
        />
      </div>

      {/* TOPOLOGY & LIVE TELEMETRY / ALARMS */}
      <div className='grid min-h-0 gap-4 lg:h-[500px] lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]'>
        {/* Topology Card */}
        <div className='flex h-[480px] min-h-0 min-w-0 flex-col rounded-xl border border-border/60 bg-card overflow-hidden lg:h-full'>
          <div className='flex items-center border-b border-border/50 px-4 py-2.5'>
            <div className='flex items-center gap-2'>
              <span className='size-2 rounded-full bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]' />
              <h2 className='text-xs font-semibold tracking-tight text-foreground uppercase'>
                Topología en Vivo
              </h2>
            </div>
          </div>
          <div className='min-h-0 flex-1 relative bg-background/50'>
            <EmsTopology
              components={status.data?.components ?? []}
              alarms={alarmCenter.data?.items ?? []}
            />
          </div>
        </div>

        {/* Telemetry and Alarms */}
        <div className='grid min-h-0 min-w-0 gap-4 lg:h-full lg:grid-rows-2'>
          {/* Live Throughput Sparkline */}
          <div className='flex flex-col min-h-0 rounded-xl border border-border/60 bg-card p-3.5'>
            <div className='flex items-center justify-between pb-2 border-b border-border/40'>
              <h3 className='text-xs font-semibold tracking-tight text-foreground uppercase'>
                Telemetría en Vivo
              </h3>
              <div className='flex items-center gap-2 font-mono text-[10px] text-muted-foreground'>
                <span className='flex items-center gap-1'>
                  <span className='size-1.5 rounded-full bg-emerald-500' />
                  ogstun (N6)
                </span>
                <span className='flex items-center gap-1'>
                  <span className='size-1.5 rounded-full bg-sky-400' />
                  lo (SBI)
                </span>
              </div>
            </div>
            <div className='min-h-0 flex-1 pt-2'>
              <ThroughputChart history={metrics.data?.history} />
            </div>
          </div>

          {/* Alarms Panel */}
          <div className='flex flex-col min-h-0 rounded-xl border border-border/60 bg-card overflow-hidden'>
            <div className='flex items-center justify-between border-b border-border/40 px-3.5 py-2.5'>
              <div className='flex items-center gap-2'>
                <h3 className='text-xs font-semibold tracking-tight text-foreground uppercase'>
                  Alarmas Telco Activas
                </h3>
                <Link
                  to='/alarms'
                  className='text-[10px] text-muted-foreground hover:text-foreground transition-colors'
                >
                  Ver todas →
                </Link>
              </div>
              <div className='flex items-center gap-1 font-mono text-[10px]'>
                {alarmCenter.data?.counts?.critical ? (
                  <span className='rounded bg-red-600/90 px-1.5 py-0.5 font-bold text-white shadow-xs'>
                    {alarmCenter.data.counts.critical} CRIT
                  </span>
                ) : null}
                {alarmCenter.data?.counts?.major ? (
                  <span className='rounded bg-amber-500/90 px-1.5 py-0.5 font-bold text-white shadow-xs'>
                    {alarmCenter.data.counts.major} MAJ
                  </span>
                ) : null}
                <Badge
                  variant={alarmCenter.data?.total ? 'destructive' : 'secondary'}
                  className='font-mono text-[10px] h-5'
                >
                  {alarmCenter.data?.total ?? 0}
                </Badge>
              </div>
            </div>
            <div className='min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2.5'>
              {alarmCenter.data?.items?.length ? (
                alarmCenter.data.items.map((alarm) => (
                  <div
                    key={alarm.id}
                    className={`rounded-lg border border-border/50 bg-background/50 p-2 text-xs transition-colors ${
                      alarm.severity === 'critical'
                        ? 'border-l-2 border-l-red-500'
                        : 'border-l-2 border-l-amber-500'
                    }`}
                  >
                    <div className='flex items-center justify-between gap-1'>
                      <span className='font-mono font-bold text-[11px] text-foreground'>
                        {alarm.network_function || alarm.component}
                      </span>
                      <span
                        className={`font-mono text-[9px] font-semibold uppercase px-1 rounded ${
                          alarm.severity === 'critical'
                            ? 'bg-red-500/20 text-red-400'
                            : 'bg-amber-500/20 text-amber-400'
                        }`}
                      >
                        {alarm.severity}
                      </span>
                    </div>
                    <p className='mt-0.5 text-[11px] text-muted-foreground truncate'>
                      {alarm.message}
                    </p>
                    {alarm.procedures && alarm.procedures.length > 0 && (
                      <p className='mt-0.5 font-mono text-[9px] text-muted-foreground/70'>
                        Proc: {alarm.procedures.join(', ')}
                      </p>
                    )}
                  </div>
                ))
              ) : (
                <div className='flex h-full min-h-[90px] flex-col items-center justify-center text-center'>
                  <span className='text-xs text-muted-foreground'>
                    Sin incidentes activos · Testbed 100% nominal
                  </span>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* LABORATORIO DE FALLAS */}
      <div className='rounded-xl border border-border/60 bg-card overflow-hidden'>
        <div className='border-b border-border/40 px-4 py-3'>
          <h2 className='text-xs font-semibold tracking-tight text-foreground uppercase'>
            Laboratorio de Inyección de Fallas
          </h2>
        </div>
        <div className='overflow-x-auto'>
          <table className='w-full text-left text-sm'>
            <thead className='border-b border-border/40 bg-muted/20 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground'>
              <tr>
                <th className='px-4 py-2.5 font-medium'>Prueba</th>
                <th className='px-4 py-2.5 font-medium'>Interfaces</th>
                <th className='px-4 py-2.5 font-medium'>Estado</th>
                <th className='px-4 py-2.5 font-medium'>Tiempo</th>
                <th className='px-4 py-2.5 text-right font-medium'>Acción</th>
              </tr>
            </thead>
            <tbody className='divide-y divide-border/30'>
              {experiments.data?.map((exp) => {
                const isInjected = exp.state?.status === 'injected'
                const isWorking = injectingId === exp.id
                return (
                  <tr
                    key={exp.id}
                    className={`transition-colors ${
                      isInjected
                        ? 'bg-red-500/5 hover:bg-red-500/10'
                        : 'hover:bg-muted/10'
                    }`}
                  >
                    <td className='px-4 py-2.5'>
                      <div className='font-medium text-foreground text-xs'>
                        {exp.title}
                      </div>
                      <div className='text-[10px] text-muted-foreground font-mono truncate max-w-md'>
                        {exp.expected_detection}
                      </div>
                    </td>
                    <td className='px-4 py-2.5 whitespace-nowrap'>
                      <div className='flex items-center gap-1 flex-wrap'>
                        {exp.interfaces?.length ? (
                          exp.interfaces.map((iface) => (
                            <span
                              key={iface}
                              className='font-mono text-[9px] rounded bg-muted/60 px-1.5 py-0.5 text-muted-foreground border border-border/50'
                            >
                              {iface}
                            </span>
                          ))
                        ) : (
                          <span className='text-muted-foreground text-xs'>—</span>
                        )}
                      </div>
                    </td>
                    <td className='px-4 py-2.5 whitespace-nowrap'>
                      {isInjected ? (
                        <span className='inline-flex items-center gap-1.5 rounded-full border border-red-500/30 bg-red-500/10 px-2 py-0.5 text-[11px] font-medium text-red-400'>
                          <span className='size-1.5 rounded-full bg-red-500 animate-ping' />
                          Falla activa
                        </span>
                      ) : (
                        <span className='inline-flex items-center gap-1.5 rounded-full border border-border/40 bg-muted/30 px-2 py-0.5 text-[11px] text-muted-foreground'>
                          <span className='size-1.5 rounded-full bg-emerald-500/80' />
                          Nominal
                        </span>
                      )}
                    </td>
                    <td className='px-4 py-2.5 font-mono text-xs whitespace-nowrap text-muted-foreground'>
                      {isInjected
                        ? `${exp.state?.elapsed_seconds ?? 0} s`
                        : '—'}
                    </td>
                    <td className='px-4 py-2.5 text-right whitespace-nowrap'>
                      <Button
                        size='sm'
                        variant={isInjected ? 'destructive' : 'outline'}
                        className='h-7 text-xs px-3 font-medium min-w-24'
                        disabled={injectingId !== null}
                        onClick={() => handleExperimentToggle(exp)}
                      >
                        {isWorking ? (
                          isInjected ? 'Restaurando…' : 'Inyectando…'
                        ) : isInjected ? (
                          <>
                            <RotateCcw className='size-3 mr-1' />
                            Restaurar
                          </>
                        ) : (
                          'Inyectar'
                        )}
                      </Button>
                    </td>
                  </tr>
                )
              })}
              {(experiments.isLoading ||
                experiments.isError ||
                !experiments.data?.length) && (
                <tr>
                  <td
                    colSpan={5}
                    className='px-4 py-6 text-center text-xs text-muted-foreground'
                  >
                    {experiments.isLoading
                      ? 'Cargando catálogo de fallas…'
                      : experiments.isError
                        ? 'No se pudo cargar el catálogo de fallas.'
                        : 'No hay pruebas registradas para este escenario.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </Main>
  )
}

function MetricCard({
  title,
  value,
  subtitle,
  status = 'neutral',
  icon: Icon,
}: {
  title: string
  value: string
  subtitle?: string
  status?: 'good' | 'warning' | 'neutral'
  icon: React.ElementType
}) {
  const dotColor =
    status === 'good'
      ? 'bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]'
      : status === 'warning'
        ? 'bg-amber-500 shadow-[0_0_6px_rgba(245,158,11,0.7)]'
        : 'bg-zinc-500'

  return (
    <div className='flex flex-col justify-between rounded-xl border border-border/60 bg-card p-3.5 transition-all duration-200 hover:border-border'>
      <div className='flex items-center justify-between'>
        <span className='text-[10px] font-semibold tracking-wider uppercase text-muted-foreground'>
          {title}
        </span>
        <div className='flex size-6 items-center justify-center rounded-md border border-border/40 bg-muted/30 text-muted-foreground'>
          <Icon className='size-3.5' />
        </div>
      </div>
      <div className='mt-2'>
        <div className='font-mono text-2xl font-bold tracking-tight text-foreground'>
          {value}
        </div>
        {subtitle && (
          <div className='mt-1 flex items-center gap-1.5 text-xs text-muted-foreground'>
            <span className={`size-1.5 shrink-0 rounded-full ${dotColor}`} />
            <span className='truncate'>{subtitle}</span>
          </div>
        )}
      </div>
    </div>
  )
}

function ThroughputChart({
  history,
}: {
  history?: { time: string; ogstun_kbps: number; lo_kbps: number }[]
}) {
  if (!history || history.length < 2) {
    return (
      <div className='flex h-full min-h-24 items-center justify-center text-xs text-muted-foreground'>
        Recolectando telemetría...
      </div>
    )
  }

  const maxVal = Math.max(
    ...history.map((d) => Math.max(d.ogstun_kbps, d.lo_kbps)),
    10
  )
  const width = 400
  const height = 120
  const paddingX = 2
  const paddingTop = 6
  const paddingBottom = 4

  const getPoints = (key: 'ogstun_kbps' | 'lo_kbps') =>
    history
      .map((d, i) => {
        const x = paddingX + (i / (history.length - 1)) * (width - 2 * paddingX)
        const y =
          height - paddingBottom - (d[key] / maxVal) * (height - paddingTop - paddingBottom)
        return `${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')

  const ogstunPoints = getPoints('ogstun_kbps')
  const loPoints = getPoints('lo_kbps')

  // Create area polygon for ogstun
  const firstX = paddingX
  const lastX = width - paddingX
  const bottomY = height - paddingBottom
  const ogstunArea = `${firstX},${bottomY} ${ogstunPoints} ${lastX},${bottomY}`

  return (
    <div className='h-full flex flex-col min-h-0'>
      <div className='flex items-center justify-end text-[10px] text-muted-foreground mb-1'>
        <span className='font-mono font-medium text-foreground'>
          Pico: {maxVal.toFixed(1)} Kbps
        </span>
      </div>
      <div className='relative w-full flex-1 min-h-[90px]'>
        <svg
          viewBox={`0 0 ${width} ${height}`}
          preserveAspectRatio='none'
          className='h-full w-full'
        >
          <defs>
            <linearGradient id='ogstunGrad' x1='0' y1='0' x2='0' y2='1'>
              <stop offset='0%' stopColor='rgb(16 185 129)' stopOpacity='0.25' />
              <stop offset='100%' stopColor='rgb(16 185 129)' stopOpacity='0.0' />
            </linearGradient>
          </defs>
          {/* Baseline grid */}
          <line
            x1='0'
            y1={bottomY}
            x2={width}
            y2={bottomY}
            stroke='currentColor'
            className='text-border/40'
            strokeWidth='1'
          />
          {/* Fill area under ogstun */}
          <polygon points={ogstunArea} fill='url(#ogstunGrad)' />
          {/* lo traffic (Control) */}
          <polyline
            fill='none'
            stroke='rgb(56 189 248)'
            strokeWidth='1.5'
            strokeDasharray='3 3'
            points={loPoints}
            vectorEffect='non-scaling-stroke'
          />
          {/* ogstun traffic (User) */}
          <polyline
            fill='none'
            stroke='rgb(16 185 129)'
            strokeWidth='2'
            points={ogstunPoints}
            vectorEffect='non-scaling-stroke'
          />
        </svg>
      </div>
    </div>
  )
}
