import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, expect, test, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { api } from '@/lib/api'
import { InvestigationAssistant } from './investigation'

afterEach(() => vi.restoreAllMocks())
const evidence = [
  { id: 'dataset', kind: 'dataset', document: { source: 'live_campaign' } },
]
const context = {
  context_sha256: 'hash',
  references: { ev_01: { evidence_id: 'measured', sha256: 'sha' } },
  model_context: {
    observations: [{ id: 'ev_01', claim: 'Observación conservada.' }],
  },
}
async function mount(onChoose = vi.fn()) {
  return render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { mutations: { retry: false } } })
      }
    >
      <InvestigationAssistant
        experimentId='experiment'
        evidence={evidence}
        onChoose={onChoose}
      />
    </QueryClientProvider>
  )
}

test('offline assistant remains optional and allows another request', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({ data: { context } })
  const post = vi
    .spyOn(api, 'post')
    .mockImplementation(async (path) => ({
      data: path.endsWith('/suggest')
        ? { id: 'request', status: 'unavailable', reason: 'offline' }
        : { id: 'investigation' },
    }))
  const screen = await mount()
  expect(post).not.toHaveBeenCalled()
  await screen
    .getByRole('button', { name: 'Asistente local de investigación' })
    .click()
  await screen
    .getByRole('button', { name: 'Consultar asistente local', exact: true })
    .click()
  await expect
    .element(
      screen.getByText('Asistente de IA fuera de línea.', { exact: false })
    )
    .toBeVisible()
  await expect
    .element(
      screen.getByRole('button', {
        name: 'Consultar asistente local',
        exact: true,
      })
    )
    .toBeEnabled()
  expect(post.mock.calls.some(([path]) => path.includes('/start'))).toBe(false)
})

test('validated suggestion links evidence and only copies a notebook draft', async () => {
  vi.spyOn(api, 'get').mockResolvedValue({ data: { context } })
  const post = vi.spyOn(api, 'post').mockImplementation(async (path) => ({
    data: path.endsWith('/suggest')
      ? {
          id: 'request',
          status: 'completed',
          proposal: {
            observations: [
              { claim: 'Observación conservada.', evidence_ids: ['ev_01'] },
            ],
            hypotheses: [
              {
                id: 'h1',
                description: 'El relay podría contribuir a la espera.',
                supporting_evidence: ['ev_01'],
                contradicting_evidence: [],
                missing_information: ['Medición independiente del relay.'],
              },
            ],
            proposed_test: {
              catalog_action: 'compare_player_observations',
              parameters: {},
              expected_observations_by_hypothesis: {
                h1: 'La espera cambiaría con el reproductor.',
              },
              limitations: ['No demuestra causalidad.'],
            },
            conclusion_status: 'inconclusive',
          },
        }
      : { id: 'investigation' },
  }))
  const choose = vi.fn()
  const screen = await mount(choose)
  await screen
    .getByRole('button', { name: 'Asistente local de investigación' })
    .click()
  await screen
    .getByRole('button', { name: 'Consultar asistente local', exact: true })
    .click()
  await expect
    .element(
      screen.getByText('Propuesta orientativa · conclusión: inconclusive')
    )
    .toBeVisible()
  await expect
    .element(screen.getByRole('link', { name: 'ev_01' }).first())
    .toHaveAttribute('href', '#evidence-measured')
  await screen
    .getByRole('button', { name: 'Usar revisión en mi cuaderno' })
    .click()
  expect(choose).toHaveBeenCalledWith(
    expect.stringContaining('Comparar observaciones'),
    ['measured']
  )
  expect(post.mock.calls).toHaveLength(2)
  expect(post.mock.calls.some(([path]) => path.includes('/start'))).toBe(false)
})
