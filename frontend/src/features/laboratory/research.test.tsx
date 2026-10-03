import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { useAuthStore } from '@/stores/auth-store'
import { api } from '@/lib/api'
import { BindingForm, PreflightPanel, ResearchPanel } from './research'

beforeEach(() =>
  useAuthStore
    .getState()
    .auth.setUser({ username: 'student', role: 'student', testbed: 'lab' })
)
afterEach(() => {
  vi.restoreAllMocks()
  useAuthStore.getState().auth.reset()
})
function wrapper(node: React.ReactNode) {
  return (
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
      {node}
    </QueryClientProvider>
  )
}

test('preflight renders blocked checks without enabling live campaigns', async () => {
  let collected = false
  vi.spyOn(api, 'get').mockImplementation(async () => ({
    data: collected
      ? [
          {
            id: 'p1',
            created_at: '2026-09-29',
            status: 'completed',
            stale: false,
            document: {
              checks: [{ id: 'competing_session', status: 'blocked' }],
              pending_gates: ['remote_fencing'],
              errors: {},
            },
          },
        ]
      : [],
  }))
  const post = vi.spyOn(api, 'post').mockImplementation(async () => {
    collected = true
    return { data: {} }
  })
  const screen = await render(
    wrapper(<PreflightPanel campaignId='campaign' assignmentId='grant' />)
  )
  await screen.getByText('Preflight vivo · solo lectura').click()
  await screen
    .getByRole('button', { name: 'Comprobar condiciones vivas' })
    .click()
  await expect
    .element(screen.getByText('competing_session: Bloqueado'))
    .toBeVisible()
  await expect
    .element(
      screen.getByText('La ejecución real permanece bloqueada.', {
        exact: false,
      })
    )
    .toBeVisible()
  expect(post.mock.calls[0][1]).toEqual({ assignment_id: 'grant' })
})

test('notebook references evidence and preserves student conclusion separately', async () => {
  const evidence = [
    {
      id: 'ev1',
      kind: 'dataset',
      sha256: 'abc',
      document: {
        source: 'historical_import',
        trials: [
          {
            ordinal: 1,
            treatment: 'disabled',
            startup_seconds: 8.2,
            validity_status: 'inconclusive',
          },
        ],
      },
    },
  ]
  let notes: unknown[] = []
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data: path.endsWith('/evidence') ? evidence : notes,
  }))
  const post = vi.spyOn(api, 'post').mockImplementation(async (_path, body) => {
    notes = [{ id: 'n1', created_at: '2026-09-29', document: body }]
    return { data: notes[0] }
  })
  const screen = await render(wrapper(<ResearchPanel experimentId='exp' />))
  await screen.getByText('Evidencias, comparación y cuaderno').click()
  await expect
    .element(screen.getByText('dataset · historical_import', { exact: true }))
    .toBeVisible()
  await screen
    .getByLabelText('Predicción y fundamento')
    .fill('Creo que la espera puede disminuir.')
  await screen
    .getByLabelText('Conclusión y limitaciones')
    .fill('Falta verificar la política inicial.')
  await screen
    .getByLabelText('Interpretación del alumno')
    .selectOptions('inconclusive')
  await screen.getByRole('checkbox').click()
  await screen
    .getByRole('button', { name: 'Guardar entrada del cuaderno' })
    .click()
  await expect.element(screen.getByText('Entrada conservada.')).toBeVisible()
  expect(post.mock.calls[0][1]).toMatchObject({
    outcome: 'inconclusive',
    evidence_ids: ['ev1'],
  })
  expect(evidence[0].document.trials[0].startup_seconds).toBe(8.2)
})

test('teacher binding submits typed subjects and only authorizes reading', async () => {
  const post = vi.spyOn(api, 'post').mockResolvedValue({ data: {} })
  const screen = await render(wrapper(<BindingForm assignmentId='grant' />))
  await screen.getByText('Vincular sujetos reales para lectura').click()
  await screen.getByLabelText('SUPI observado').fill('imsi-999700000000001')
  await screen.getByLabelText('SUPI competidor').fill('imsi-999700000000004')
  await screen
    .getByRole('button', { name: 'Guardar vínculo de lectura' })
    .click()
  await expect.element(screen.getByText('Vínculo guardado.')).toBeVisible()
  expect(post.mock.calls[0][1]).toEqual({
    observed_supi: 'imsi-999700000000001',
    competing_supi: 'imsi-999700000000004',
    dnn: 'internet',
  })
})

test('ACK without reception shows inconclusive result and opens its evidence', async () => {
  vi.spyOn(api, 'get').mockImplementation(async (path) => ({
    data: path.endsWith('/evidence')
      ? [
          {
            id: 'ack',
            kind: 'n7_observation',
            sha256: 'abc',
            document: { source: 'synthetic_test', status: 204 },
          },
          {
            id: 'assessment',
            kind: 'effect_analysis',
            sha256: 'def',
            document: {
              source: 'synthetic_test',
              control_ack_observed: true,
              effect_finding: 'inconclusive',
              validity_status: 'inconclusive',
              measurement: null,
              reasons: ['receiver_measurement_missing'],
              evidence_ids: ['ack'],
            },
          },
        ]
      : [],
  }))
  const screen = await render(wrapper(<ResearchPanel experimentId='exp' />))
  await screen.getByText('Evidencias, comparación y cuaderno').click()
  await expect
    .element(
      screen.getByText('Dictamen de efecto: inconclusive', { exact: true })
    )
    .toBeVisible()
  await expect
    .element(screen.getByText('Tasa recibida: Sin dato bps', { exact: true }))
    .toBeVisible()
  await expect
    .element(screen.getByRole('link', { name: 'Abrir evidencia ack' }))
    .toHaveAttribute('href', '#evidence-ack')
  await expect
    .element(
      screen.getByText('ACK observado: Sí · no demuestra enforcement.', {
        exact: true,
      })
    )
    .toBeVisible()
})
