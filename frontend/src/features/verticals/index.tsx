import { TerminalScope } from '@/features/terminal/terminal-scope'
import { useEffect, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Activity,
  AlertCircle,
  ArrowUpRight,
  CarFront,
  CheckCircle2,
  Cpu,
  Radio,
  ShieldCheck,
} from 'lucide-react'
import { useAuthStore } from '@/stores/auth-store'
import { useScenarioStore } from '@/stores/scenario-store'
import { api, apiErrorMessage } from '@/lib/api'
import { UpfXdpPanel } from '@/features/performance/upf-xdp-panel'
import {
  isTransition,
  measuredMode,
  useUrllcXdp,
  vehicleProbeKey,
  type NetworkMode,
  type VehicleObservation,
} from '@/features/performance/use-urllc-xdp'
import { Dial, Road, Trace } from '@/features/terminal/device-instruments'
import './verticals.css'

type DeviceStatus = {
  connected: boolean
  problem: string | null
  session: { address: string; interface: string } | null
}
type Measurement = {
  source_ip: string
  target: string
  sent: number
  received: number
  rtt_ms: number | null
  jitter_ms: number | null
  loss_pct: number | null
  samples_ms: number[]
  observed_at: string
  send_seconds: number
  sent_bytes: number
}
type FleetResult = Measurement & {
  virtual_sensors: number
  radio_ues: number
  sent_sensor_ids: number[]
  acked_sensor_ids: number[]
}
type UpfSample = {
  service: string
  status: string
  traffic_complete: boolean
  sample_age_seconds: number | null
  metrics: { ul_pps?: number; dl_pps?: number }
  history: { timestamp: number; ul_pps?: number }[]
}
const fmt = (v: number | null | undefined, digits = 2) =>
  v == null ? '—' : v.toLocaleString('es-PE', { maximumFractionDigits: digits })
const time = (value?: string) =>
  value ? new Date(value).toLocaleTimeString('es-PE') : 'Sin medición'
function useDevice(name: 'vehicle' | 'sensor', imsi: string) {
  const username = useAuthStore((s) => s.auth.user?.username)
  return useQuery({
    queryKey: ['terminal-device', username, imsi, name],
    queryFn: async () =>
      (await api.get<DeviceStatus>(`/terminal/devices/${name}?imsi=${encodeURIComponent(imsi)}`)).data,
    refetchInterval: 8000,
    retry: false,
  })
}
function Connection({
  connected,
  loading,
}: {
  connected: boolean
  loading: boolean
}) {
  return (
    <span className={`vertical-connection ${connected ? 'is-connected' : ''}`}>
      <i />
      {loading
        ? 'Verificando enlace'
        : connected
          ? 'PDU conectada'
          : 'Sin enlace verificado'}
    </span>
  )
}
function Metric({
  label,
  value,
  unit,
}: {
  label: string
  value: string
  unit?: string
}) {
  return (
    <div className='vertical-metric'>
      <span>{label}</span>
      <strong>
        {value}
        <small>{unit}</small>
      </strong>
    </div>
  )
}

function Mobility({ imsi }: { imsi: string }) {
  const status = useDevice('vehicle', imsi)
  const xdp = useUrllcXdp()
  const connected = !!status.data?.connected && !status.isError
  const [throttle, setThrottle] = useState(0)
  const [speed, setSpeed] = useState(0)
  const [braking, setBraking] = useState(false)
  const [monitor, setMonitor] = useState(false)
  const [history, setHistory] = useState<(number | null)[]>([])
  const [byMode, setByMode] = useState<
    Partial<Record<NetworkMode, Measurement>>
  >({})
  const probe = useMutation({
    mutationKey: [...vehicleProbeKey, imsi],
    mutationFn: async () => {
      // Compare observed sessions and modes around each real UDP measurement.
      const observe = async () =>
        (await api.get<VehicleObservation>('/terminal/devices/vehicle', { params: { imsi } })).data
      const before = await observe().catch(() => undefined)
      if (isTransition(before?.xdp))
        throw new Error('Canary en curso; espere para medir')
      const measured = (
        await api.post<Measurement>('/terminal/devices/vehicle/probe', { imsi })
      ).data
      const after = await observe().catch(() => undefined)
      return { ...measured, networkMode: measuredMode(before, after, measured) }
    },
    onSuccess: (m) => {
      const mode = m.networkMode
      if (mode) setByMode((previous) => ({ ...previous, [mode]: m }))
      setHistory((h) =>
        [...h, ...(m.samples_ms.length ? m.samples_ms : [null])].slice(-40)
      )
    },
    onError: () => setHistory((h) => [...h, null].slice(-40)),
  })
  const brake = useMutation({
    mutationFn: async () =>
      (await api.post<Measurement>('/terminal/devices/vehicle/brake', { imsi })).data,
  })
  const measure = probe.mutate
  useEffect(() => {
    const timer = setInterval(
      () =>
        setSpeed((v) =>
          braking ? Math.max(0, v - 15) : v + (throttle * 1.8 - v) * 0.08
        ),
      100
    )
    return () => clearInterval(timer)
  }, [throttle, braking])
  useEffect(() => {
    if (!monitor || !connected || probe.isPending || xdp.transitioning) return
    const timer = setInterval(() => {
      if (!document.hidden) measure()
    }, 3000)
    return () => clearInterval(timer)
  }, [monitor, connected, probe.isPending, measure, xdp.transitioning])
  const error = status.error ?? probe.error ?? brake.error
  return (
    <section
      className='vertical-card mobility-card'
      aria-labelledby='mobility-heading'
    >
      <header className='vertical-card-heading'>
        <div className='vertical-heading-icon'>
          <CarFront size={22} />
        </div>
        <div className='vertical-heading-text'>
          <h2 id='mobility-heading'>Cockpit V2X</h2>
        </div>
        <span className='vertical-slice urllc'>URLLC</span>
      </header>
      <div className='vertical-network-line'>
        <span>SST 2 · SD 000002 · UPF-03</span>
        <Connection connected={connected} loading={status.isPending} />
      </div>

      <div className='vertical-card-body'>
        <div className='mobility-cluster'>
          <div className='mobility-speed'>
            <Dial
              value={speed}
              max={200}
              unit='km/h'
              label='VELOCIDAD'
              color='#2dd4bf'
            />
            <span className='drive-indicator'>
              D <small>DRIVE</small>
            </span>
          </div>
          <div className='mobility-road'>
            <span className={braking ? 'brake-indicator' : ''}>
              <ShieldCheck size={14} />
              {braking ? 'Parada simulada' : 'Asistencia V2X'}
            </span>
            <Road moving={speed > 1} braking={braking} />
          </div>
        </div>

        <div className='vertical-throttle'>
          <label htmlFor='v2x-throttle'>
            Acelerador <span>{throttle}%</span>
          </label>
          <input
            id='v2x-throttle'
            type='range'
            min={0}
            max={100}
            value={throttle}
            onChange={(e) => {
              setThrottle(+e.target.value)
              setBraking(false)
            }}
          />
          <div
            className='vertical-tachometer'
            role='img'
            aria-label={`Tacómetro: ${Math.round(800 + speed * 32)} rpm`}
          >
            {Array.from({ length: 30 }, (_, i) => (
              <i
                key={i}
                className={i < ((800 + speed * 32) / 7500) * 30 ? 'lit' : ''}
              />
            ))}
          </div>
          <small>{fmt(800 + speed * 32, 0)} rpm</small>
        </div>

        <div className='mec-panel'>
          <UpfXdpPanel
            title={
              <span className='vertical-panel-title'>
                <Activity size={14} /> ENLACE AL EDGE MEC
              </span>
            }
            subtitle={<span className='panel-ip-badge'>172.31.48.2</span>}
            className='vertical-panel-header'
          />
          <div className='vertical-metrics'>
            <Metric
              label='RTT medido'
              value={fmt(probe.data?.rtt_ms)}
              unit='ms'
            />
            <Metric
              label='Jitter'
              value={fmt(probe.data?.jitter_ms)}
              unit='ms'
            />
            <Metric
              label='Pérdida'
              value={fmt(probe.data?.loss_pct, 1)}
              unit='%'
            />
          </div>
          <div
            className='vertical-metrics'
            aria-label='RTT medido por modo hacia MEC'
          >
            {(['kernel', 'xdp'] as const).map((mode) => (
              <div key={mode}>
                <Metric
                  label={`RTT ${mode}`}
                  value={fmt(byMode[mode]?.rtt_ms)}
                  unit='ms'
                />
                {byMode[mode] && (
                  <small>
                    {time(byMode[mode]?.observed_at)} · {byMode[mode]?.received}/{byMode[mode]?.sent} ACK
                  </small>
                )}
              </div>
            ))}
          </div>
          <Trace
            values={history}
            label='Historial de RTT UDP al MEC'
            color='#2dd4bf'
          />
          <div className='vertical-monitor-row'>
            <small>
              {probe.data
                ? `${time(probe.data.observed_at)} · ${probe.data.received}/${probe.data.sent} ACK`
                : ''}
            </small>
            <button
              className='vertical-button secondary'
              disabled={!monitor && (!connected || xdp.transitioning)}
              onClick={() => {
                if (!monitor && !probe.isPending) probe.mutate()
                setMonitor(!monitor)
              }}
            >
              <Activity size={14} />
              {monitor ? 'Detener monitor' : 'Medir enlace MEC'}
            </button>
          </div>
        </div>
      </div>

      <div className='vertical-actions-wrapper'>
        <button
          className='vertical-brake'
          disabled={!connected || brake.isPending}
          onClick={() => {
            setBraking(true)
            setThrottle(0)
            brake.mutate()
          }}
        >
          <span>
            {brake.isPending ? 'Esperando ACK…' : 'Frenado de emergencia'}
          </span>
          <span className='brake-rate-badge'>MEC ACK</span>
        </button>

        <div className='vertical-feedback' role='status'>
          {error ? (
            <span className='feedback-pill error'>
              <AlertCircle size={13} />
              {apiErrorMessage(error, 'No se pudo medir el enlace')}
            </span>
          ) : status.data?.problem ? (
            <span className='feedback-pill warning'>
              <Activity size={13} />
              {status.data.problem}
            </span>
          ) : brake.data ? (
            <span className='feedback-pill success'>
              <CheckCircle2 size={13} />
              {brake.data.received > 0
                ? `Orden V2X recibida · ACK en ${fmt(brake.data.rtt_ms)} ms`
                : 'Sin respuesta ACK'}
            </span>
          ) : (
            <span className='feedback-pill idle'>
              <span className='pulse-dot urllc' />
              {connected ? 'PDU disponible · sin ACK medido' : 'Sin enlace verificado'}
            </span>
          )}
        </div>
      </div>

      <p className='px-4 py-2 text-xs text-muted-foreground'>Velocidad y animación: estímulos simulados. RTT y ACK: mediciones UDP al MEC. La orden V2X no acredita frenado físico ni PC5.</p>
      <footer className='vertical-card-footer'>
        <div className='footer-chip-group'>
          <span className='footer-chip'>UE …{imsi.slice(-3)}</span>
          <span className='footer-chip mono'>
            {status.data?.session?.address ?? 'IP no observada'}
          </span>
        </div>
        <div className='footer-chip-group'>
          <span className='footer-chip badge-slice urllc'>DNN 5g-plus</span>
          <span className='footer-chip'>UDP echo</span>
        </div>
      </footer>
    </section>
  )
}

function MassiveTelemetry({ imsi }: { imsi: string }) {
  const status = useDevice('sensor', imsi)
  const role = useAuthStore((s) => s.auth.user?.role)
  const canReadPm = role === 'teacher' || role === 'admin'
  const [density, setDensity] = useState(100)
  const [cycle, setCycle] = useState<{
    start: number
    baseline: number | undefined
  } | null>(null)
  const telemetry = useQuery({
    queryKey: ['verticals-upf-telemetry'],
    queryFn: async () =>
      (await api.get<{ targets: UpfSample[] }>('/upf-telemetry/snapshot')).data,
    refetchInterval: 2500,
    enabled: canReadPm,
    retry: false,
  })
  const upf = telemetry.data?.targets.find((t) => t.service === 'miot')
  const complete =
    !telemetry.isError && upf?.status === 'measured' && upf.traffic_complete
  const pps = complete ? upf.metrics.ul_pps : undefined
  const burst = useMutation({
    mutationFn: async (sensors: number) =>
      (
        await api.post<FleetResult>('/terminal-devices/industrial/telemetry', {
          imsi,
          sensors,
        })
      ).data,
    onSuccess: () => {
      if (canReadPm) void telemetry.refetch()
    },
  })
  const sent = new Set(burst.data?.sent_sensor_ids ?? [])
  const acked = new Set(burst.data?.acked_sensor_ids ?? [])
  const cycleRates =
    complete && cycle
      ? upf.history
          .filter(
            (p) => p.timestamp >= cycle.start && p.timestamp <= cycle.start + 20
          )
          .flatMap((p) => (p.ul_pps == null ? [] : [p.ul_pps]))
      : []
  const peak = cycleRates.length ? Math.max(...cycleRates) : undefined
  const connected = !!status.data?.connected && !status.isError
  const error = status.error ?? burst.error
  return (
    <section
      className='vertical-card fleet-card'
      aria-labelledby='fleet-heading'
    >
      <header className='vertical-card-heading'>
        <div className='vertical-heading-icon'>
          <Cpu size={22} />
        </div>
        <div className='vertical-heading-text'>
          <h2 id='fleet-heading'>Parque de telemetría masiva</h2>
        </div>
        <span className='vertical-slice miot'>MIoT</span>
      </header>
      <div className='vertical-network-line'>
        <span>SST 3 · SD 000003 · UPF-03</span>
        <Connection connected={connected} loading={status.isPending} />
      </div>

      <div className='vertical-card-body'>
        <div className='fleet-instruments-wrapper'>
          <div className='fleet-instruments'>
            <Dial
              small
              value={68.2}
              max={120}
              unit='°C'
              label='TEMPERATURA'
              color='#f59e0b'
            />
            <Dial
              small
              value={4.6}
              max={10}
              unit='bar'
              label='PRESIÓN HIDRÁULICA'
              color='#10b981'
            />
            <Dial
              small
              value={23.4}
              max={80}
              unit='Hz'
              label='VIBRACIÓN'
              color='#60a5fa'
            />
          </div>
        </div>

        <div className='fleet-map'>
          <div className='vertical-panel-label'>
            <span>
              <Radio size={14} /> ESTADO DEL PARQUE
            </span>
            <div className='fleet-density-control'>
              <label htmlFor='fleet-density'>Densidad:</label>
              <select
                id='fleet-density'
                aria-label='Densidad de flota masiva'
                value={density}
                disabled={burst.isPending}
                onChange={(e) => {
                  setDensity(+e.target.value)
                  burst.reset()
                  setCycle(null)
                }}
              >
                {[10, 50, 100, 1000].map((n) => (
                  <option key={n} value={n}>
                    {fmt(n, 0)} sensores
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div
            className={`fleet-grid ${burst.isPending ? 'is-transmitting' : ''} ${density === 1000 ? 'dense' : ''}`}
            role='img'
            aria-label={`${density} sensores virtuales. ${burst.isPending ? 'Ciclo en curso' : `${acked.size} confirmados por ACK`}`}
          >
            {Array.from({ length: density }, (_, i) => {
              const id = i + 1
              const state = burst.isPending
                ? 'pending'
                : acked.has(id)
                  ? 'ack'
                  : sent.has(id)
                    ? 'lost'
                    : burst.data
                      ? 'unsent'
                      : 'idle'
              return (
                <span
                  key={id}
                  className={`sensor-cell ${state}`}
                  title={`Sensor ${id}: ${state === 'ack' ? 'ACK' : state === 'lost' ? 'Sin ACK' : state === 'unsent' ? 'No enviado' : state === 'pending' ? 'En curso' : 'En espera'}`}
                  style={{ animationDelay: `${(i % 17) * 0.06}s` }}
                >
                  {density <= 100 ? String(id).padStart(3, '0') : ''}
                </span>
              )
            })}
          </div>
          <div className='fleet-legend'>
            <span>
              <i />
              En espera
            </span>
            <span>
              <i className='pending' />
              En curso
            </span>
            <span>
              <i className='ack' />
              ACK
            </span>
            <span>
              <i className='lost' />
              Sin ACK
            </span>
          </div>
        </div>

        <div className='upf-panel'>
          <div className='vertical-panel-label'>
            <span>
              <Activity size={14} /> TELEMETRÍA EN UPF-03 (MIoT)
            </span>
            <Link to='/performance' className='performance-mini-link'>
              Ver Performance <ArrowUpRight size={12} />
            </Link>
          </div>
          <div className='vertical-metrics'>
            <Metric
              label='Enviados / ACK'
              value={
                burst.data ? `${burst.data.sent} / ${burst.data.received}` : '—'
              }
            />
            <Metric label='UL actual' value={fmt(pps, 1)} unit='pps' />
            <Metric label='Pico' value={fmt(peak, 1)} unit='pps' />
          </div>
          <div className='vertical-monitor-row'>
            <small>
              {complete
                ? `Muestreo ~5s · hace ${fmt(upf.sample_age_seconds, 0)}s${
                    cycle && peak != null && cycle.baseline != null
                      ? ` · Δ pico: ${fmt(peak - cycle.baseline, 1)} pps`
                      : ''
                  }`
                : ''}
            </small>
            <span className='upf-traffic-badge'>
              {complete ? 'Contadores 3GPP activos' : 'UPF Standby'}
            </span>
          </div>
        </div>
      </div>

      <div className='vertical-actions-wrapper'>
        <button
          className='vertical-button transmit-cycle'
          disabled={!connected || burst.isPending}
          onClick={() => {
            setCycle({ start: Date.now() / 1000, baseline: pps })
            burst.mutate(density)
          }}
        >
          <span>
            {burst.isPending
              ? 'Transmitiendo…'
              : 'Transmitir ciclo masivo'}
          </span>
          <span className='transmit-rate-badge'>200 pps UDP</span>
        </button>

        <div className='vertical-feedback' role='status'>
          {error ? (
            <span className='feedback-pill error'>
              <AlertCircle size={13} />
              {apiErrorMessage(error, 'No se pudo transmitir el ciclo')}
            </span>
          ) : status.data?.problem ? (
            <span className='feedback-pill warning'>
              <Activity size={13} />
              {status.data.problem}
            </span>
          ) : burst.data ? (
            <span className='feedback-pill success'>
              <CheckCircle2 size={13} />
              {`${burst.data.received}/${burst.data.virtual_sensors} ACK · Pérdida: ${fmt(burst.data.loss_pct, 1)}%`}
            </span>
          ) : (
            <span className='feedback-pill idle'>
              <span className='pulse-dot miot' />
              {connected ? 'PDU disponible · sin ráfaga medida' : 'Sin enlace verificado'}
            </span>
          )}
        </div>
      </div>

      <p className='px-4 py-2 text-xs text-muted-foreground'>Temperatura, presión y vibración: estímulos simulados. Sensores virtuales sobre un UE; paquetes y ACK provienen de la respuesta de red, pps del UPF.</p>
      <footer className='vertical-card-footer'>
        <div className='footer-chip-group'>
          <span className='footer-chip'>UE …{imsi.slice(-3)}</span>
          <span className='footer-chip mono'>
            {status.data?.session?.address ?? 'Sin IP observada'}
          </span>
        </div>
        <div className='footer-chip-group'>
          <span className='footer-chip badge-slice miot'>corporate</span>
          <span className='footer-chip'>10.46.0.1:8765</span>
        </div>
      </footer>
    </section>
  )
}

export function VerticalsPage() {
  const scenario = useScenarioStore((s) => s.scenario)
  return (
    <main id='content' tabIndex={-1} className='verticals-page'>
      <h1 className='sr-only'>Casos Verticales 5G</h1>
      {scenario !== '5g-sa' ? (
        <div className='verticals-empty'>
          Seleccione 5G Standalone en el menú superior para operar estos casos
          verticales.
        </div>
      ) : (
        <div className='verticals-columns'>
          <TerminalScope kind='vehicle'>{(device, selector) => <div>{selector}<Mobility imsi={device.supi} /></div>}</TerminalScope>
          <TerminalScope kind='sensor'>{(device, selector) => <div>{selector}<MassiveTelemetry imsi={device.supi} /></div>}</TerminalScope>
        </div>
      )}
    </main>
  )
}
