import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { api, apiErrorMessage } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { InvestigationAssistant } from './investigation'
import { useIdempotentPost } from './requests'

type Preflight = {
  id: string
  created_at: string
  status: string
  stale: boolean
  document: {
    checks?: { id: string; status: string }[]
    pending_gates?: string[]
    errors?: Record<string, string>
  } | null
}
type Evidence = {
  id: string
  kind: string
  sha256: string
  document: {
    source?: string
    metric?: string
    hypothesis_outcome?: string
    mean_delta_seconds?: number | null
    effect_finding?: string
    validity_status?: string
    control_ack_observed?: boolean
    reasons?: string[]
    evidence_ids?: string[]
    measurement?: {
      throughput_bps: number | null
      duration_seconds: number | null
    } | null
    trials?: {
      ordinal: number
      treatment: string
      startup_seconds: number | null
      validity_status: string
      exclusion_reason?: string
    }[]
    excluded?: { block: number; reasons: string[] }[]
  }
}
type Note = {
  id: string
  created_at: string
  document: {
    prediction: string
    conclusion: string
    next_test: string
    outcome: string
    evidence_ids: string[]
  }
}

function ErrorLine({ error }: { error: unknown }) {
  return error ? (
    <p role='alert' className='text-sm text-destructive'>
      {apiErrorMessage(error, 'Operación no disponible.')}
    </p>
  ) : null
}

export function BindingForm({ assignmentId }: { assignmentId: string }) {
  const [observed, setObserved] = useState('')
  const [competing, setCompeting] = useState('')
  const post = useIdempotentPost()
  const bind = useMutation({
    mutationFn: () =>
      post(`/laboratory/assignments/${assignmentId}/bindings`, {
        observed_supi: observed,
        competing_supi: competing,
        dnn: 'internet',
      }),
  })
  return (
    <details className='mt-3'>
      <summary className='cursor-pointer'>
        Vincular sujetos reales para lectura
      </summary>
      <form
        className='mt-2 space-y-2'
        onSubmit={(e) => {
          e.preventDefault()
          bind.mutate()
        }}
      >
        <Label htmlFor={`observed-${assignmentId}`}>SUPI observado</Label>
        <Input
          id={`observed-${assignmentId}`}
          value={observed}
          required
          pattern='imsi-[0-9]{14,15}'
          onChange={(e) => setObserved(e.target.value)}
        />
        <Label htmlFor={`competing-${assignmentId}`}>SUPI competidor</Label>
        <Input
          id={`competing-${assignmentId}`}
          value={competing}
          required
          pattern='imsi-[0-9]{14,15}'
          onChange={(e) => setCompeting(e.target.value)}
        />
        <Button type='submit' disabled={bind.isPending}>
          Guardar vínculo de lectura
        </Button>
        {bind.isSuccess && <p role='status'>Vínculo guardado.</p>}
        <ErrorLine error={bind.error} />
      </form>
    </details>
  )
}

export function PreflightPanel({
  campaignId,
  assignmentId,
}: {
  campaignId: string
  assignmentId: string
}) {
  const [open, setOpen] = useState(false)
  const user = useAuthStore((s) => s.auth.user)
  const key = [
    'laboratory-research',
    user?.username,
    user?.testbed,
    campaignId,
    'preflights',
  ]
  const cache = useQueryClient()
  const post = useIdempotentPost()
  const list = useQuery({
    queryKey: key,
    enabled: open,
    queryFn: async () =>
      (
        await api.get<Preflight[]>(
          `/laboratory/campaigns/${campaignId}/preflights`
        )
      ).data,
  })
  const collect = useMutation({
    mutationFn: () =>
      post(`/laboratory/campaigns/${campaignId}/preflights`, {
        assignment_id: assignmentId,
      }),
    onSuccess: () => cache.invalidateQueries({ queryKey: key }),
  })
  return (
    <details
      onToggle={(e) => setOpen(e.currentTarget.open)}
      className='my-3 rounded border p-3'
    >
      <summary className='cursor-pointer font-medium'>
        Preflight vivo · solo lectura
      </summary>
      <Button
        variant='outline'
        disabled={!assignmentId || collect.isPending}
        onClick={() => collect.mutate()}
      >
        {collect.isPending
          ? 'Consultando testbed…'
          : 'Comprobar condiciones vivas'}
      </Button>
      {!assignmentId && (
        <p className='text-xs'>
          Selecciona la asignación del plan; el docente debe haber vinculado los
          sujetos.
        </p>
      )}
      <ErrorLine error={list.error || collect.error} />
      {list.data?.map((p) => (
        <div key={p.id} className='mt-3 text-sm'>
          <p>
            {new Date(p.created_at).toLocaleString()} · {p.status} ·{' '}
            {p.stale ? 'Observación caducada' : 'Observación reciente'}
          </p>
          <ul>
            {p.document?.checks?.map((c) => (
              <li key={c.id}>
                {c.id}: {c.status === 'passed' ? 'Comprobado' : 'Bloqueado'}
              </li>
            ))}
          </ul>
          {!!p.document?.pending_gates?.length && (
            <div>
              <p>La ejecución real permanece bloqueada.</p>
              <p>
                Pendientes para actuar: {p.document.pending_gates.join(', ')}
              </p>
            </div>
          )}
          {!!p.document?.errors &&
            Object.entries(p.document.errors).map(([node, error]) => (
              <p key={node}>
                {node}: {error}
              </p>
            ))}
        </div>
      ))}
    </details>
  )
}

export function ResearchPanel({
  experimentId,
  expanded = false,
  active = true,
}: {
  experimentId: string
  expanded?: boolean
  active?: boolean
}) {
  const [open, setOpen] = useState(expanded)
  const [notebookOpen, setNotebookOpen] = useState(!expanded)
  const [form, setForm] = useState({
    prediction: '',
    conclusion: '',
    next_test: '',
    outcome: 'pending',
    evidence_ids: [] as string[],
  })
  const user = useAuthStore((s) => s.auth.user)
  const prefix = [
    'laboratory-research',
    user?.username,
    user?.testbed,
    experimentId,
  ]
  const cache = useQueryClient()
  const post = useIdempotentPost()
  const evidence = useQuery({
    queryKey: [
      'laboratory-research',
      user?.username,
      user?.testbed,
      experimentId,
      'evidence',
    ],
    enabled: open && active,
    queryFn: async () =>
      (
        await api.get<Evidence[]>(
          `/laboratory/experiments/${experimentId}/evidence`
        )
      ).data,
  })
  const notes = useQuery({
    queryKey: [
      'laboratory-research',
      user?.username,
      user?.testbed,
      experimentId,
      'notes',
    ],
    enabled: open && active,
    queryFn: async () =>
      (await api.get<Note[]>(`/laboratory/experiments/${experimentId}/notes`))
        .data,
  })
  const save = useMutation({
    mutationFn: () =>
      post(`/laboratory/experiments/${experimentId}/notes`, form),
    onSuccess: () => cache.invalidateQueries({ queryKey: prefix }),
  })
  const analyze = useMutation({
    mutationFn: (id: string) =>
      post(
        `/laboratory/experiments/${experimentId}/evidence/${id}/analyze`,
        {}
      ),
    onSuccess: () => cache.invalidateQueries({ queryKey: prefix }),
  })
  const download = useMutation({
    mutationFn: async () => {
      const response = await api.get(
        `/laboratory/experiments/${experimentId}/export`,
        { responseType: 'blob' }
      )
      const url = URL.createObjectURL(response.data)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `laboratory-${experimentId}.zip`
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    },
  })
  return (
    <details
      className='space-y-4 rounded-lg border bg-card p-5'
      open={open}
      onToggle={(e) => setOpen(e.currentTarget.open)}
      onClickCapture={(event) => {
        const link = (event.target as HTMLElement).closest(
          'a[href^="#evidence-"]'
        )
        const target = link?.getAttribute('href')?.slice(1)
        let parent = target
          ? document.getElementById(target)?.parentElement
          : null
        while (parent) {
          if (parent instanceof HTMLDetailsElement) parent.open = true
          parent = parent.parentElement
        }
      }}
    >
      <summary className='cursor-pointer font-semibold'>
        Evidencias, comparación y cuaderno
      </summary>
      <Button
        variant='outline'
        disabled={download.isPending}
        onClick={() => download.mutate()}
      >
        Exportar expediente con hashes
      </Button>
      <ErrorLine
        error={
          evidence.error ||
          notes.error ||
          save.error ||
          analyze.error ||
          download.error
        }
      />
      <InvestigationAssistant
        key={experimentId}
        experimentId={experimentId}
        expanded={expanded}
        evidence={evidence.data ?? []}
        onChoose={(text, references) => {
          setForm((previous) => ({
            ...previous,
            next_test: text,
            evidence_ids: references,
          }))
          setNotebookOpen(true)
        }}
      />
      <details open={!expanded} className='rounded-lg border p-4'>
        <summary className='cursor-pointer font-medium'>
          Evidencias · {evidence.data?.length ?? 0}
        </summary>
        {evidence.data?.length === 0 && (
          <p className='my-3 text-sm'>No hay medidas incorporadas.</p>
        )}
        {evidence.data?.map((e) => (
          <article
            key={e.id}
            id={`evidence-${e.id}`}
            className='my-3 rounded border p-3 text-sm'
          >
            <p className='font-medium'>
              {e.kind} · {e.document.source ?? 'Expediente'}
            </p>
            <code className='block text-xs break-all'>
              {e.id} · SHA-256 {e.sha256}
            </code>
            {e.document.trials && (
              <div className='overflow-x-auto'>
                <table className='my-2 w-full text-left'>
                  <thead>
                    <tr>
                      <th>Ensayo</th>
                      <th>Control</th>
                      <th>Espera (s)</th>
                      <th>Validez</th>
                    </tr>
                  </thead>
                  <tbody>
                    {e.document.trials.map((t) => (
                      <tr key={t.ordinal}>
                        <td>{t.ordinal}</td>
                        <td>{t.treatment}</td>
                        <td>{t.startup_seconds ?? 'Sin dato'}</td>
                        <td>
                          {t.validity_status} {t.exclusion_reason}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {e.kind === 'effect_analysis' && (
              <div
                className='my-2 space-y-1'
                aria-label='Verificación de efecto'
              >
                <p>Dictamen de efecto: {e.document.effect_finding}</p>
                <p>Validez de observación: {e.document.validity_status}</p>
                <p>
                  ACK observado:{' '}
                  {e.document.control_ack_observed ? 'Sí' : 'No consta'} · no
                  demuestra enforcement.
                </p>
                <p>
                  Tasa recibida:{' '}
                  {e.document.measurement?.throughput_bps ?? 'Sin dato'} bps
                </p>
                {e.document.reasons?.map((reason) => (
                  <p key={reason}>{reason}</p>
                ))}
                <p>
                  Alcance por sesión; no acredita límite agregado por slice.
                </p>
              </div>
            )}
            {e.document.evidence_ids?.map((ref) => (
              <a
                key={ref}
                href={`#evidence-${ref}`}
                className='mr-3 inline-block text-primary underline'
              >
                Abrir evidencia {ref}
              </a>
            ))}
            <details className='my-2'>
              <summary className='cursor-pointer'>
                Ver documento y procedencia
              </summary>
              <pre className='max-h-80 overflow-auto rounded bg-muted p-2 text-xs break-all whitespace-pre-wrap'>
                {JSON.stringify(e.document, null, 2)}
              </pre>
            </details>
            {(e.kind === 'dataset' || e.kind === 'effect_evidence') && (
              <Button
                size='sm'
                variant='outline'
                disabled={analyze.isPending}
                onClick={() => analyze.mutate(e.id)}
              >
                Reanalizar sin usar el Core
              </Button>
            )}
            {e.kind === 'analysis' && (
              <>
                <p>
                  Diferencia media emparejada (activado − desactivado):{' '}
                  {e.document.mean_delta_seconds ?? 'Sin parejas válidas'}
                </p>
                <p>Dictamen del analizador: {e.document.hypothesis_outcome}</p>
                {e.document.excluded?.map((x) => (
                  <p key={x.block}>
                    Bloque {x.block} excluido: {x.reasons.join(', ')}
                  </p>
                ))}
              </>
            )}
          </article>
        ))}
      </details>
      <details
        open={notebookOpen}
        onToggle={(event) => setNotebookOpen(event.currentTarget.open)}
        className='rounded-lg border p-4'
      >
        <summary className='cursor-pointer font-medium'>
          Cuaderno del alumno · entradas conservadas
        </summary>
        <form
          className='mt-4 space-y-3'
          onSubmit={(e) => {
            e.preventDefault()
            save.mutate()
          }}
        >
          {(
            [
              ['prediction', 'Predicción y fundamento'],
              ['conclusion', 'Conclusión y limitaciones'],
              ['next_test', 'Siguiente prueba propuesta'],
            ] as const
          ).map(([field, label]) => (
            <div key={field}>
              <Label htmlFor={`note-${field}`}>{label}</Label>
              <Textarea
                id={`note-${field}`}
                value={form[field]}
                required={field === 'prediction'}
                minLength={field === 'prediction' ? 10 : undefined}
                maxLength={field === 'conclusion' ? 8000 : 4000}
                onChange={(e) => setForm({ ...form, [field]: e.target.value })}
              />
            </div>
          ))}
          <Label htmlFor='note-outcome'>Interpretación del alumno</Label>
          <select
            id='note-outcome'
            className='block rounded border bg-background p-2'
            value={form.outcome}
            onChange={(e) => setForm({ ...form, outcome: e.target.value })}
          >
            <option value='pending'>Pendiente</option>
            <option value='supported'>Apoyada</option>
            <option value='not_supported'>No apoyada</option>
            <option value='inconclusive'>Inconclusa</option>
          </select>
          <fieldset>
            <legend>Referencias de evidencia</legend>
            {evidence.data?.map((e) => (
              <Label key={e.id} className='my-1 flex gap-2 text-xs'>
                <input
                  type='checkbox'
                  checked={form.evidence_ids.includes(e.id)}
                  onChange={(event) =>
                    setForm({
                      ...form,
                      evidence_ids: event.target.checked
                        ? [...form.evidence_ids, e.id]
                        : form.evidence_ids.filter((id) => id !== e.id),
                    })
                  }
                />
                {e.kind} · {e.id}
              </Label>
            ))}
          </fieldset>
          <Button type='submit' disabled={save.isPending}>
            Guardar entrada del cuaderno
          </Button>
          {save.isSuccess && <p role='status'>Entrada conservada.</p>}
        </form>
        {notes.data?.map((n) => (
          <article key={n.id} className='mt-3 border-t pt-3 text-sm'>
            <p>
              {new Date(n.created_at).toLocaleString()} · Interpretación del
              alumno: {n.document.outcome}
            </p>
            <p>{n.document.prediction}</p>
            <p>{n.document.conclusion}</p>
            <p>Siguiente prueba: {n.document.next_test}</p>
            <p>
              Referencias:{' '}
              {n.document.evidence_ids.join(', ') || 'Sin referencias'}
            </p>
          </article>
        ))}
      </details>
    </details>
  )
}
