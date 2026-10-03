import { useEffect, useState } from 'react'
import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { api, apiErrorMessage } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { useIdempotentPost } from './requests'
import { BindingForm, PreflightPanel } from './research'

type Assignment = {
  id: string
  mode: 'dry_run' | 'real'
  owner: string
  grantor: string
  testbed: string
  observed_ue: string
  competing_ue: string
  max_runs: number
  max_load_mbps: number
  max_traffic_bytes: number
  max_capture_bytes: number
  max_jobs: number
  used_jobs: number
  enabled: number
  expires_at: number
}
type Execution = {
  id: string
  mode: 'dry_run' | 'real'
  validity_status?: string
  hypothesis_outcome?: string
  recovery_verified?: boolean
  recovery_scope?: string | null
  metrics?:
    | {
        ordinal: number
        treatment: string
        startup_delay_seconds: number
        p1203_mos: number | null
        received_payload_bytes: number
        rebuffer_count: number
      }[]
    | null
  status: string
  cursor: number
  cancel_requested: number
  error_code: string | null
}
type Event = {
  id: number
  event: string
  cursor: number
  created_at: string
  detail: { action?: string; run_ordinal?: number | null; error_code?: string }
}
const statuses: Record<string, string> = {
  queued: 'En cola',
  running: 'Ensayando',
  recovering: 'Recuperando',
  recovery_required: 'Recuperación pendiente · recurso bloqueado',
  completed: 'Ensayo completado',
  cancelled: 'Cancelado',
  failed: 'Fallido',
}
const eventLabels: Record<string, string> = {
  queued: 'Reserva de ensayo creada',
  claimed: 'Worker asignado',
  step_intent: 'Intención',
  step_result: 'Resultado simulado',
  cancel_requested: 'Cancelación solicitada',
  cancelled_before_claim: 'Cancelado antes del inicio',
  assignment_revoked: 'Asignación revocada',
  assignment_expired_or_revoked: 'Asignación vencida o revocada',
  lease_expired: 'Propiedad vencida',
  queue_timeout: 'Cola vencida sin worker',
  recovery_required: 'Recuperación requerida',
  recovery_claimed: 'Recuperación iniciada',
  recovery_started: 'Restauración iniciada',
  recovery_retry_requested: 'Reintento de recuperación solicitado',
  finished: 'Finalizado',
}
const actions: Record<string, string> = {
  preflight: 'Comprobación del sandbox',
  prepare: 'Preparación',
  apply: 'Intervención simulada',
  verify: 'Verificación simulada',
  measure: 'Paso de medición sin datos de red',
  restore: 'Restauración del sandbox',
  verify_restored: 'Verificación de restauración',
}
const terminal = (status?: string) =>
  ['completed', 'cancelled', 'failed'].includes(status ?? '')
const available = (a: Assignment, now: number) =>
  !!a.enabled && a.expires_at * 1000 > now && a.used_jobs < a.max_jobs

function useLabClock() {
  const [now, setNow] = useState(Date.now)
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return now
}

function useLabIdentity() {
  const user = useAuthStore((s) => s.auth.user)
  return { user, identity: `${user?.username}:${user?.testbed}:${user?.role}` }
}
function useAssignments() {
  const { identity } = useLabIdentity()
  return useQuery({
    queryKey: ['laboratory', identity, 'assignments'],
    queryFn: async () =>
      (await api.get<Assignment[]>('/laboratory/assignments')).data,
    refetchInterval: 5000,
  })
}
function ErrorMessage({ error }: { error: unknown }) {
  return error ? (
    <p role='alert' className='text-sm text-destructive'>
      {apiErrorMessage(error, 'No se pudo completar la operación.')}
    </p>
  ) : null
}

export function AssignmentsPanel() {
  const now = useLabClock()
  const { user, identity } = useLabIdentity()
  const list = useAssignments()
  const cache = useQueryClient()
  const post = useIdempotentPost()
  const [form, setForm] = useState({
    mode: 'dry_run' as 'dry_run' | 'real',
    username: '',
    observed_ue: '',
    competing_ue: '',
    validity_hours: 24,
    max_runs: 24,
    max_load_mbps: 100,
    max_traffic_bytes: 1000000000,
    max_capture_bytes: 100000000,
    max_jobs: 3,
  })
  const refresh = () =>
    cache.invalidateQueries({ queryKey: ['laboratory', identity] })
  const create = useMutation({
    mutationFn: () => post('/laboratory/assignments', form),
    onSuccess: refresh,
  })
  const revoke = useMutation({
    mutationFn: (id: string) =>
      api.post(`/laboratory/assignments/${id}/revoke`),
    onSuccess: refresh,
  })
  const teacher = user?.role === 'teacher' || user?.role === 'admin'
  const numbers = [
    ['validity_hours', 'Vigencia (horas)', 1, 168],
    ['max_runs', 'Máximo de ensayos por plan', 2, 720],
    ['max_load_mbps', 'Carga máxima (Mbps)', 0, 1000],
    ['max_traffic_bytes', 'Límite de tráfico por plan (bytes)', 1, 1e12],
    ['max_capture_bytes', 'Límite de captura por plan (bytes)', 1, 1e11],
    ['max_jobs', 'Máximo de ejecuciones', 1, 100],
  ] as const
  return (
    <section
      className='space-y-4 rounded-lg border bg-card p-5'
      aria-label='Asignaciones de práctica'
    >
      <h3 className='font-semibold'>Asignaciones de práctica</h3>
      {teacher && (
        <details>
          <summary className='cursor-pointer py-2 font-medium'>
            Asignar práctica
          </summary>
          <form
            className='space-y-3'
            onSubmit={(e) => {
              e.preventDefault()
              create.mutate()
            }}
          >
            <fieldset
              disabled={create.isPending}
              className='grid gap-3 md:grid-cols-3'
            >
              <div>
                <Label htmlFor='grant-mode'>Modo autorizado</Label>
                <select
                  id='grant-mode'
                  className='block rounded border bg-background p-2'
                  value={form.mode}
                  onChange={(e) =>
                    setForm({
                      ...form,
                      mode: e.target.value as 'dry_run' | 'real',
                    })
                  }
                >
                  <option value='dry_run'>Ensayo dry_run</option>
                  <option value='real'>Core real · piloto QoE 001/004</option>
                </select>
              </div>
              {(
                [
                  ['username', 'Usuario destinatario'],
                  ['observed_ue', 'Alias observado autorizado'],
                  ['competing_ue', 'Alias competidor autorizado'],
                ] as const
              ).map(([field, label]) => (
                <div key={field}>
                  <Label htmlFor={`grant-${field}`}>{label}</Label>
                  <Input
                    id={`grant-${field}`}
                    required
                    maxLength={80}
                    pattern='[a-zA-Z0-9_-]+'
                    value={form[field]}
                    onChange={(e) =>
                      setForm({ ...form, [field]: e.target.value })
                    }
                  />
                </div>
              ))}
              {numbers.map(([field, label, min, max]) => (
                <div key={field}>
                  <Label htmlFor={`grant-${field}`}>{label}</Label>
                  <Input
                    id={`grant-${field}`}
                    type='number'
                    required
                    min={min}
                    max={max}
                    step={field === 'max_load_mbps' ? 'any' : 1}
                    value={form[field]}
                    onChange={(e) =>
                      setForm({ ...form, [field]: Number(e.target.value) })
                    }
                  />
                </div>
              ))}
            </fieldset>
            <Button disabled={create.isPending} type='submit'>
              Guardar asignación
            </Button>
            {create.isSuccess && <p role='status'>Asignación guardada.</p>}
            <ErrorMessage error={create.error} />
          </form>
        </details>
      )}
      {list.isPending && <p>Cargando asignaciones…</p>}
      <ErrorMessage error={list.error || revoke.error} />
      {list.data?.length === 0 && (
        <p className='text-sm'>No hay asignaciones.</p>
      )}
      <ul className='space-y-2'>
        {list.data?.map((a) => (
          <li key={a.id} className='rounded border p-3 text-sm'>
            <p className='font-medium'>
              {a.owner} · {a.testbed} · {a.mode ?? 'dry_run'} · {a.observed_ue}{' '}
              / {a.competing_ue}
            </p>
            <p>
              {!a.enabled
                ? 'Revocada'
                : a.expires_at * 1000 <= now
                  ? 'Vencida'
                  : a.used_jobs >= a.max_jobs
                    ? 'Agotada'
                    : 'Vigente'}{' '}
              · {a.used_jobs}/{a.max_jobs} ejecuciones · hasta{' '}
              {new Date(a.expires_at * 1000).toLocaleString()}
            </p>
            <p>
              Máximo {a.max_runs} ensayos · {a.max_load_mbps} Mbps · tráfico{' '}
              {a.max_traffic_bytes.toLocaleString()} B · captura{' '}
              {a.max_capture_bytes.toLocaleString()} B
            </p>
            {teacher && a.grantor === user?.username && !!a.enabled && (
              <Button
                className='mt-2'
                size='sm'
                variant='outline'
                disabled={revoke.isPending}
                onClick={() => revoke.mutate(a.id)}
              >
                Revocar asignación de {a.owner}
              </Button>
            )}
            {teacher && a.grantor === user?.username && !!a.enabled && (
              <BindingForm assignmentId={a.id} />
            )}
          </li>
        ))}
      </ul>
    </section>
  )
}

export function CampaignExecution({ campaignId }: { campaignId: string }) {
  const now = useLabClock()
  const { user, identity } = useLabIdentity()
  const assignments = useAssignments()
  const [assignmentId, setAssignmentId] = useState('')
  const cache = useQueryClient()
  const post = useIdempotentPost()
  const queryKey = ['laboratory', identity, campaignId, 'executions']
  const executions = useQuery({
    queryKey,
    queryFn: async () =>
      (
        await api.get<Execution[]>(
          `/laboratory/campaigns/${campaignId}/executions`
        )
      ).data,
    refetchInterval: (query) =>
      terminal(query.state.data?.[0]?.status) ? false : 1500,
  })
  const execution = executions.data?.[0]
  const start = useMutation({
    mutationFn: () =>
      post<Execution>(`/laboratory/campaigns/${campaignId}/start`, {
        assignment_id: assignmentId,
        mode:
          assignments.data?.find((a) => a.id === assignmentId)?.mode ??
          'dry_run',
      }),
    onSuccess: async () => {
      await cache.invalidateQueries({ queryKey: ['laboratory', identity] })
    },
  })
  const control = useMutation({
    mutationFn: (action: 'cancel' | 'recover') =>
      api.post(`/laboratory/executions/${execution?.id}/${action}`),
    onSuccess: () => cache.invalidateQueries({ queryKey }),
  })
  const eligible =
    assignments.data?.filter(
      (a) => a.owner === user?.username && available(a, now)
    ) ?? []
  const mode =
    execution?.mode ??
    eligible.find((a) => a.id === assignmentId)?.mode ??
    'dry_run'
  return (
    <div className='space-y-5'>
      <p className='text-xs text-muted-foreground'>
        {mode === 'real'
          ? 'Piloto operativo real'
          : 'Ensayo simulado · Hipótesis: no evaluada.'}
      </p>
      <ErrorMessage
        error={
          executions.error || assignments.error || start.error || control.error
        }
      />
      {executions.isPending && <p>Cargando ejecución…</p>}
      {executions.isSuccess && (
        <details open={!execution} className='rounded-lg border px-4 py-3'>
          <summary className='cursor-pointer font-medium'>
            Control de ejecución
          </summary>
          <PreflightPanel campaignId={campaignId} assignmentId={assignmentId} />
          <div className='flex flex-wrap items-end gap-2'>
            <div className='space-y-1'>
              <Label htmlFor={`assignment-${campaignId}`}>
                Asignación para este plan
              </Label>
              <select
                id={`assignment-${campaignId}`}
                className='block rounded border bg-background p-2 text-sm'
                value={assignmentId}
                disabled={start.isPending}
                onChange={(e) => setAssignmentId(e.target.value)}
              >
                <option value=''>Selecciona una asignación vigente</option>
                {eligible.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.observed_ue} / {a.competing_ue} · {a.mode ?? 'dry_run'} ·{' '}
                    {a.max_jobs - a.used_jobs} disponibles · {a.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </div>
            {!execution && (
              <Button
                disabled={
                  start.isPending ||
                  !eligible.some((a) => a.id === assignmentId)
                }
                onClick={() => start.mutate()}
              >
                {mode === 'real'
                  ? 'Ejecutar piloto real'
                  : 'Iniciar ensayo dry_run'}
              </Button>
            )}
          </div>
        </details>
      )}
      {execution && (
        <>
          <p role='status' className='font-medium'>
            {statuses[execution.status] ?? execution.status} ·{' '}
            {execution.cursor} pasos registrados
          </p>
          <p className='font-mono text-xs break-all text-muted-foreground'>
            Ejecución: {execution.id}
          </p>
          {mode === 'real' && (
            <div
              className='space-y-4 text-sm'
              aria-label='Resultados reales QoE'
            >
              <p>
                Validez: {execution.validity_status ?? 'inconclusive'} ·
                Hipótesis: {execution.hypothesis_outcome ?? 'not_evaluated'}
              </p>
              <p>
                Recuperación:{' '}
                {execution.recovery_verified ? 'Verificada' : 'Pendiente'}
              </p>
              {execution.recovery_scope === 'no_mutation_attempted' && (
                <p>No se llegó a intervenir en el Core.</p>
              )}
              {execution.metrics?.length ? (
                <>
                  <div
                    aria-label='Comparación descriptiva de espera inicial'
                    className='space-y-5 rounded-lg border bg-card p-5'
                  >
                    <p className='font-medium'>
                      Espera inicial observada (segundos)
                    </p>
                    {execution.metrics.map((m) => (
                      <div
                        key={m.ordinal}
                        className='grid grid-cols-[5.5rem_minmax(0,1fr)_5.5rem] items-center gap-3 text-xs'
                      >
                        <span>
                          {m.treatment.includes('disabled')
                            ? 'NWDAF OFF'
                            : 'NWDAF ON'}
                        </span>
                        <div className='rounded-sm bg-muted/50'>
                          <div
                            role='img'
                            aria-label={`Ensayo ${m.ordinal}: ${m.startup_delay_seconds.toFixed(3)} segundos`}
                            className={`h-7 rounded-sm ${m.treatment.includes('disabled') ? 'bg-zinc-600' : 'bg-zinc-200'}`}
                            style={{
                              width: `${Math.max(1, (100 * m.startup_delay_seconds) / Math.max(...execution.metrics!.map((x) => x.startup_delay_seconds), 0.001))}%`,
                            }}
                          />
                        </div>
                        <span className='text-right font-mono tabular-nums'>
                          {m.startup_delay_seconds.toFixed(3)} s
                        </span>
                      </div>
                    ))}
                  </div>
                  <div className='overflow-x-auto rounded-lg border bg-card'>
                    <table className='w-full text-left'>
                      <caption className='p-4 text-left text-sm font-medium text-foreground'>
                        Resumen QoE
                      </caption>
                      <thead>
                        <tr>
                          <th>Ensayo</th>
                          <th>Tratamiento</th>
                          <th>
                            <span aria-hidden='true'>Startup delay (s)</span>
                            <span className='sr-only'>Startup (s)</span>
                          </th>
                          <th>MOS estimado P.1203</th>
                          <th>Bytes recibidos</th>
                          <th>
                            <span aria-hidden='true'>Rebuffering</span>
                            <span className='sr-only'>Pausas</span>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {execution.metrics.map((m) => (
                          <tr key={m.ordinal}>
                            <td>{m.ordinal}</td>
                            <td>
                              <span title={m.treatment}>
                                {m.treatment.includes('disabled')
                                  ? 'NWDAF OFF'
                                  : 'NWDAF ON'}
                              </span>
                            </td>
                            <td>{m.startup_delay_seconds.toFixed(3)}</td>
                            <td>
                              {m.p1203_mos?.toFixed(4) ?? 'Sin estimación'}
                            </td>
                            <td>{m.received_payload_bytes.toLocaleString()}</td>
                            <td>{m.rebuffer_count}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              ) : (
                <p>Sin métricas de reproducción medidas.</p>
              )}
            </div>
          )}
          {execution.cancel_requested === 1 && !terminal(execution.status) && (
            <p>Cancelación solicitada; esperando recuperación verificada.</p>
          )}
          {execution.error_code && (
            <p className='text-sm'>Motivo: {execution.error_code}</p>
          )}
          {!terminal(execution.status) && (
            <Button
              variant='outline'
              disabled={control.isPending || !!execution.cancel_requested}
              onClick={() => control.mutate('cancel')}
            >
              Cancelar ensayo
            </Button>
          )}
          {execution.status === 'recovery_required' && (
            <Button
              className='ml-2'
              variant='outline'
              disabled={control.isPending}
              onClick={() => control.mutate('recover')}
            >
              Reintentar recuperación
            </Button>
          )}
          <ExecutionEvents
            key={execution.id}
            execution={execution}
            identity={identity}
          />
        </>
      )}
    </div>
  )
}

function ExecutionEvents({
  execution,
  identity,
}: {
  execution: Execution
  identity: string
}) {
  const events = useInfiniteQuery({
    queryKey: [
      'laboratory',
      identity,
      execution.id,
      'events',
      execution.status,
    ],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) =>
      (
        await api.get<Event[]>(
          `/laboratory/executions/${execution.id}/events?after=${pageParam}`
        )
      ).data,
    getNextPageParam: (page) =>
      page.length === 200 ? page[page.length - 1].id : undefined,
    refetchInterval: terminal(execution.status) ? false : 1500,
  })
  return (
    <details className='rounded-lg border px-4 py-3'>
      <summary className='cursor-pointer font-medium'>
        Diario del ensayo
      </summary>
      <ErrorMessage error={events.error} />
      <ol className='mt-4 max-h-64 space-y-2 overflow-auto font-mono text-xs text-muted-foreground'>
        {events.data?.pages.flat().map((event) => (
          <li key={event.id}>
            <time>{new Date(event.created_at).toLocaleTimeString()}</time> ·{' '}
            {execution.mode === 'real' && event.event === 'step_result'
              ? 'Resultado real'
              : (eventLabels[event.event] ?? event.event)}
            {event.detail.action &&
              ` · ${execution.mode === 'real' ? event.detail.action : (actions[event.detail.action] ?? event.detail.action)}`}
            {event.detail.run_ordinal != null &&
              ` · ensayo ${event.detail.run_ordinal}`}
            {event.detail.error_code && ` · ${event.detail.error_code}`}
          </li>
        ))}
      </ol>
      {events.hasNextPage && (
        <Button
          variant='outline'
          size='sm'
          disabled={events.isFetchingNextPage}
          onClick={() => void events.fetchNextPage()}
        >
          Cargar más eventos
        </Button>
      )}
    </details>
  )
}
