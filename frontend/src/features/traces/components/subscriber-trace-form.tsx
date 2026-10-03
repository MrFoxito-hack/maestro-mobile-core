import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Check, ChevronsUpDown, Loader2, Play } from 'lucide-react'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import {
  optionFrom,
  type SubscriberTraceDraft,
  type TraceCapabilities,
  type TraceOption,
} from '../types'
import { TraceFormHelp } from './trace-form-help'

type SubscriberListItem = {
  imsi: string
}

type SubscriberTraceFormProps = {
  capabilities?: TraceCapabilities
  isLoading: boolean
  canCreate: boolean
  isSubmitting: boolean
  onSubmit: (draft: SubscriberTraceDraft) => void
}

const FALLBACK_PROCEDURES: TraceOption[] = [
  { id: 'registration', label: 'Registration' },
  { id: 'authentication', label: '5G-AKA Authentication' },
  { id: 'pdu-session', label: 'PDU Session Establishment' },
]

export function SubscriberTraceForm({
  capabilities,
  isLoading,
  canCreate,
  isSubmitting,
  onSubmit,
}: SubscriberTraceFormProps) {
  const subscriberCapabilities = capabilities?.subscriber
  const [name, setName] = useState(() => makeTaskName())
  const [identifierType, setIdentifierType] = useState<
    'imsi' | 'supi' | 'ue-ip'
  >('imsi')
  const [identifier, setIdentifier] = useState('')
  const [subscriberOpen, setSubscriberOpen] = useState(false)
  const [selectedProcedures, setSelectedProcedures] = useState<string[]>([])
  const [proceduresTouched, setProceduresTouched] = useState(false)
  const [includeUserPlane, setIncludeUserPlane] = useState(true)
  const [includeSbi, setIncludeSbi] = useState(true)
  const [duration, setDuration] = useState('60')
  const [maxMegabytes, setMaxMegabytes] = useState('50')

  const subscribers = useQuery({
    queryKey: ['subscribers', 'trace-selector'],
    queryFn: async () =>
      (await api.get<SubscriberListItem[]>('/subscribers')).data,
    enabled: identifierType === 'imsi' || identifierType === 'supi',
  })

  const procedureOptions = (
    subscriberCapabilities?.procedures?.map(optionFrom) ?? FALLBACK_PROCEDURES
  ).filter((item) => item.id !== 'user-plane')
  const defaults =
    subscriberCapabilities?.default_procedures ??
    procedureOptions
      .filter((item) => item.id !== 'user-plane')
      .map((item) => item.id)
  const procedures = proceduresTouched ? selectedProcedures : defaults
  const agent = capabilities?.capture_agents?.[0]
  const identifierTypes = subscriberCapabilities?.identifier_types?.map(
    optionFrom
  ) ?? [
    { id: 'imsi', label: 'IMSI' },
    { id: 'supi', label: 'SUPI' },
    { id: 'ue-ip', label: 'Dirección IP del UE' },
  ]
  const identifierValid = validateIdentifier(identifierType, identifier)
  const maxDuration =
    capabilities?.quota?.max_duration_seconds ??
    capabilities?.limits?.max_duration_seconds ??
    900
  const maxSize =
    capabilities?.quota?.max_megabytes ??
    capabilities?.limits?.max_megabytes ??
    500
  const durationOptions = [60, 120, 300, 600, 900].filter((value) => value <= maxDuration)
  const sizeOptions = [25, 50, 100, 250, 500].filter((value) => value <= maxSize)

  const toggleProcedure = (id: string, checked: boolean) => {
    setProceduresTouched(true)
    setSelectedProcedures((current) => {
      const base = proceduresTouched ? current : defaults
      return checked
        ? [...new Set([...base, id])]
        : base.filter((item) => item !== id)
    })
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!name.trim() || !identifierValid || !procedures.length) return
    onSubmit({
      name: name.trim(),
      scenario_id: '5g-sa',
      testbed_id: capabilities?.testbed_id ?? 'local',
      capture_agent_id: agent?.id ?? 'primary',
      identifier_type: identifierType,
      identifier: identifier.trim(),
      procedures,
      include_user_plane: includeUserPlane,
      include_sbi: includeSbi,
      auto_trigger: false,
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
        <h2 className='text-sm font-semibold'>Nueva captura de suscriptor</h2>
        <TraceFormHelp mode='subscriber' />
      </div>
      <fieldset disabled={isSubmitting} className='min-w-0 divide-y'>
        <section className='px-5 py-5'>
          <div className='grid gap-x-6 gap-y-4 sm:grid-cols-2'>
            <div className='space-y-2 sm:col-span-2'>
              <Label htmlFor='subscriber-task-name'>Nombre de la tarea</Label>
              <Input
                id='subscriber-task-name'
                value={name}
                maxLength={80}
                required
                onChange={(event) => setName(event.target.value)}
                placeholder='Ej. UE01-Registration-PDU'
              />
            </div>
            <div className='space-y-2'>
              <Label htmlFor='subscriber-id-type'>Tipo de identificador</Label>
              <Select
                value={identifierType}
                onValueChange={(value) => {
                  setIdentifierType(value as 'imsi' | 'supi' | 'ue-ip')
                  setIdentifier('')
                }}
              >
                <SelectTrigger className='w-full' id='subscriber-id-type'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {identifierTypes
                    .filter((item) =>
                      ['imsi', 'supi', 'ue-ip'].includes(item.id)
                    )
                    .map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.label}
                      </SelectItem>
                    ))}
                </SelectContent>
              </Select>
            </div>
            <div className='space-y-2'>
              <Label htmlFor='subscriber-identifier'>
                {identifierType === 'ue-ip'
                  ? 'IP asignada al UE'
                  : 'Suscriptor'}
              </Label>
              {identifierType === 'ue-ip' ? (
                <Input
                  id='subscriber-identifier'
                  value={identifier}
                  required
                  onChange={(event) => setIdentifier(event.target.value)}
                  placeholder='10.45.0.2'
                  aria-invalid={identifier.length > 0 && !identifierValid}
                />
              ) : (
                <Popover open={subscriberOpen} onOpenChange={setSubscriberOpen}>
                  <PopoverTrigger asChild>
                    <Button
                      id='subscriber-identifier'
                      type='button'
                      variant='outline'
                      role='combobox'
                      aria-expanded={subscriberOpen}
                      className='w-full justify-between font-mono font-normal'
                    >
                      {identifier ||
                        (subscribers.isLoading
                          ? 'Consultando suscriptores…'
                          : 'Seleccione o busque un IMSI')}
                      <ChevronsUpDown className='opacity-50' />
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent
                    className='w-[var(--radix-popover-trigger-width)] p-0'
                    align='start'
                  >
                    <Command>
                      <CommandInput placeholder='Buscar IMSI…' />
                      <CommandList>
                        <CommandEmpty>
                          No se encontró el suscriptor.
                        </CommandEmpty>
                        <CommandGroup heading='Open5GS · perfiles disponibles'>
                          {subscribers.data?.map((subscriber) => (
                            <CommandItem
                              key={subscriber.imsi}
                              value={subscriber.imsi}
                              onSelect={(value) => {
                                setIdentifier(value)
                                setSubscriberOpen(false)
                              }}
                            >
                              <Check
                                className={cn(
                                  'size-4',
                                  identifier === subscriber.imsi
                                    ? 'opacity-100'
                                    : 'opacity-0'
                                )}
                              />
                              <span className='font-mono'>
                                {subscriber.imsi}
                              </span>
                            </CommandItem>
                          ))}
                        </CommandGroup>
                      </CommandList>
                    </Command>
                  </PopoverContent>
                </Popover>
              )}
              {identifier.length > 0 && !identifierValid && (
                <p className='text-xs text-destructive'>
                  El identificador no tiene un formato válido.
                </p>
              )}
            </div>
          </div>
        </section>

        <section className='px-5 py-5'>
          <div className='space-y-4'>
            <fieldset className='flex flex-wrap gap-x-6 gap-y-3'>
              <legend className='mb-3 text-xs font-medium text-muted-foreground'>
                Procedimientos
              </legend>
              {procedureOptions.map((procedure) => (
                <label
                  key={procedure.id}
                  className='flex cursor-pointer items-center gap-2'
                >
                  <Checkbox
                    className='mt-0.5'
                    checked={procedures.includes(procedure.id)}
                    onCheckedChange={(checked) =>
                      toggleProcedure(procedure.id, checked === true)
                    }
                  />
                  <span>
                    <span className='block text-sm font-medium'>
                      {procedure.label}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>
            {!procedures.length && (
              <p className='text-xs text-destructive'>
                Seleccione al menos un procedimiento.
              </p>
            )}

            <div className='grid gap-3 sm:grid-cols-2'>
              <ToggleOption
                label='Plano de usuario (GTP-U / N3 / N6)'
                checked={includeUserPlane}
                disabled={subscriberCapabilities?.supports_user_plane === false}
                onCheckedChange={setIncludeUserPlane}
              />
              <ToggleOption
                label='Mensajes SBI (HTTP/2 REST)'
                checked={includeSbi}
                disabled={subscriberCapabilities?.supports_sbi === false}
                onCheckedChange={setIncludeSbi}
              />
            </div>
          </div>
        </section>

        <section className='px-5 py-5'>
          <div className='grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3'>
            <div className='space-y-2'>
              <Label htmlFor='subscriber-duration'>Duración</Label>
              <Select value={duration} onValueChange={setDuration}>
                <SelectTrigger className='w-full' id='subscriber-duration'>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {durationOptions.map((value) => (
                    <SelectItem key={value} value={String(value)}>
                      {value < 60 ? `${value} s` : `${value / 60} min`}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className='space-y-2'>
              <Label htmlFor='subscriber-max-size'>Tamaño máximo</Label>
              <Select value={maxMegabytes} onValueChange={setMaxMegabytes}>
                <SelectTrigger className='w-full' id='subscriber-max-size'>
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
            </div>
          </div>
        </section>
      </fieldset>

      <div className='flex flex-wrap items-center justify-end gap-3 border-t bg-muted/20 px-5 py-3'>
        <p className='mr-auto text-xs text-muted-foreground'>
          Captura pasiva de señalización en tiempo real.
        </p>
        {!canCreate && (
          <p className='mr-auto text-xs text-muted-foreground'>
            Sin permiso para iniciar capturas.
          </p>
        )}
        <Button
          className='min-w-36'
          type='submit'
          disabled={
            !canCreate ||
            !name.trim() ||
            !identifierValid ||
            !procedures.length ||
            isSubmitting
          }
        >
          {isSubmitting ? <Loader2 className='animate-spin' /> : <Play />}
          Iniciar captura
        </Button>
      </div>
    </form>
  )
}

function ToggleOption({
  label,
  checked,
  disabled,
  onCheckedChange,
}: {
  label: string
  checked: boolean
  disabled?: boolean
  onCheckedChange: (value: boolean) => void
}) {
  return (
    <label className='flex items-center justify-between gap-3 rounded-md bg-muted/30 px-3 py-2.5'>
      <span className='text-sm'>{label}</span>
      <Switch
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onCheckedChange={onCheckedChange}
      />
    </label>
  )
}

function validateIdentifier(type: 'imsi' | 'supi' | 'ue-ip', value: string) {
  const clean = value.trim()
  if (type === 'imsi') return /^\d{14,15}$/.test(clean)
  if (type === 'supi') return /^(?:imsi-)?\d{14,15}$/i.test(clean)
  const octets = clean.split('.')
  return (
    octets.length === 4 &&
    octets.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255)
  )
}

function makeTaskName() {
  const now = new Date()
  const part = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((value) => String(value).padStart(2, '0'))
    .join('')
  return `UE-TRACE-${part}`
}

function FormSkeleton() {
  return <div className='h-80 animate-pulse rounded-lg border bg-muted/40' />
}
