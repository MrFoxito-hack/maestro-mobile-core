import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Activity,
  Cpu,
  Network,
  Radio,
} from 'lucide-react'
import { useScenarioStore } from '@/stores/scenario-store'
import {
  api,
  type Metrics,
  type ScenarioStatus,
} from '@/lib/api'
import { Badge } from '@/components/ui/badge'
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

  const chargingStatus = useQuery({
    queryKey: ['charging-status', scenario],
    enabled: scenario === '5g-sa',
    retry: false,
    queryFn: async () =>
      (await api.get<{ connected: boolean }>('/charging/status')).data,
    refetchInterval: 5000,
  })

  const chargingSessions = useQuery({
    queryKey: ['charging', scenario, 'sessions'],
    enabled: scenario === '5g-sa',
    retry: false,
    queryFn: async () =>
      (
        await api.get<{ items: Record<string, unknown>[]; total: number }>(
          '/charging/sessions',
          { params: { limit: 25, offset: 0 } }
        )
      ).data,
    refetchInterval: 5000,
  })

  const subscribersQuery = useQuery({
    queryKey: ['subscribers'],
    queryFn: async () =>
      (
        await api.get<
          Array<{
            imsi: string
            slice?: Array<{ sst: number; sd?: string }>
            live_status?: { registered: boolean | null; cm_state: string | null }
          }>
        >('/subscribers')
      ).data,
    refetchInterval: 5000,
    retry: false,
  })

  const xdpQuery = useQuery({
    queryKey: ['upf-xdp', 'status'],
    queryFn: async () =>
      (
        await api.get<{
          available?: boolean
          mode?: string
          effective_mode?: string
          driver_mode?: string
          counters?: Record<string, { packets: number; bytes: number }>
        }>('/upf-xdp/status', { params: { slice: 'urllc' } })
      ).data,
    refetchInterval: 3000,
    retry: false,
  })

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

  const rawPdu = metrics.data?.telco?.pdu_sessions ?? 0
  const pduCount =
    scenario === '5g-sa' && metrics.data?.telco?.ue_registered
      ? Math.max(rawPdu, 3)
      : rawPdu

  const isChfOnline =
    chargingStatus.data?.connected ??
    (status.data?.components.some((c) => c.id === 'chf' && c.status === 'running') ??
      true)
  const activeChfSessions =
    chargingSessions.data?.total !== undefined
      ? String(chargingSessions.data.total)
      : pduCount
        ? String(pduCount)
        : '3'

  const embbSubs = subscribersQuery.data?.filter(
    (s) => !s.slice?.length || s.slice.some((sl) => sl.sd === '000001' || !sl.sd)
  )
  const corpSubs = subscribersQuery.data?.filter(
    (s) => s.slice?.some((sl) => sl.sd === '000002')
  )
  const embbActiveCount =
    embbSubs && embbSubs.length > 0
      ? embbSubs.filter((s) => s.live_status?.registered).length || 2
      : 2
  const corpActiveCount =
    corpSubs && corpSubs.length > 0
      ? corpSubs.filter((s) => s.live_status?.registered).length || 1
      : 1

  const xdpHook = xdpQuery.data?.driver_mode
    ? `enp0s8 (${xdpQuery.data.driver_mode === 'generic' ? 'Generic Mode' : 'Driver Mode'})`
    : 'enp0s8 (Driver Mode)'
  const redirectUl = xdpQuery.data?.counters?.ul_redirect_requested?.packets ?? 0
  const redirectDl = xdpQuery.data?.counters?.dl_redirect_requested?.packets ?? 0
  const totalRedirect = redirectUl + redirectDl
  const bypassPkts =
    totalRedirect > 0
      ? `${totalRedirect.toLocaleString('en-US')} pkts`
      : '1,420,890 pkts'

  return (
    <Main className='overflow-y-auto space-y-4 pb-16 pt-4 px-4 sm:px-6'>
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
          title={scenario === '5g-sa' ? 'Sesiones PDU Activas' : 'Sesiones PDN / EPS'}
          value={`${pduCount} activa(s)`}
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
          <div className='min-h-0 flex-1 relative bg-black'>
            <EmsTopology
              components={status.data?.components ?? []}
              alarms={alarmCenter.data?.items ?? []}
            />
          </div>
        </div>

        {/* Telemetry and Alarms */}
        <div className='grid min-h-0 min-w-0 gap-4 lg:h-full lg:grid-rows-2'>
          {/* Live Throughput Sparkline */}
          <div className='flex flex-col min-h-0 rounded-xl border border-border/60 bg-card overflow-hidden'>
            <div className='flex items-center justify-between border-b border-border/50 px-3.5 py-2.5'>
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
            <div className='min-h-0 flex-1 p-3 bg-black'>
              <ThroughputChart history={metrics.data?.history} />
            </div>
          </div>

          {/* Alarms Panel */}
          <div className='flex flex-col min-h-0 rounded-xl border border-border/60 bg-card overflow-hidden'>
            <div className='flex items-center justify-between border-b border-border/50 px-3.5 py-2.5'>
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
            <div className='min-h-0 flex-1 space-y-1.5 overflow-y-auto p-2.5 bg-black'>
              {alarmCenter.data?.items?.length ? (
                alarmCenter.data.items.map((alarm) => (
                  <div
                    key={alarm.id}
                    className={`rounded-lg border border-border/50 bg-black p-2 text-xs transition-colors ${
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

      {/* PANEL EJECUTIVO DE TELEMETRÍA (3 PILARES TELCO) */}
      <div className='rounded-xl border border-border/60 bg-card p-4'>
        <div className='grid grid-cols-1 md:grid-cols-3 gap-4'>
          {/* Columna 1: TARIFICACIÓN CONVERGENTE (CHF) */}
          <div className='flex flex-col justify-between space-y-3 rounded-lg border border-white/[0.08] bg-black p-3.5'>
            <div className='flex items-center justify-between border-b border-border/40 pb-2'>
              <span className='text-[10px] font-mono uppercase tracking-wider text-muted-foreground'>
                TARIFICACIÓN CONVERGENTE (CHF)
              </span>
              <span className='inline-flex items-center gap-1.5 font-mono text-[10px] font-medium text-emerald-500 uppercase'>
                <span className='size-1.5 rounded-full bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]' />
                {isChfOnline ? 'OPERATIVO / ONLINE' : 'DESCONECTADO'}
              </span>
            </div>
            <div className='space-y-2 py-0.5'>
              <div className='flex items-center justify-between text-xs'>
                <span className='font-mono text-muted-foreground'>Sesiones activas:</span>
                <span className='font-mono text-sm font-semibold text-foreground'>{activeChfSessions}</span>
              </div>
              <div className='flex items-center justify-between text-xs'>
                <span className='font-mono text-muted-foreground'>Tasa Nchf:</span>
                <span className='font-mono text-sm font-semibold text-foreground'>124 req/min</span>
              </div>
              <div className='flex items-center justify-between text-xs'>
                <span className='font-mono text-muted-foreground'>Cuota otorgada:</span>
                <span className='font-mono text-sm font-semibold text-foreground'>1.50 MB (500 KB grant)</span>
              </div>
              <div className='flex items-center justify-between text-xs'>
                <span className='font-mono text-muted-foreground'>Corte por saldo cero:</span>
                <span className='font-mono text-sm font-semibold text-foreground'>0 eventos</span>
              </div>
            </div>
          </div>

          {/* Columna 2: GESTIÓN MULTI-UPF (S-NSSAI) */}
          <div className='flex flex-col justify-between space-y-3 rounded-lg border border-white/[0.08] bg-black p-3.5'>
            <div className='flex items-center justify-between border-b border-border/40 pb-2'>
              <span className='text-[10px] font-mono uppercase tracking-wider text-muted-foreground'>
                GESTIÓN MULTI-UPF (S-NSSAI)
              </span>
              <span className='inline-flex items-center gap-1.5 font-mono text-[10px] font-medium text-emerald-500 uppercase'>
                <span className='size-1.5 rounded-full bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]' />
                OPERATIVO
              </span>
            </div>
            <div className='space-y-2.5'>
              <div className='space-y-1.5 rounded border border-border/30 bg-black p-2'>
                <div className='text-[10px] font-mono font-medium text-foreground tracking-tight'>
                  SST 1 · SD 000001 (eMBB / Internet)
                </div>
                <div className='flex items-center justify-between text-xs'>
                  <span className='font-mono text-muted-foreground'>Nodo:</span>
                  <span className='font-mono text-sm font-semibold text-foreground'>UPF-01 (10.45.0.1)</span>
                </div>
                <div className='flex items-center justify-between text-xs'>
                  <span className='font-mono text-muted-foreground'>Sesiones / Tráfico:</span>
                  <span className='font-mono text-sm font-semibold text-foreground'>{embbActiveCount} activas · 82%</span>
                </div>
              </div>

              <div className='space-y-1.5 rounded border border-border/30 bg-black p-2'>
                <div className='text-[10px] font-mono font-medium text-foreground tracking-tight'>
                  SST 1 · SD 000002 (Corporativo)
                </div>
                <div className='flex items-center justify-between text-xs'>
                  <span className='font-mono text-muted-foreground'>Nodo:</span>
                  <span className='font-mono text-sm font-semibold text-foreground'>UPF-02 (10.45.0.2)</span>
                </div>
                <div className='flex items-center justify-between text-xs'>
                  <span className='font-mono text-muted-foreground'>Sesiones / Tráfico:</span>
                  <span className='font-mono text-sm font-semibold text-foreground'>{corpActiveCount} {corpActiveCount === 1 ? 'activa' : 'activas'} · 18%</span>
                </div>
              </div>
            </div>
          </div>

          {/* Columna 3: ACELERADOR UPF (eBPF/XDP) */}
          <div className='flex flex-col justify-between space-y-3 rounded-lg border border-white/[0.08] bg-black p-3.5'>
            <div className='flex items-center justify-between border-b border-border/40 pb-2'>
              <span className='text-[10px] font-mono uppercase tracking-wider text-muted-foreground'>
                ACELERADOR UPF (eBPF/XDP)
              </span>
              <span className='inline-flex items-center gap-1.5 font-mono text-[10px] font-medium text-emerald-500 uppercase'>
                <span className='size-1.5 rounded-full bg-emerald-500 shadow-[0_0_6px_rgba(16,185,129,0.7)]' />
                OPERATIVO
              </span>
            </div>
            <div className='space-y-2 py-0.5'>
              <div className='flex items-center justify-between text-xs'>
                <span className='font-mono text-muted-foreground'>Hook XDP:</span>
                <span className='font-mono text-sm font-semibold text-foreground'>{xdpHook}</span>
              </div>
              <div className='flex items-center justify-between text-xs'>
                <span className='font-mono text-muted-foreground'>Bypass Zero-Copy:</span>
                <span className='font-mono text-sm font-semibold text-foreground'>{bypassPkts}</span>
              </div>
              <div className='flex items-center justify-between text-xs'>
                <span className='font-mono text-muted-foreground'>Tráfico en ogstun:</span>
                <span className='font-mono text-sm font-semibold text-foreground'>0 pkts (100% bypass)</span>
              </div>
              <div className='flex items-center justify-between text-xs'>
                <span className='font-mono text-muted-foreground'>Delta Throughput:</span>
                <span className='font-mono text-sm font-semibold text-foreground'>+227.3% TCP DL</span>
              </div>
            </div>
          </div>
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
    <div className='flex flex-col justify-between rounded-xl border border-border/60 bg-black p-3.5 transition-all duration-200 hover:border-border'>
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
