import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { api, canOperate } from '@/lib/api'
import type { OperationResult } from '@/features/commands/types'
import { windowQuality, type CorrelatedSample } from './ab-observations'
import { readUrllcStatus } from './use-urllc-xdp'

type Authority = { version: number; phase: string }
type Receipt = {
  action_id?: string
  version?: number
  effective_policy_verified?: boolean
}
type Target = {
  service: string
  status: string
  source_timestamp?: number
  traffic_complete: boolean
  sample_age_seconds: number | null
  metrics: { ul_pps?: number; dl_pps?: number }
}
type Row = CorrelatedSample & { quality: string }

export function AbObservationsPanel() {
  const user = useAuthStore((s) => s.auth.user)
  const allowed = canOperate(user?.role)
  const queryClient = useQueryClient()
  const queryKey = ['c7-ab-observations', user?.username]
  // QueryClient is cache infrastructure; the user-scoped key identifies the data.
  // eslint-disable-next-line @tanstack/query/exhaustive-deps
  const query = useQuery({
    queryKey,
    enabled: allowed,
    retry: false,
    refetchInterval: 5000,
    queryFn: async ({ queryKey }) => {
      const [snapshot, authority, history, xdp] = await Promise.all([
        api.get<{ targets: Target[] }>('/upf-telemetry/snapshot'),
        api.get<Authority>('/operations/policy-authority').catch(() => null),
        api
          .get<
            OperationResult[]
          >('/operations/history', { params: { limit: 200 } })
          .catch(() => null),
        readUrllcStatus().catch(() => null),
      ])
      const runs = (history?.data ?? []).filter(
        (run) =>
          run.status === 'success' &&
          (run.data as Receipt | null)?.effective_policy_verified === true
      )
      const run = authority
        ? runs.find(
            (r) => (r.data as Receipt).version === authority.data.version
          )
        : undefined
      const receipt = run?.data as Receipt | undefined
      const completed = run ? Date.parse(run.completed_at) : NaN
      // A mode command is evidence for its own version only. A later unobserved
      // native action must not inherit a guessed authority mode.
      const authorityMode =
        run?.operation_id === 'nwdaf.mode'
          ? String(run.parameters.mode)
          : undefined
      const after = await api
        .get<Authority>('/operations/policy-authority')
        .catch(() => null)
      const consistent =
        !!authority &&
        !!after &&
        authority.data.version === after.data.version &&
        authority.data.phase === after.data.phase
      const samples = snapshot.data.targets.map(
        (t): CorrelatedSample => ({
          timestamp: t.source_timestamp
            ? t.source_timestamp * 1000
            : Date.now(),
          service: t.service,
          actionId: receipt?.action_id,
          version: authority?.data.version,
          authorityMode,
          phase: authority?.data.phase,
          generation:
            t.service === 'urllc' ? xdp?.session_generation : undefined,
          networkMode:
            t.service === 'urllc'
              ? xdp?.effective_mode === 'unknown'
                ? undefined
                : xdp?.effective_mode
              : t.traffic_complete
                ? 'kernel'
                : undefined,
          confirmed:
            t.service !== 'urllc' ||
            xdp?.effective_mode === 'kernel' ||
            xdp?.confirmed === true,
          fresh:
            consistent &&
            Number.isFinite(completed) &&
            (t.source_timestamp ?? 0) * 1000 >= completed &&
            t.status === 'measured' &&
            t.sample_age_seconds !== null &&
            t.sample_age_seconds >= 0 &&
            t.sample_age_seconds <= 20,
          ulPps: t.metrics.ul_pps,
          dlPps: t.metrics.dl_pps,
        })
      )
      const previous =
        queryClient.getQueryData<{ rows: Row[] }>(queryKey)?.rows ?? []
      const rows = [
        ...previous,
        ...samples
          .filter(
            (sample) =>
              !previous.some(
                (r) =>
                  r.timestamp === sample.timestamp &&
                  r.service === sample.service
              )
          )
          .map((sample) => ({
            ...sample,
            quality: windowQuality(
              [...previous].reverse().find((r) => r.service === sample.service),
              sample
            ),
          })),
      ].slice(-90)
      return { xdp, rows }
    },
  })
  const rows = query.data?.rows ?? []
  if (!allowed) return null
  return (
    <section
      aria-label='Ventanas A/B correlacionadas'
      className='mb-4 rounded-lg border p-3 text-xs'
    >
      <h2 className='text-base font-semibold'>
        Ventanas A/B · Correlación operativa
      </h2>
      <p className='my-2 text-muted-foreground'>
        Observaciones de esta sesión de interfaz. A = kernel; B = XDP. Estable
        exige extremos consecutivos con la misma identidad; los huecos y
        transiciones se excluyen. Un Action ID ausente o una versión sin recibo
        MML se muestra sin correlación. Las tasas son del UPF TUN y pueden ser
        parciales durante XDP.
      </p>
      {query.isPending && <p>Esperando observaciones…</p>}
      {query.isError && (
        <p role='alert'>
          Observación no disponible; las filas conservadas son históricas.
        </p>
      )}
      <div className='max-h-56 overflow-auto'>
        <table className='w-full text-left'>
          <thead>
            <tr>
              {[
                'Hora',
                'Canal',
                'Ventana',
                'Action ID',
                'Autoridad / versión',
                'Generación',
                'A/B',
                'UL / DL pps',
              ].map((h) => (
                <th className='p-2' key={h}>
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr
                key={`${r.timestamp}-${r.service}`}
                className={
                  r.quality === 'Estable'
                    ? 'text-emerald-600'
                    : 'text-amber-600'
                }
              >
                <td className='p-2 whitespace-nowrap'>
                  {new Date(r.timestamp).toLocaleTimeString('es-PE')}
                </td>
                <td>{r.service}</td>
                <td>{r.quality}</td>
                <td>{r.actionId ?? 'No observado'}</td>
                <td>
                  {r.authorityMode ?? 'No verificada'} / {r.version ?? '—'}
                </td>
                <td>{r.generation ?? '—'}</td>
                <td>
                  {r.networkMode === 'kernel'
                    ? 'A · kernel'
                    : r.networkMode === 'xdp'
                      ? 'B · XDP'
                      : '—'}
                </td>
                <td>
                  {r.ulPps?.toFixed(2) ?? '—'} / {r.dlPps?.toFixed(2) ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className='mt-2'>
        Contadores XDP observados (paquetes / bytes):{' '}
        {query.isError
          ? 'No disponibles'
          : Object.entries(query.data?.xdp?.counters ?? {})
              .map(
                ([name, value]) => `${name}: ${value.packets} / ${value.bytes}`
              )
              .join(' · ') || 'No disponibles'}
      </p>
    </section>
  )
}
