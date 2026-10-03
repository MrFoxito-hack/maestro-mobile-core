import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { api, apiErrorMessage } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { useIdempotentPost } from './requests'

type EvidenceChoice = {
  id: string
  kind: string
  document: { source?: string; dataset_id?: string }
}
type Context = {
  context_sha256: string
  references: Record<string, { evidence_id: string; sha256: string }>
  model_context: { observations: { id: string; claim: string }[] }
}
type Proposal = {
  observations: { claim: string; evidence_ids: string[] }[]
  hypotheses: {
    id: string
    description: string
    supporting_evidence: string[]
    contradicting_evidence: string[]
    missing_information: string[]
  }[]
  proposed_test: {
    catalog_action: string
    parameters: Record<string, never>
    expected_observations_by_hypothesis: Record<string, string>
    limitations: string[]
  }
  conclusion_status: string
}
type Suggestion = {
  id: string
  status: string
  reason?: string
  proposal?: Proposal
  runtime?: {
    model: string
    total_seconds: number
    first_token_seconds: number | null
  }
}
const actions: Record<string, string> = {
  review_policy_evidence: 'Revisar evidencia de políticas y recuperación',
  compare_player_observations: 'Comparar observaciones del reproductor',
  review_receiver_counters: 'Revisar contadores de recepción',
}

export function InvestigationAssistant({
  experimentId,
  evidence,
  onChoose,
  expanded = false,
}: {
  experimentId: string
  evidence: EvidenceChoice[]
  onChoose: (text: string, references: string[]) => void
  expanded?: boolean
}) {
  const [open, setOpen] = useState(expanded)
  const [dataset, setDataset] = useState('')
  const [investigation, setInvestigation] = useState('')
  const [question, setQuestion] = useState(
    'Propón una hipótesis alternativa y una revisión de evidencia que la distinga.'
  )
  const [chosen, setChosen] = useState(false)
  const post = useIdempotentPost()
  const choices = evidence.filter(
    (e) =>
      e.kind === 'dataset' &&
      ['live_campaign', 'historical_import'].includes(e.document.source ?? '')
  )
  const selected = dataset || choices[choices.length - 1]?.id || ''
  const suggest = useMutation({
    mutationFn: async () => {
      const existing = [...evidence]
        .reverse()
        .find(
          (e) =>
            e.kind === 'investigation_context' &&
            e.document.dataset_id === selected
        )
      const id =
        investigation ||
        existing?.id ||
        (
          await post<{ id: string }>(
            `/laboratory/experiments/${experimentId}/investigations`,
            { dataset_id: selected }
          )
        ).id
      setInvestigation(id)
      const context = (
        await api.get<{ context: Context }>(`/laboratory/investigations/${id}`)
      ).data.context
      const suggestion = await post<Suggestion>(
        `/laboratory/investigations/${id}/suggest`,
        { question }
      )
      return { context, suggestion }
    },
    onMutate: () => setChosen(false),
  })
  const result = suggest.data?.suggestion
  const context = suggest.data?.context
  const proposal = result?.proposal
  const references = (ids: string[]) =>
    ids.map((id) => (
      <a
        key={id}
        className='mr-2 underline'
        href={`#evidence-${context?.references[id]?.evidence_id}`}
        title={
          context?.model_context.observations.find((e) => e.id === id)?.claim
        }
      >
        {id}
      </a>
    ))
  return (
    <section
      aria-label='Investigador local'
      className='my-4 space-y-4 rounded-lg border bg-background p-5 text-sm'
    >
      <div className='flex flex-wrap items-center justify-between gap-3'>
        <Button
          variant='ghost'
          className='h-auto px-0 font-semibold'
          aria-expanded={open}
          aria-controls='lab-assistant-content'
          onClick={() => setOpen(!open)}
        >
          Asistente local de investigación
        </Button>
        <span className='rounded border px-2 py-1 text-xs text-muted-foreground'>
          GPU local · RTX 5070
        </span>
      </div>
      {open && (
        <div id='lab-assistant-content' className='space-y-3'>
          <Label htmlFor='ai-dataset'>Expediente analizado</Label>
          <select
            id='ai-dataset'
            className='block w-full rounded border bg-background p-2'
            value={selected}
            disabled={suggest.isPending}
            onChange={(e) => {
              setDataset(e.target.value)
              setInvestigation('')
              suggest.reset()
              setChosen(false)
            }}
          >
            {!choices.length && (
              <option value=''>
                Sin evidencia real o histórica disponible
              </option>
            )}
            {choices.map((e, i) => (
              <option key={e.id} value={e.id}>
                Dataset {i + 1} · {e.document.source} · {e.id.slice(0, 8)}
              </option>
            ))}
          </select>
          <Label htmlFor='ai-question'>Pregunta al tutor</Label>
          <Textarea
            id='ai-question'
            value={question}
            minLength={10}
            maxLength={500}
            disabled={suggest.isPending}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <Button
            disabled={
              !selected || question.trim().length < 10 || suggest.isPending
            }
            onClick={() => suggest.mutate()}
          >
            {suggest.isPending
              ? 'Consultando modelo local…'
              : 'Consultar asistente local'}
          </Button>
          {suggest.isError && (
            <p role='alert'>
              {apiErrorMessage(suggest.error, 'Asistente de IA fuera de línea')}
            </p>
          )}
          {result && result.status !== 'completed' && (
            <p role='status'>
              {result.reason === 'invalid_model_output'
                ? 'La propuesta no superó la validación de evidencia. Puedes seguir investigando sin IA.'
                : result.status === 'pending' ||
                    ['busy', 'acquisition_active'].includes(result.reason ?? '')
                  ? 'Asistente ocupado; el laboratorio sigue disponible.'
                  : 'Asistente de IA fuera de línea.'}
            </p>
          )}
          {proposal && (
            <div
              className='space-y-3'
              aria-label='Propuesta del investigador local'
            >
              <p className='font-medium'>
                Propuesta orientativa · conclusión: {proposal.conclusion_status}
              </p>
              <ul className='space-y-2 text-muted-foreground'>
                {proposal.observations.map((o, i) => (
                  <li key={i}>
                    {o.claim} {references(o.evidence_ids)}
                  </li>
                ))}
              </ul>
              <div className='grid gap-3 xl:grid-cols-2'>
                {proposal.hypotheses.map((h) => (
                  <article
                    key={h.id}
                    className='space-y-3 rounded-lg border bg-card p-4'
                  >
                    <h4 className='font-mono text-xs text-muted-foreground'>
                      {h.id}
                    </h4>
                    <p className='leading-relaxed font-medium'>
                      {h.description}
                    </p>
                    <p>A favor: {references(h.supporting_evidence)}</p>
                    <p>
                      En contra:{' '}
                      {h.contradicting_evidence.length
                        ? references(h.contradicting_evidence)
                        : 'Sin evidencia citada'}
                    </p>
                    <p>Falta comprobar: {h.missing_information.join(' ')}</p>
                    <p>
                      Observación esperada:{' '}
                      {
                        proposal.proposed_test
                          .expected_observations_by_hypothesis[h.id]
                      }
                    </p>
                  </article>
                ))}
              </div>
              <p>
                Siguiente revisión:{' '}
                {actions[proposal.proposed_test.catalog_action]}
              </p>
              <p className='text-xs'>
                {proposal.proposed_test.limitations.join(' ')}
              </p>
              <Button
                variant='outline'
                onClick={() => {
                  onChoose(
                    `${actions[proposal.proposed_test.catalog_action]}. ${Object.values(proposal.proposed_test.expected_observations_by_hypothesis).join(' ')}`,
                    [
                      ...new Set(
                        Object.values(context!.references).map(
                          (e) => e.evidence_id
                        )
                      ),
                    ]
                  )
                  setChosen(true)
                }}
              >
                Usar revisión en mi cuaderno
              </Button>
              {chosen && <p role='status'>Revisión copiada al borrador.</p>}
              {result?.runtime && (
                <p className='text-xs'>
                  {result.runtime.model} ·{' '}
                  {result.runtime.total_seconds.toFixed(2)} s · primer token{' '}
                  {result.runtime.first_token_seconds?.toFixed(2) ?? '—'} s
                </p>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
