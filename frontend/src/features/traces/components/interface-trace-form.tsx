import { useMemo, useState, type FormEvent } from 'react'
import { Loader2, Play } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import type {
  InterfaceTraceDraft,
  TraceCapabilities,
  TraceCaptureTarget,
} from '../types'
import { TraceFormHelp } from './trace-form-help'

type InterfaceTraceFormProps = {
  capabilities?: TraceCapabilities
  isLoading: boolean
  canCreate: boolean
  isSubmitting: boolean
  onSubmit: (draft: InterfaceTraceDraft) => void
}

export function InterfaceTraceForm({
  capabilities,
  isLoading,
  canCreate,
  isSubmitting,
  onSubmit,
}: InterfaceTraceFormProps) {
  const [name, setName] = useState(() => makeTaskName('IF-N2'))
  const [selectedAgent, setSelectedAgent] = useState('')
  const [selectedComponent, setSelectedComponent] = useState('')
  const [selectedTarget, setSelectedTarget] = useState('')
  const [duration, setDuration] = useState('60')
  const [maxMegabytes, setMaxMegabytes] = useState('25')

  const agents = capabilities?.capture_agents ?? []
  const networkFunctions = capabilities?.network_functions ?? []
  const agentId = selectedAgent || agents[0]?.id || 'primary'
  const defaultNf =
    networkFunctions.find(
      (item) => item.id === 'amf' || item.component_id === 'amf'
    ) ?? networkFunctions[0]
  const componentId =
    selectedComponent || defaultNf?.component_id || defaultNf?.id || ''
  const component = networkFunctions.find(
    (item) => item.id === componentId || item.component_id === componentId
  )
  const targets = useMemo(
    () =>
      (capabilities?.capture_targets ?? []).filter((target) =>
        targetSupportsComponent(target, componentId)
      ),
    [capabilities?.capture_targets, componentId]
  )
  const preferredTarget =
    targets.find((item) => item.id.toLowerCase() === 'n2') ?? targets[0]
  const targetId = targets.some((item) => item.id === selectedTarget)
    ? selectedTarget
    : (preferredTarget?.id ?? '')
  const target = targets.find((item) => item.id === targetId)
  const limit = capabilities?.limits
  const durationOptions = [30, 60, 120, 300, 600, 900].filter(
    (value) => value <= (limit?.max_duration_seconds ?? 900)
  )
  const sizeOptions = [25, 50, 100, 250, 500].filter(
    (value) => value <= (limit?.max_megabytes ?? 500)
  )

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!component || !target || !name.trim()) return
    onSubmit({
      name: name.trim(),
      scenario_id: capabilities?.scenario_id ?? '5g-sa',
      testbed_id: capabilities?.testbed_id ?? 'local',
      capture_agent_id: agentId,
      node_id: component.node_id,
      component_id: component.component_id ?? component.id,
      capture_point: target.id,
      duration_seconds: Number(duration),
      max_megabytes: Number(maxMegabytes),
    })
  }

  if (isLoading) return <FormSkeleton />

  return (
    <form
      onSubmit={submit}
      className='overflow-hidden rounded-lg border bg-card'
    >
      <div className='flex items-center justify-between border-b px-5 py-3'>
        <h2 className='text-sm font-semibold'>Nueva captura por interfaz</h2>
        <TraceFormHelp mode='interface' />
      </div>
      <fieldset disabled={isSubmitting} className='min-w-0 divide-y'>
        <section className='px-5 py-5'>
          <div className='grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3'>
            <Field
              className='sm:col-span-2 lg:col-span-3'
              label='Nombre de la tarea'
              htmlFor='interface-task-name'
            >
              <Input
                id='interface-task-name'
                value={name}
                maxLength={80}
                required
                onChange={(event) => setName(event.target.value)}
                placeholder='Ej. Registro-UE01-N2'
              />
            </Field>

            <Field label='Agente de captura' htmlFor='interface-agent'>
              <Select value={agentId} onValueChange={setSelectedAgent}>
                <SelectTrigger className='w-full' id='interface-agent'>
                  <SelectValue placeholder='Seleccione un agente' />
                </SelectTrigger>
                <SelectContent>
                  {agents.map((agent) => (
                    <SelectItem key={agent.id} value={agent.id}>
                      {agent.label ?? agent.hostname ?? agent.id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>

            <Field label='Función de red' htmlFor='interface-nf'>
              <Select
                value={componentId}
                onValueChange={(value) => {
                  setSelectedComponent(value)
                  setSelectedTarget('')
                }}
              >
                <SelectTrigger className='w-full' id='interface-nf'>
                  <SelectValue placeholder='Seleccione una NF' />
                </SelectTrigger>
                <SelectContent>
                  {networkFunctions.map((item) => {
                    const id = item.component_id ?? item.id
                    return (
                      <SelectItem key={`${item.node_id}:${id}`} value={id}>
                        {item.label ?? id.toUpperCase()} · {item.node_id}
                      </SelectItem>
                    )
                  })}
                </SelectContent>
              </Select>
            </Field>

            <Field label='Interfaz 3GPP' htmlFor='interface-target'>
              <Select value={targetId} onValueChange={setSelectedTarget}>
                <SelectTrigger className='w-full' id='interface-target'>
                  <SelectValue placeholder='Seleccione una interfaz' />
                </SelectTrigger>
                <SelectContent>
                  {targets.map((item) => (
                    <SelectItem key={item.id} value={item.id}>
                      {item.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!targets.length && componentId && (
                <p className='text-xs text-amber-600'>
                  La NF seleccionada no tiene puntos de captura declarados.
                </p>
              )}
            </Field>
          </div>
        </section>

        <section className='px-5 py-5'>
          <div className='grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3'>
            <Field label='Duración' htmlFor='interface-duration'>
              <Select value={duration} onValueChange={setDuration}>
                <SelectTrigger className='w-full' id='interface-duration'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {durationOptions.map((value) => (
                    <SelectItem key={value} value={String(value)}>
                      {formatDuration(value)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label='Tamaño máximo' htmlFor='interface-size'>
              <Select value={maxMegabytes} onValueChange={setMaxMegabytes}>
                <SelectTrigger className='w-full' id='interface-size'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {sizeOptions.map((value) => (
                    <SelectItem key={value} value={String(value)}>
                      {value} MB
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
          </div>
        </section>
      </fieldset>

      <div className='flex flex-wrap items-center justify-end gap-3 border-t bg-muted/20 px-5 py-3'>
        {!canCreate && (
          <p className='mr-auto text-xs text-muted-foreground'>
            Sin permiso para iniciar capturas.
          </p>
        )}
        <Button
          className='min-w-36'
          type='submit'
          disabled={
            !canCreate || !component || !target || !name.trim() || isSubmitting
          }
        >
          {isSubmitting ? <Loader2 className='animate-spin' /> : <Play />}
          Iniciar captura
        </Button>
      </div>
    </form>
  )
}

function Field({
  label,
  htmlFor,
  className,
  children,
}: {
  label: string
  htmlFor: string
  className?: string
  children: React.ReactNode
}) {
  return (
    <div className={`space-y-2 ${className ?? ''}`}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  )
}

function FormSkeleton() {
  return <div className='h-80 animate-pulse rounded-lg border bg-muted/40' />
}

function targetSupportsComponent(
  target: TraceCaptureTarget,
  componentId: string
) {
  if (!componentId) return true
  const supported = [
    target.component_id,
    ...(target.component_ids ?? []),
    ...(target.nf_ids ?? []),
  ].filter(Boolean)
  return !supported.length || supported.includes(componentId)
}

function makeTaskName(prefix: string) {
  const now = new Date()
  const part = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((value) => String(value).padStart(2, '0'))
    .join('')
  return `${prefix}-${part}`
}

function formatDuration(seconds: number) {
  return seconds < 60 ? `${seconds} segundos` : `${seconds / 60} min`
}
