import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { api, apiErrorMessage } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { EmsPage } from '@/features/ems-page'
import './laboratory.css'
import { useIdempotentPost } from './requests'
import { ResearchPanel } from './research'
import { AssignmentsPanel, CampaignExecution } from './runtime'

type Descriptor = {
  schema_version: 1
  template: 'qoe_closed_loop'
  template_version: 1
  scenario: '5g-sa'
  observed_ue: string | null
  competing_ue: string | null
  load_mbps: number[]
  repetitions: number | null
  measurement_seconds: number | null
  campaign_budget_bytes: number | null
  capture_budget_bytes: number | null
  seed: number
  primary_metric: 'player_startup_delay_seconds'
  design: 'paired_randomized_blocks'
}
type Revision = {
  id: string
  number: number
  sha256: string
  descriptor: Descriptor
}
type Run = {
  ordinal: number
  block: number
  load_mbps: number
  repetition: number
  treatment: string
  measurement_seconds: number
}
type Campaign = { id: string; revision_id: string; plan: { runs: Run[] } }
type Experiment = {
  id: string
  title: string
  question: string
  hypothesis: string
  created_at: string
}
type Detail = Experiment & { revisions: Revision[]; campaigns: Campaign[] }
type Validation = {
  revision_id: string
  design_valid: boolean
  issues: { field: string; message: string }[]
  estimates: {
    runs: number
    measurement_seconds: number
    competing_payload_bytes: number
  }
  warnings: string[]
}

const initial: Descriptor = {
  schema_version: 1,
  template: 'qoe_closed_loop',
  template_version: 1,
  scenario: '5g-sa',
  observed_ue: null,
  competing_ue: null,
  load_mbps: [],
  repetitions: null,
  measurement_seconds: null,
  campaign_budget_bytes: null,
  capture_budget_bytes: null,
  seed: 42017,
  primary_metric: 'player_startup_delay_seconds',
  design: 'paired_randomized_blocks',
}

export function LaboratoryPage() {
  const user = useAuthStore((s) => s.auth.user)
  // Remount private design state when identity/testbed changes.
  return (
    <Workspace
      key={`${user?.username}:${user?.testbed}:${user?.role}`}
      identity={`${user?.username}:${user?.testbed}:${user?.role}`}
    />
  )
}

function Workspace({ identity }: { identity: string }) {
  const post = useIdempotentPost()
  const cache = useQueryClient()
  const [tab, setTab] = useState('results')
  const [newExperimentOpen, setNewExperimentOpen] = useState(true)
  const [selected, setSelected] = useState<string | null>(null)
  const [title, setTitle] = useState('')
  const [question, setQuestion] = useState('')
  const [hypothesis, setHypothesis] = useState('')
  const list = useQuery({
    queryKey: ['laboratory', identity, 'experiments'],
    queryFn: async () =>
      (await api.get<Experiment[]>('/laboratory/experiments')).data,
  })
  const detail = useQuery({
    queryKey: ['laboratory', identity, selected],
    enabled: !!selected,
    queryFn: async () =>
      (await api.get<Detail>(`/laboratory/experiments/${selected}`)).data,
  })
  const create = useMutation({
    mutationFn: () =>
      post<Experiment>('/laboratory/experiments', {
        title,
        question,
        hypothesis,
      }),
    onSuccess: (experiment) => {
      setSelected(experiment.id)
      setNewExperimentOpen(false)
      setTitle('')
      setQuestion('')
      setHypothesis('')
      void cache.invalidateQueries({
        queryKey: ['laboratory', identity, 'experiments'],
      })
    },
  })

  return (
    <div className='dark laboratory min-h-full w-full bg-background text-foreground'>
      <EmsPage title='Laboratorio de investigación 5G'>
        <div className='w-full space-y-4'>
          <Tabs value={tab} onValueChange={setTab} className='w-full space-y-4'>
            <div className='flex flex-wrap items-center justify-between gap-2 border-b pb-2'>
              <TabsList
                aria-label='Secciones del laboratorio'
                className='h-auto justify-start gap-1 rounded-none bg-transparent p-0'
              >
                <TabsTrigger value='results' className='flex-none px-4 py-2'>
                  Resultados
                </TabsTrigger>
                <TabsTrigger value='assistant' className='flex-none px-4 py-2'>
                  Asistente local
                </TabsTrigger>
                <TabsTrigger
                  value='configuration'
                  className='flex-none px-4 py-2'
                >
                  Configuración
                </TabsTrigger>
              </TabsList>
              <Button
                variant='outline'
                size='sm'
                onClick={() => {
                  setNewExperimentOpen(true)
                  setTab('configuration')
                }}
              >
                Nuevo experimento
              </Button>
            </div>
            <div className='grid min-w-0 gap-6 lg:grid-cols-[220px_minmax(0,1fr)]'>
              <aside className='min-w-0'>
                <section className='space-y-2' aria-label='Mis experimentos'>
                  <h3 className='font-semibold'>Mis experimentos</h3>
                  {list.isPending && <p>Cargando experimentos…</p>}
                  {list.isError && (
                    <p role='alert'>
                      {apiErrorMessage(
                        list.error,
                        'No se pudo cargar el catálogo.'
                      )}
                    </p>
                  )}
                  {list.data?.length === 0 && (
                    <p className='text-sm text-muted-foreground'>
                      Todavía no has creado experimentos.
                    </p>
                  )}
                  {list.data?.map((experiment) => (
                    <button
                      key={experiment.id}
                      type='button'
                      aria-pressed={selected === experiment.id}
                      onClick={() => {
                        setSelected(experiment.id)
                        setNewExperimentOpen(false)
                      }}
                      className={`w-full rounded-md border px-3 py-2.5 text-left text-sm break-words transition-colors ${selected === experiment.id ? 'border-white/20 bg-white/5 text-foreground' : 'border-transparent text-muted-foreground hover:bg-muted'}`}
                    >
                      {experiment.title}
                    </button>
                  ))}
                </section>
              </aside>
              <div className='min-w-0 space-y-5'>
                <TabsContent
                  value='configuration'
                  forceMount
                  hidden={tab !== 'configuration'}
                  className='space-y-5 data-[state=inactive]:hidden'
                >
                  <details
                    className='rounded-lg border bg-card p-5'
                    open={newExperimentOpen}
                    onToggle={(event) =>
                      setNewExperimentOpen(event.currentTarget.open)
                    }
                  >
                    <summary className='cursor-pointer font-medium'>
                      Nuevo experimento
                    </summary>
                    <form
                      className='mt-4 grid gap-3'
                      onSubmit={(e) => {
                        e.preventDefault()
                        create.mutate()
                      }}
                    >
                      <Label htmlFor='lab-title'>Título</Label>
                      <Input
                        id='lab-title'
                        required
                        minLength={3}
                        maxLength={160}
                        value={title}
                        onChange={(e) => setTitle(e.target.value)}
                      />
                      <Label htmlFor='lab-question'>
                        ¿Qué quieres estudiar?
                      </Label>
                      <Textarea
                        id='lab-question'
                        required
                        minLength={10}
                        maxLength={2000}
                        value={question}
                        onChange={(e) => setQuestion(e.target.value)}
                      />
                      <Label htmlFor='lab-hypothesis'>
                        ¿Qué esperas que ocurra y por qué?
                      </Label>
                      <Textarea
                        id='lab-hypothesis'
                        required
                        minLength={10}
                        maxLength={4000}
                        value={hypothesis}
                        onChange={(e) => setHypothesis(e.target.value)}
                      />
                      <Button disabled={create.isPending} type='submit'>
                        Guardar experimento
                      </Button>
                      {create.isError && (
                        <p role='alert' className='text-sm text-destructive'>
                          {apiErrorMessage(create.error, 'No se pudo guardar.')}
                        </p>
                      )}
                    </form>
                  </details>
                  <AssignmentsPanel />
                  {detail.data && (
                    <Design
                      key={detail.data.id}
                      experiment={detail.data}
                      refresh={() => {
                        void cache.invalidateQueries({
                          queryKey: ['laboratory', identity, selected],
                        })
                      }}
                    />
                  )}
                </TabsContent>
                <TabsContent
                  value='results'
                  forceMount
                  hidden={tab !== 'results'}
                  className='space-y-5 data-[state=inactive]:hidden'
                >
                  {detail.data && <CampaignResults experiment={detail.data} />}
                </TabsContent>
                <TabsContent
                  value='assistant'
                  forceMount
                  hidden={tab !== 'assistant'}
                  className='data-[state=inactive]:hidden'
                >
                  {detail.data && (
                    <ResearchPanel
                      key={detail.data.id}
                      experimentId={detail.data.id}
                      expanded
                      active={tab === 'assistant'}
                    />
                  )}
                </TabsContent>
                {!selected && tab !== 'configuration' && (
                  <div className='rounded-lg border border-dashed px-6 py-16 text-center'>
                    <p className='text-sm text-muted-foreground'>
                      Crea o selecciona un experimento.
                    </p>
                    <Button
                      className='mt-4'
                      variant='outline'
                      onClick={() => setTab('configuration')}
                    >
                      Configurar experimento
                    </Button>
                  </div>
                )}
                {selected && detail.isPending && <p>Cargando diseño…</p>}
                {detail.isError && (
                  <p role='alert'>
                    {apiErrorMessage(
                      detail.error,
                      'No se pudo abrir el diseño.'
                    )}
                  </p>
                )}
              </div>
            </div>
          </Tabs>
        </div>
      </EmsPage>
    </div>
  )
}

function Design({
  experiment,
  refresh,
}: {
  experiment: Detail
  refresh: () => void
}) {
  const post = useIdempotentPost()
  const [descriptor, setDescriptor] = useState<Descriptor>(
    experiment.revisions[0]?.descriptor ?? initial
  )
  const [levels, setLevels] = useState(descriptor.load_mbps.join(', '))
  const [activeRevision, setActiveRevision] = useState<Revision | null>(
    experiment.revisions[0] ?? null
  )
  const [validation, setValidation] = useState<Validation | null>(null)
  const [dirty, setDirty] = useState(false)
  const save = useMutation({
    mutationFn: async () => {
      const parsed = levels.trim()
        ? levels.split(',').map((v) => (v.trim() === '' ? NaN : Number(v)))
        : []
      if (parsed.some((v) => !Number.isFinite(v)))
        throw new Error(
          'Usa cargas numéricas separadas por comas, por ejemplo 0, 5, 10.'
        )
      return post<Revision>(
        `/laboratory/experiments/${experiment.id}/revisions`,
        { ...descriptor, load_mbps: parsed }
      )
    },
    onSuccess: (revision) => {
      setActiveRevision(revision)
      setDescriptor(revision.descriptor)
      setDirty(false)
      setValidation(null)
      refresh()
    },
  })
  const validate = useMutation({
    mutationFn: async () =>
      (
        await api.post<Validation>(
          `/laboratory/revisions/${activeRevision?.id}/validate`
        )
      ).data,
    onSuccess: setValidation,
  })
  const campaign = useMutation({
    mutationFn: () =>
      post('/laboratory/campaigns', { revision_id: activeRevision?.id }),
    onSuccess: refresh,
  })
  const busy = save.isPending || validate.isPending || campaign.isPending
  function change(field: keyof Descriptor, value: string | number | null) {
    setDescriptor((previous) => ({ ...previous, [field]: value }))
    setDirty(true)
    setValidation(null)
  }
  const numeric = [
    ['repetitions', 'Repeticiones por nivel', 1, 30],
    ['measurement_seconds', 'Medición por ensayo (s)', 10, 300],
    ['campaign_budget_bytes', 'Presupuesto de tráfico total (bytes)', 1, 1e12],
    ['capture_budget_bytes', 'Presupuesto de captura (bytes)', 1, 1e11],
    ['seed', 'Semilla del orden de ensayos', 0, 2147483647],
  ] as const

  return (
    <div className='space-y-5'>
      <div className='rounded-lg border p-5'>
        <h3 className='text-xl font-semibold'>{experiment.title}</h3>
        <p className='mt-2'>{experiment.question}</p>
        <p className='mt-2 text-sm text-muted-foreground'>
          Hipótesis: {experiment.hypothesis}
        </p>
      </div>
      <form
        className='space-y-4 rounded-lg border p-5'
        onSubmit={(e) => {
          e.preventDefault()
          save.mutate()
        }}
      >
        <h3 className='font-semibold'>Diseño · QoE con y sin closed-loop</h3>
        <p className='text-xs text-muted-foreground'>
          Diseño y ensayo dry_run.
        </p>
        <fieldset disabled={busy} className='grid gap-4 md:grid-cols-2'>
          {(['observed_ue', 'competing_ue'] as const).map((field) => (
            <div key={field} className='space-y-2'>
              <Label htmlFor={`lab-${field}`}>
                {field === 'observed_ue'
                  ? 'Alias del UE observado'
                  : 'Alias del UE competidor'}
              </Label>
              <Input
                id={`lab-${field}`}
                value={descriptor[field] ?? ''}
                maxLength={80}
                pattern='[a-zA-Z0-9_-]+'
                onChange={(e) => change(field, e.target.value || null)}
                placeholder='Alias de laboratorio, sin credenciales'
              />
            </div>
          ))}
          <div className='space-y-2'>
            <Label htmlFor='lab-loads'>
              Niveles de carga competidora (Mbps)
            </Label>
            <Input
              id='lab-loads'
              value={levels}
              placeholder='0, 5, 10'
              onChange={(e) => {
                setLevels(e.target.value)
                setDirty(true)
                setValidation(null)
              }}
            />
          </div>
          {numeric.map(([field, label, min, max]) => (
            <div key={field} className='space-y-2'>
              <Label htmlFor={`lab-${field}`}>{label}</Label>
              <Input
                id={`lab-${field}`}
                type='number'
                min={min}
                max={max}
                step={1}
                required={field === 'seed'}
                value={descriptor[field] ?? ''}
                onChange={(e) =>
                  change(
                    field,
                    e.target.value === '' ? null : Number(e.target.value)
                  )
                }
              />
            </div>
          ))}
        </fieldset>
        <div className='flex flex-wrap gap-2'>
          <Button type='submit' disabled={busy}>
            Guardar nueva revisión
          </Button>
          <Button
            type='button'
            variant='outline'
            disabled={!activeRevision || dirty || busy}
            onClick={() => validate.mutate()}
          >
            Validar diseño guardado
          </Button>
          <Button
            type='button'
            variant='outline'
            disabled={!validation?.design_valid || dirty || busy}
            onClick={() => campaign.mutate()}
          >
            Crear plan de ensayos
          </Button>
        </div>
        {dirty && (
          <p className='text-sm text-muted-foreground'>
            Cambios sin guardar. Guarda una revisión antes de validar.
          </p>
        )}
        {[save, validate, campaign].map(
          (mutation, i) =>
            mutation.isError && (
              <p key={i} role='alert' className='text-sm text-destructive'>
                {apiErrorMessage(
                  mutation.error,
                  'No se pudo completar la operación.'
                )}
              </p>
            )
        )}
      </form>
      {activeRevision && (
        <div className='rounded-lg border p-4 text-sm'>
          Revisión {activeRevision.number} · huella SHA-256
          <code className='mt-1 block text-xs break-all text-muted-foreground'>
            {activeRevision.sha256}
          </code>
        </div>
      )}
      {validation && (
        <section className='space-y-3 rounded-lg border p-5' aria-live='polite'>
          <h3 className='font-semibold'>
            {validation.design_valid
              ? 'Diseño completo para planificación'
              : 'Diseño pendiente'}
          </h3>

          <ul className='list-inside list-disc text-sm'>
            {validation.issues.map((issue) => (
              <li key={`${issue.field}:${issue.message}`}>
                {issue.field}: {issue.message}
              </li>
            ))}
          </ul>
          <p>
            {validation.estimates.runs} ensayos ·{' '}
            {validation.estimates.measurement_seconds} s de medición ·{' '}
            {validation.estimates.competing_payload_bytes.toLocaleString()}{' '}
            bytes de carga competidora
          </p>
          <ul className='list-inside list-disc space-y-1 text-sm text-muted-foreground'>
            {validation.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </section>
      )}
      {experiment.revisions.length > 0 && (
        <section className='rounded-lg border p-4'>
          <h3 className='mb-2 font-semibold'>Revisiones guardadas</h3>
          <div className='flex flex-wrap gap-2'>
            {experiment.revisions.map((revision) => (
              <Button
                key={revision.id}
                variant='outline'
                size='sm'
                disabled={busy || dirty}
                onClick={() => {
                  setActiveRevision(revision)
                  setDescriptor(revision.descriptor)
                  setLevels(revision.descriptor.load_mbps.join(', '))
                  setValidation(null)
                }}
              >
                Revisión {revision.number}
              </Button>
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

function CampaignResults({ experiment }: { experiment: Detail }) {
  return (
    <div className='space-y-5'>
      {experiment.campaigns.length === 0 && (
        <div className='rounded-lg border border-dashed px-6 py-16 text-center text-sm text-muted-foreground'>
          Sin planes de ensayos. Prepara el diseño en Configuración.
        </div>
      )}
      {experiment.campaigns.map((plan) => (
        <section key={plan.id} className='rounded-lg border p-5'>
          <h3 className='font-semibold'>
            Plan guardado · {plan.plan.runs.length} ensayos
          </h3>
          <p className='mb-3 text-xs break-all text-muted-foreground'>
            {plan.id} · Revisión{' '}
            {
              experiment.revisions.find((r) => r.id === plan.revision_id)
                ?.number
            }{' '}
            · Diseño inmutable
          </p>
          <CampaignExecution campaignId={plan.id} />
          <details className='mt-4 border-t pt-3'>
            <summary className='cursor-pointer text-sm text-muted-foreground'>
              Orden de ensayos · {plan.plan.runs.length}
            </summary>
            <div className='mt-3 max-h-80 overflow-auto'>
              <table className='w-full text-left text-sm'>
                <thead>
                  <tr>
                    <th className='p-2'>Orden</th>
                    <th>Bloque</th>
                    <th>Carga Mbps</th>
                    <th>Tratamiento</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.plan.runs.map((run) => (
                    <tr key={run.ordinal} className='border-t'>
                      <td className='p-2'>{run.ordinal}</td>
                      <td>{run.block}</td>
                      <td>{run.load_mbps}</td>
                      <td>
                        {run.treatment === 'controller_enabled_verified'
                          ? 'Control activado'
                          : 'Control desactivado'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </section>
      ))}
    </div>
  )
}
