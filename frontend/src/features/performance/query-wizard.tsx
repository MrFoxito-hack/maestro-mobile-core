import { useState, useEffect } from 'react'
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ChevronRight,
  Clock3,
  Database,
  Network,
  Search,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { ScrollArea } from '@/components/ui/scroll-area'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  supportsObject,
  measurementDomain,
  selectMeasurementObject,
  isOperationalCounter,
} from './types'
import type {
  Aggregation,
  KpiCounter,
  KpiObject,
  KpiQueryDraft,
  RangeKey,
} from './types'

const STEPS = [
  { title: 'Objeto', icon: Network },
  { title: 'Contador', icon: Database },
  { title: 'Tiempo', icon: Clock3 },
]

function toggle(values: string[], value: string) {
  return values.includes(value)
    ? values.filter((item) => item !== value)
    : [...values, value]
}

export function QueryWizard({
  onOpenChange,
  objects,
  counters,
  initial,
  onApply,
}: {
  onOpenChange: (open: boolean) => void
  objects: KpiObject[]
  counters: KpiCounter[]
  initial: KpiQueryDraft
  onApply: (draft: KpiQueryDraft) => void
}) {
  const [step, setStep] = useState(0)
  const [draft, setDraft] = useState({
    ...initial,
    object_ids: [] as string[],
    counter_ids: [] as string[],
  })
  const [domain, setDomain] = useState('testbed')
  const [counterKind, setCounterKind] = useState('service')
  const [search, setSearch] = useState('')

  const domainOptions = [
    ...new Map(
      objects.map((item) => [
        measurementDomain(item.id),
        {
          id: measurementDomain(item.id),
          label:
            item.type === 'nf'
              ? item.label
              : item.type === 'testbed'
                ? 'Testbed / VM'
                : item.type === 'interface'
                  ? 'Interfaces de red'
                  : item.label,
        },
      ])
    ).values(),
  ]
  const isNf = draft.object_ids.some((id) => id.startsWith('nf:'))
  const requires30m = draft.counter_ids.some((id) => {
    const counter = counters.find((c) => c.id === id)
    return counter?.min_granularity_seconds && counter.min_granularity_seconds > 300
  })

  useEffect(() => {
    if (requires30m && draft.granularity_seconds < 1800) {
      setDraft((d) => ({ ...d, granularity_seconds: 1800 }))
    }
  }, [requires30m, draft.granularity_seconds])
  const compatibleCounters = counters.filter(
    (counter) =>
      draft.object_ids.length > 0 &&
      draft.object_ids.every((id) => supportsObject(counter, id)) &&
      (!isNf ||
        (counterKind === 'operations'
          ? isOperationalCounter(counter)
          : !isOperationalCounter(counter)))
  )
  const visibleObjects = objects.filter(
    (item) =>
      measurementDomain(item.id) === domain &&
      `${item.label} ${item.group}`.toLowerCase().includes(search.toLowerCase())
  )
  const visibleCounters = compatibleCounters.filter((item) =>
    `${item.label} ${item.category}`
      .toLowerCase()
      .includes(search.toLowerCase())
  )
  const groups = [...new Set(visibleObjects.map((item) => item.group))]
  const categories = [
    ...new Set(compatibleCounters.map((item) => item.category)),
  ]

  const valid =
    step === 0
      ? draft.object_ids.length > 0
      : step === 1
        ? draft.counter_ids.length > 0
        : true

  return (
    <section className='flex min-h-[calc(100svh-12rem)] flex-col overflow-hidden rounded-lg border bg-card'>
      <div className='grid grid-cols-3 border-b bg-muted/25 px-8 py-4'>
        {STEPS.map((item, index) => {
          const Icon = item.icon
          return (
            <div key={item.title} className='flex items-center'>
              <button
                type='button'
                onClick={() => index <= step && setStep(index)}
                className='flex items-center gap-3 text-left'
              >
                <span
                  className={cn(
                    'flex size-8 items-center justify-center rounded-full border text-sm font-semibold',
                    index === step &&
                      'border-primary bg-primary text-primary-foreground',
                    index < step && 'border-primary text-primary'
                  )}
                >
                  {index < step ? <Check className='size-4' /> : index + 1}
                </span>
                <span>
                  <span className='block text-xs text-muted-foreground'>
                    Paso {index + 1}
                  </span>
                  <span className='flex items-center gap-1 font-medium'>
                    <Icon className='size-4' /> {item.title}
                  </span>
                </span>
              </button>
              {index < STEPS.length - 1 && (
                <div
                  className={cn(
                    'mx-5 h-px flex-1 bg-border',
                    index < step && 'bg-primary'
                  )}
                />
              )}
            </div>
          )
        })}
      </div>

      <div className='min-h-0 flex-1 p-4 md:p-6'>
        {step === 0 && (
          <div className='mb-4 max-w-sm space-y-2'>
            <Label htmlFor='measurement-domain'>Tipo de elemento</Label>
            <Select
              value={domain}
              onValueChange={(value) => {
                setDomain(value)
                setDraft({ ...draft, object_ids: [], counter_ids: [] })
                setSearch('')
                setCounterKind('service')
              }}
            >
              <SelectTrigger id='measurement-domain' className='w-full'>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {domainOptions.map((item) => (
                  <SelectItem key={item.id} value={item.id}>
                    {item.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        )}
        {step === 1 && isNf && (
          <div className='mb-4 flex gap-2'>
            <Button
              variant={counterKind === 'service' ? 'secondary' : 'ghost'}
              onClick={() => setCounterKind('service')}
            >
              Servicio
            </Button>
            <Button
              variant={counterKind === 'operations' ? 'secondary' : 'ghost'}
              onClick={() => setCounterKind('operations')}
            >
              Operación y recursos
            </Button>
          </div>
        )}
        {step < 2 && (
          <div className='relative mb-4 max-w-sm'>
            <Search className='absolute top-2.5 left-3 size-4 text-muted-foreground' />
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder={
                step === 0 ? 'Buscar objetos…' : 'Buscar contadores…'
              }
              className='pl-9'
            />
          </div>
        )}

        {step === 0 && (
          <div className='grid gap-5 lg:grid-cols-[1fr_64px_1fr]'>
            <SelectionPanel title='Objetos disponibles'>
              {groups.map((group) => (
                <div key={group} className='mb-5'>
                  <p className='mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase'>
                    {group}
                  </p>
                  {visibleObjects
                    .filter((item) => item.group === group)
                    .map((item) => (
                      <label
                        key={item.id}
                        className='flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 hover:bg-muted'
                      >
                        <Checkbox
                          checked={draft.object_ids.includes(item.id)}
                          onCheckedChange={() =>
                            setDraft({
                              ...draft,
                              object_ids: selectMeasurementObject(
                                draft.object_ids,
                                item.id
                              ),
                              counter_ids: [],
                            })
                          }
                        />
                        <span className='min-w-0 flex-1 truncate text-sm'>
                          {item.label}
                        </span>
                        <Badge variant='outline' className='text-[10px]'>
                          {item.type.toUpperCase()}
                        </Badge>
                      </label>
                    ))}
                </div>
              ))}
            </SelectionPanel>
            <TransferRail />
            <SelectionPanel
              title={`Objetos seleccionados (${draft.object_ids.length})`}
            >
              {objects
                .filter((item) => draft.object_ids.includes(item.id))
                .map((item) => (
                  <button
                    key={item.id}
                    type='button'
                    onClick={() =>
                      setDraft({
                        ...draft,
                        object_ids: selectMeasurementObject(
                          draft.object_ids,
                          item.id
                        ),
                        counter_ids: [],
                      })
                    }
                    className='flex w-full items-center gap-3 rounded-md border px-3 py-2 text-left hover:bg-muted'
                  >
                    <Network className='size-4 text-primary' />
                    <span className='flex-1 text-sm'>{item.label}</span>
                    <span className='text-xs text-muted-foreground'>
                      Quitar
                    </span>
                  </button>
                ))}
            </SelectionPanel>
          </div>
        )}

        {step === 1 && (
          <div className='grid gap-5 lg:grid-cols-[1fr_64px_1fr]'>
            <SelectionPanel title='Contadores disponibles'>
              {!visibleCounters.length && (
                <p className='text-sm text-muted-foreground'>
                  {search
                    ? 'Sin coincidencias.'
                    : isNf && counterKind === 'service'
                      ? 'Esta NF no tiene contadores de servicio disponibles en el catálogo actual. Consulta Operación y recursos.'
                      : 'No hay contadores disponibles para esta selección.'}
                </p>
              )}
              {categories.map((category) => (
                <div key={category} className='mb-5'>
                  <p className='mb-2 text-xs font-semibold tracking-wide text-muted-foreground uppercase'>
                    {category}
                  </p>
                  {visibleCounters
                    .filter((item) => item.category === category)
                    .map((item) => (
                      <label
                        key={item.id}
                        className='flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 hover:bg-muted'
                      >
                        <Checkbox
                          checked={draft.counter_ids.includes(item.id)}
                          disabled={
                            !draft.counter_ids.includes(item.id) &&
                            draft.counter_ids.length >= 12
                          }
                          onCheckedChange={() =>
                            setDraft({
                              ...draft,
                              counter_ids: toggle(draft.counter_ids, item.id),
                            })
                          }
                        />
                        <span className='min-w-0 flex-1 text-sm'>
                          {item.label}
                        </span>
                        <Badge variant='secondary'>{item.unit}</Badge>
                      </label>
                    ))}
                </div>
              ))}
            </SelectionPanel>
            <TransferRail />
            <SelectionPanel
              title={`Contadores seleccionados (${draft.counter_ids.length})`}
            >
              {counters
                .filter((item) => draft.counter_ids.includes(item.id))
                .map((item) => (
                  <button
                    key={item.id}
                    type='button'
                    onClick={() =>
                      setDraft({
                        ...draft,
                        counter_ids: toggle(draft.counter_ids, item.id),
                      })
                    }
                    className='flex w-full items-center gap-3 rounded-md border px-3 py-2 text-left hover:bg-muted'
                  >
                    <Database className='size-4 text-primary' />
                    <span className='flex-1 text-sm'>{item.label}</span>
                    <Badge variant='secondary'>{item.unit}</Badge>
                  </button>
                ))}
            </SelectionPanel>
          </div>
        )}

        {step === 2 && (
          <div className='grid gap-6 md:grid-cols-2'>
            <section className='rounded-lg border p-5'>
              <h3 className='font-semibold'>Ventana de consulta</h3>
              <p className='mb-5 text-sm text-muted-foreground'>
                Periodo histórico que se mostrará en la gráfica.
              </p>
              <div className='grid grid-cols-2 gap-3 sm:grid-cols-3'>
                {(['15m', '1h', '6h', '24h', '7d'] as RangeKey[]).map(
                  (range) => (
                    <Button
                      key={range}
                      type='button'
                      variant={
                        draft.range_key === range ? 'default' : 'outline'
                      }
                      onClick={() => setDraft({ ...draft, range_key: range })}
                    >
                      {range}
                    </Button>
                  )
                )}
              </div>
            </section>
            <section className='rounded-lg border p-5'>
              <h3 className='font-semibold'>Tratamiento de muestras</h3>
              <p className='mb-5 text-sm text-muted-foreground'>
                Resolución y operación aplicada por cada intervalo.
              </p>
              <div className='space-y-4'>
                <div className='space-y-2'>
                  <Label>Granularidad</Label>
                  <Select
                    value={String(draft.granularity_seconds)}
                    onValueChange={(value) =>
                      setDraft({
                        ...draft,
                        granularity_seconds: Number(value),
                      })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value='300' disabled={requires30m}>
                        {requires30m ? '5 minutos (Bloqueado: requiere 30 min)' : '5 minutos'}
                      </SelectItem>
                      <SelectItem value='1800'>30 minutos</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className='space-y-2'>
                  <Label>Agregación</Label>
                  <Select
                    value={draft.aggregation}
                    onValueChange={(value) =>
                      setDraft({
                        ...draft,
                        aggregation: value as Aggregation,
                      })
                    }
                  >
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value='avg'>Promedio</SelectItem>
                      <SelectItem value='min'>Mínimo</SelectItem>
                      <SelectItem value='max'>Máximo</SelectItem>
                      <SelectItem value='sum'>Suma</SelectItem>
                      <SelectItem value='last'>Último valor</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </div>
            </section>
            <section className='rounded-lg border bg-muted/25 p-5 md:col-span-2'>
              <h3 className='font-semibold'>Resumen de consulta</h3>
              <div className='mt-3 flex flex-wrap gap-2'>
                <Badge>{draft.object_ids.length} objetos</Badge>
                <Badge>{draft.counter_ids.length} contadores</Badge>
                <Badge variant='outline'>Últimas {draft.range_key}</Badge>
                <Badge variant='outline'>
                  Cada {draft.granularity_seconds}s
                </Badge>
                <Badge variant='outline'>
                  {draft.aggregation.toUpperCase()}
                </Badge>
              </div>
            </section>
          </div>
        )}
      </div>

      <div className='flex flex-wrap justify-end gap-2 border-t px-6 py-4'>
        <Button variant='outline' onClick={() => onOpenChange(false)}>
          Cancelar
        </Button>
        {step > 0 && (
          <Button
            variant='outline'
            onClick={() => {
              setStep(step - 1)
              setSearch('')
            }}
          >
            <ArrowLeft /> Anterior
          </Button>
        )}
        {step < 2 ? (
          <Button
            disabled={!valid}
            onClick={() => {
              if (step === 1 && requires30m && draft.granularity_seconds < 1800) {
                setDraft((prev) => ({ ...prev, granularity_seconds: 1800 }))
              }
              setStep(step + 1)
              setSearch('')
            }}
          >
            Siguiente <ArrowRight />
          </Button>
        ) : (
          <Button
            onClick={() => {
              onApply(draft)
              onOpenChange(false)
            }}
          >
            <Check /> Ejecutar consulta
          </Button>
        )}
      </div>
    </section>
  )
}

function SelectionPanel({
  title,
  children,
}: {
  title: string
  children: React.ReactNode
}) {
  return (
    <section className='overflow-hidden rounded-lg border'>
      <div className='border-b bg-muted/30 px-4 py-3 font-medium'>{title}</div>
      <ScrollArea className='h-[min(48vh,520px)]'>
        <div className='space-y-2 p-4'>{children}</div>
      </ScrollArea>
    </section>
  )
}

function TransferRail() {
  return (
    <div className='hidden items-center justify-center lg:flex'>
      <div className='flex size-10 items-center justify-center rounded-full border bg-muted/30'>
        <ChevronRight className='size-5 text-muted-foreground' />
      </div>
    </div>
  )
}
