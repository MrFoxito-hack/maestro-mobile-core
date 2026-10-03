import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import { api } from '@/lib/api'
import { LaboratoryPage } from './index'

const experiment = {
  id: 'exp-a',
  title: 'Comparación de vídeo',
  question: '¿Mejora la espera inicial?',
  hypothesis: 'El control reduce la congestión.',
  created_at: '2026-09-29',
}
const descriptor = {
  schema_version: 1,
  template: 'qoe_closed_loop',
  template_version: 1,
  scenario: '5g-sa',
  observed_ue: 'video',
  competing_ue: 'load',
  load_mbps: [5],
  repetitions: 2,
  measurement_seconds: 30,
  campaign_budget_bytes: 1000000000,
  capture_budget_bytes: 10000000,
  seed: 42017,
  primary_metric: 'player_startup_delay_seconds',
  design: 'paired_randomized_blocks',
}
const revision = { id: 'rev-a', number: 1, sha256: 'abc123', descriptor }

beforeEach(() => {
  useAuthStore
    .getState()
    .auth.setUser({ username: 'student', role: 'student', testbed: 'lab' })
})
afterEach(() => {
  vi.restoreAllMocks()
  useAuthStore.getState().auth.reset()
})

async function mount() {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <LaboratoryPage />
    </QueryClientProvider>
  )
}

test('edits invalidate saved validation and identify dry-run scope', async () => {
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data:
      path === '/laboratory/assignments'
        ? []
        : path === '/laboratory/experiments'
          ? [experiment]
          : { ...experiment, revisions: [revision], campaigns: [] },
  }))
  const post = vi.spyOn(api, 'post').mockResolvedValue({
    data: {
      revision_id: 'rev-a',
      design_valid: true,
      issues: [],
      estimates: {
        runs: 4,
        measurement_seconds: 120,
        competing_payload_bytes: 75000000,
      },
      warnings: ['Falta preflight vivo.'],
    },
  })
  const screen = await mount()
  await screen.getByRole('button', { name: 'Comparación de vídeo' }).click()
  await screen.getByRole('tab', { name: 'Configuración' }).click()
  const plan = screen.getByRole('button', { name: 'Crear plan de ensayos' })
  await expect.element(plan).toBeDisabled()
  await screen.getByRole('button', { name: 'Validar diseño guardado' }).click()
  await expect.element(plan).toBeEnabled()
  await expect
    .element(screen.getByText('Diseño completo para planificación'))
    .toBeVisible()
  await screen.getByLabelText('Niveles de carga competidora (Mbps)').fill('10')
  await expect.element(plan).toBeDisabled()
  await expect
    .element(screen.getByRole('button', { name: 'Validar diseño guardado' }))
    .toBeDisabled()
  expect(post).toHaveBeenCalledTimes(1)
  expect(post.mock.calls[0][0]).toBe('/laboratory/revisions/rev-a/validate')
  await expect
    .element(screen.getByText('Diseño y ensayo dry_run.', { exact: false }))
    .toBeVisible()
})

test('a timed-out create retries the same idempotency key', async () => {
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data:
      path === '/laboratory/assignments'
        ? []
        : path === '/laboratory/experiments'
          ? []
          : { ...experiment, revisions: [], campaigns: [] },
  }))
  const post = vi
    .spyOn(api, 'post')
    .mockRejectedValueOnce(new Error('Tiempo agotado'))
    .mockResolvedValueOnce({ data: experiment })
  const screen = await mount()
  await screen.getByRole('tab', { name: 'Configuración' }).click()
  await screen.getByLabelText('Título', { exact: true }).fill(experiment.title)
  await screen
    .getByLabelText('¿Qué quieres estudiar?')
    .fill(experiment.question)
  await screen
    .getByLabelText('¿Qué esperas que ocurra y por qué?')
    .fill(experiment.hypothesis)
  await screen.getByRole('button', { name: 'Guardar experimento' }).click()
  await expect
    .element(screen.getByRole('alert'))
    .toHaveTextContent('Tiempo agotado')
  await screen.getByRole('button', { name: 'Guardar experimento' }).click()
  await expect
    .element(screen.getByText('Diseño · QoE con y sin closed-loop'))
    .toBeVisible()
  expect(post).toHaveBeenCalledTimes(2)
  expect(post.mock.calls[0][2]?.headers?.['Idempotency-Key']).toBe(
    post.mock.calls[1][2]?.headers?.['Idempotency-Key']
  )
})

test('tabs retain design and notebook drafts without performing mutations', async () => {
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data:
      path === '/laboratory/experiments'
        ? [experiment]
        : path === '/laboratory/experiments/exp-a'
          ? { ...experiment, revisions: [revision], campaigns: [] }
          : [],
  }))
  const post = vi.spyOn(api, 'post')
  const screen = await mount()
  await screen.getByRole('button', { name: experiment.title }).click()
  await expect
    .element(screen.getByRole('tab', { name: 'Resultados', exact: true }))
    .toHaveAttribute('aria-selected', 'true')
  await screen.getByRole('tab', { name: 'Configuración' }).click()
  await screen
    .getByLabelText('Niveles de carga competidora (Mbps)')
    .fill('5, 15')
  await screen
    .getByRole('tab', { name: 'Asistente local', exact: true })
    .click()
  await expect
    .element(screen.getByLabelText('Niveles de carga competidora (Mbps)'))
    .not.toBeVisible()
  await expect.element(screen.getByText('GPU local · RTX 5070')).toBeVisible()
  await screen
    .getByText('Cuaderno del alumno · entradas conservadas', { exact: true })
    .click()
  await screen
    .getByLabelText('Predicción y fundamento')
    .fill('Borrador de investigación conservado.')
  await screen.getByRole('tab', { name: 'Configuración' }).click()
  await expect
    .element(screen.getByLabelText('Niveles de carga competidora (Mbps)'))
    .toHaveValue('5, 15')
  await screen
    .getByRole('tab', { name: 'Asistente local', exact: true })
    .click()
  await expect
    .element(screen.getByLabelText('Predicción y fundamento'))
    .toHaveValue('Borrador de investigación conservado.')
  expect(post).not.toHaveBeenCalled()
})

const grant = {
  id: 'grant-a',
  owner: 'student',
  grantor: 'teacher',
  testbed: 'lab',
  observed_ue: 'video',
  competing_ue: 'load',
  max_runs: 24,
  max_load_mbps: 100,
  max_traffic_bytes: 1000000000,
  max_capture_bytes: 100000000,
  max_jobs: 3,
  used_jobs: 0,
  enabled: 1,
  expires_at: Date.now() / 1000 + 86400,
}
const savedPlan = { id: 'plan-a', revision_id: 'rev-a', plan: { runs: [] } }

test('start retry keeps its key; cancellation and recovery remain visible after reopening', async () => {
  let execution:
    | {
        id: string
        status: string
        cursor: number
        cancel_requested: number
        error_code: string | null
      }
    | undefined
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data:
      path === '/laboratory/assignments'
        ? [grant]
        : path === '/laboratory/experiments'
          ? [experiment]
          : path.endsWith('/executions')
            ? execution
              ? [execution]
              : []
            : path.includes('/events?')
              ? [
                  {
                    id: 1,
                    event: 'step_result',
                    cursor: 0,
                    created_at: '2026-09-29T10:00:00Z',
                    detail: { action: 'measure', run_ordinal: 1 },
                  },
                ]
              : {
                  ...experiment,
                  revisions: [revision],
                  campaigns: [savedPlan],
                },
  }))
  let attempts = 0
  const post = vi.spyOn(api, 'post').mockImplementation(async (path) => {
    if (path.endsWith('/start')) {
      if (++attempts === 1) throw new Error('Respuesta perdida')
      execution = {
        id: 'job-a',
        status: 'running',
        cursor: 2,
        cancel_requested: 0,
        error_code: null,
      }
    } else if (path.endsWith('/cancel')) {
      execution = {
        ...execution!,
        status: 'recovery_required',
        cancel_requested: 1,
        error_code: 'recovery_failed',
      }
    } else if (path.endsWith('/recover')) {
      execution = { ...execution!, status: 'cancelled', error_code: null }
    }
    return { data: execution }
  })
  let screen = await mount()
  await screen.getByRole('button', { name: experiment.title }).click()
  const start = screen.getByRole('button', { name: 'Iniciar ensayo dry_run' })
  await expect.element(start).toBeDisabled()
  await screen
    .getByLabelText('Asignación para este plan')
    .selectOptions('grant-a')
  await start.click()
  await expect
    .element(screen.getByRole('alert'))
    .toHaveTextContent('Respuesta perdida')
  await start.click()
  await expect
    .element(screen.getByText('Ensayando · 2 pasos registrados'))
    .toBeVisible()
  expect(post.mock.calls[0][2]?.headers?.['Idempotency-Key']).toBe(
    post.mock.calls[1][2]?.headers?.['Idempotency-Key']
  )
  expect(post.mock.calls[1][1]).toEqual({
    assignment_id: 'grant-a',
    mode: 'dry_run',
  })
  await screen.getByText('Diario del ensayo', { exact: true }).click()
  await expect
    .element(
      screen.getByText('Paso de medición sin datos de red', { exact: false })
    )
    .toBeVisible()
  await screen.getByRole('button', { name: 'Cancelar ensayo' }).click()
  await expect
    .element(
      screen.getByText('Recuperación pendiente · recurso bloqueado', {
        exact: false,
      })
    )
    .toBeVisible()
  await screen.unmount()
  screen = await mount()
  await screen.getByRole('button', { name: experiment.title }).click()
  await screen.getByRole('button', { name: 'Reintentar recuperación' }).click()
  await expect
    .element(screen.getByText('Cancelado · 2 pasos registrados'))
    .toBeVisible()
  await expect
    .element(screen.getByText('Hipótesis: no evaluada.', { exact: false }))
    .toBeVisible()
})

test('teacher assigns bounded practice and revokes it', async () => {
  useAuthStore
    .getState()
    .auth.setUser({ username: 'teacher', role: 'teacher', testbed: null })
  let assigned = false
  let enabled = 1
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data:
      path === '/laboratory/assignments' && assigned
        ? [{ ...grant, enabled }]
        : [],
  }))
  const post = vi.spyOn(api, 'post').mockImplementation(async (path) => {
    if (path.endsWith('/revoke')) enabled = 0
    else assigned = true
    return { data: grant }
  })
  const screen = await mount()
  await screen.getByRole('tab', { name: 'Configuración' }).click()
  await screen.getByText('Asignar práctica', { exact: true }).click()
  await screen.getByLabelText('Usuario destinatario').fill('student')
  await screen.getByLabelText('Alias observado autorizado').fill('video')
  await screen.getByLabelText('Alias competidor autorizado').fill('load')
  await screen.getByLabelText('Máximo de ejecuciones').fill('2')
  await screen.getByRole('button', { name: 'Guardar asignación' }).click()
  await expect.element(screen.getByText('Asignación guardada.')).toBeVisible()
  expect(post.mock.calls[0][1]).toMatchObject({
    username: 'student',
    mode: 'dry_run',
    max_jobs: 2,
  })
  await screen
    .getByRole('button', { name: 'Revocar asignación de student' })
    .click()
  await expect
    .element(screen.getByText('Revocada', { exact: false }))
    .toBeVisible()
})

test('student cannot start with expired, exhausted or another users grant', async () => {
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data:
      path === '/laboratory/assignments'
        ? [
            { ...grant, expires_at: 1 },
            { ...grant, id: 'exhausted', used_jobs: 3 },
            { ...grant, id: 'foreign', owner: 'another' },
          ]
        : path === '/laboratory/experiments'
          ? [experiment]
          : path.endsWith('/executions')
            ? []
            : { ...experiment, revisions: [revision], campaigns: [savedPlan] },
  }))
  const screen = await mount()
  await screen.getByRole('button', { name: experiment.title }).click()
  await expect
    .element(screen.getByRole('button', { name: 'Iniciar ensayo dry_run' }))
    .toBeDisabled()
  await expect
    .element(
      screen.getByRole('option', { name: 'Selecciona una asignación vigente' })
    )
    .toHaveTextContent('Selecciona una asignación vigente')
  await expect
    .element(screen.getByText('Asignar práctica', { exact: true }))
    .not.toBeInTheDocument()
})

test('journal loads events beyond the first page for a completed execution', async () => {
  const firstPage = Array.from({ length: 200 }, (_, i) => ({
    id: i + 1,
    event: 'step_result',
    cursor: i,
    created_at: '2026-09-29T10:00:00Z',
    detail: { action: 'verify' },
  }))
  const get = vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data:
      path === '/laboratory/assignments'
        ? [grant]
        : path === '/laboratory/experiments'
          ? [experiment]
          : path.endsWith('/executions')
            ? [
                {
                  id: 'job-a',
                  status: 'completed',
                  cursor: 145,
                  cancel_requested: 0,
                  error_code: null,
                },
              ]
            : path.endsWith('/events?after=0')
              ? firstPage
              : path.endsWith('/events?after=200')
                ? [
                    {
                      id: 201,
                      event: 'finished',
                      cursor: 145,
                      created_at: '2026-09-29T10:01:00Z',
                      detail: {},
                    },
                  ]
                : {
                    ...experiment,
                    revisions: [revision],
                    campaigns: [savedPlan],
                  },
  }))
  const screen = await mount()
  await screen.getByRole('button', { name: experiment.title }).click()
  await screen.getByText('Diario del ensayo', { exact: true }).click()
  await screen.getByRole('button', { name: 'Cargar más eventos' }).click()
  await expect
    .element(screen.getByText('Finalizado', { exact: false }))
    .toBeVisible()
  expect(get).toHaveBeenCalledWith(
    '/laboratory/executions/job-a/events?after=200'
  )
  await expect
    .element(screen.getByRole('button', { name: 'Cancelar ensayo' }))
    .not.toBeInTheDocument()
})

test.each(['completed', 'failed'])(
  'real execution %s displays measured values or their absence distinctly',
  async (status) => {
    vi.spyOn(api, 'get').mockImplementation(async (path) => ({
      data:
        path === '/laboratory/assignments'
          ? [{ ...grant, mode: 'real' }]
          : path === '/laboratory/experiments'
            ? [experiment]
            : path.endsWith('/executions')
              ? [
                  {
                    id: 'real-test-job',
                    mode: 'real',
                    status,
                    cursor: 13,
                    cancel_requested: 0,
                    error_code:
                      status === 'failed' ? 'insufficient_chf_quota' : null,
                    validity_status:
                      status === 'completed' ? 'valid' : 'inconclusive',
                    hypothesis_outcome: 'inconclusive',
                    recovery_verified: true,
                    metrics:
                      status === 'completed'
                        ? [
                            {
                              ordinal: 1,
                              treatment: 'controller_enabled_verified',
                              startup_delay_seconds: 0.125,
                              p1203_mos: 4.1234,
                              received_payload_bytes: 2000,
                              rebuffer_count: 0,
                            },
                          ]
                        : null,
                  },
                ]
              : path.includes('/events?')
                ? [
                    {
                      id: 1,
                      event: 'step_result',
                      cursor: 1,
                      created_at: '2026-09-30T01:00:00Z',
                      detail: { action: 'preflight' },
                    },
                  ]
                : {
                    ...experiment,
                    revisions: [revision],
                    campaigns: [savedPlan],
                  },
    }))
    const screen = await mount()
    await screen.getByRole('button', { name: experiment.title }).click()
    await screen.getByText('Diario del ensayo', { exact: true }).click()
    await expect
      .element(screen.getByText('Resultado real', { exact: false }))
      .toBeVisible()
    await expect
      .element(screen.getByText('Recuperación: Verificada'))
      .toBeVisible()
    if (status === 'completed') {
      await expect
        .element(screen.getByText('4.1234', { exact: true }))
        .toBeVisible()
      await expect
        .element(screen.getByText('0.125', { exact: true }))
        .toBeVisible()
    } else {
      await expect
        .element(screen.getByText('Sin métricas de reproducción medidas.'))
        .toBeVisible()
      await expect
        .element(screen.getByText('Motivo: insufficient_chf_quota'))
        .toBeVisible()
    }
  }
)
