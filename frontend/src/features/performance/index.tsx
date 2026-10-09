import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown,
  ChevronRight,
  Folder,
  LineChart,
  BarChart3,
  Table2,
  Plus,
  RefreshCw,
  Save,
  Search,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'
import { useScenarioStore } from '@/stores/scenario-store'
import { api, apiErrorMessage } from '@/lib/api'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { EmsPage } from '@/features/ems-page'
import { PerformanceChart, type ChartMode } from './performance-chart'
import { ReportDialog } from './report-dialog'
import { exportPerformance } from './export'
import { UpfXdpPanel } from './upf-xdp-panel'
import {
  NF_GROUPS,
  PERFORMANCE_TEMPLATES,
  getObjectPresentation,
  type PerformanceTemplate,
} from './templates'
import {
  supportsObject,
  type Aggregation,
  type KpiCounter,
  type KpiQueryDraft,
  type KpiQueryResult,
  type PerformanceCatalog,
  type RangeKey,
  type SavedKpiQuery,
} from './types'

function matches(template: PerformanceTemplate, counter: KpiCounter) {
  const native = counter.native_name?.replace(/^fivegs_[a-z]+function_/, '')
  return (
    template.matchCounters(counter) ||
    Boolean(native && template.matchCounters({ ...counter, id: native })) ||
    (template.id.endsWith('-health') &&
      counter.id.startsWith('nf.process.') &&
      (counter.object_ids ?? []).some((id) =>
        template.defaultObjectIds.includes(id)
      ))
  )
}

export function PerformancePage() {
  const scenario = useScenarioStore((s) => s.scenario)
  return <PerformanceWorkspace key={scenario} scenario={scenario} />
}

function PerformanceWorkspace({ scenario }: { scenario: '5g-sa' | '4g-epc' }) {
  const client = useQueryClient()
  const templates = PERFORMANCE_TEMPLATES.filter(
    (t) => t.scenario === 'both' || t.scenario === scenario
  )
  const [templateId, setTemplateId] = useState(
    scenario === '5g-sa' ? 'upf-throughput' : 'mme-attach'
  )
  const template = templates.find((t) => t.id === templateId) ?? templates[0]
  const [selection, setSelection] = useState<{
    objects: string[]
    counters: string[]
  } | null>(null)
  const [savedName, setSavedName] = useState('')
  const [range, setRange] = useState<RangeKey>('1h')
  const [aggregation, setAggregation] = useState<Aggregation>('avg')
  const [granularity, setGranularity] = useState(300)
  const [mode, setMode] = useState<ChartMode>('line')
  const [treeSearch, setTreeSearch] = useState('')
  const [objectSearch, setObjectSearch] = useState('')
  const [counterSearch, setCounterSearch] = useState('')
  const [closed, setClosed] = useState<string[]>([])
  const [saveOpen, setSaveOpen] = useState(false)
  const [name, setName] = useState('')

  const catalog = useQuery({
    queryKey: ['performance-catalog', scenario],
    queryFn: async () =>
      (await api.get<PerformanceCatalog>(`/performance/catalog/${scenario}`))
        .data,
    refetchInterval: 15_000,
  })

  const saved = useQuery({
    queryKey: ['performance-saved-queries'],
    queryFn: async () =>
      (await api.get<SavedKpiQuery[]>('/performance/saved-queries')).data,
  })

  // Extract objects matching current template
  const objects = (() => {
    return [
      ...new Map((catalog.data?.objects ?? []).map((o) => [o.id, o])).values(),
    ].filter((o) => template.matchObjects(o))
  })()

  // Extract counters compatible with template and available objects
  const counters = (() => {
    return (catalog.data?.counters ?? []).filter(
      (c) =>
        matches(template, c) && objects.some((o) => supportsObject(c, o.id))
    )
  })()

  const defaultObjects = objects.filter((o) =>
    template.defaultObjectIds.includes(o.id)
  )

  const objectIds = (() => {
    if (selection?.objects !== undefined) {
      return selection.objects.filter((id) => objects.some((o) => o.id === id))
    }
    return (defaultObjects.length ? defaultObjects : objects.slice(0, 4)).map(
      (o) => o.id
    )
  })()

  const compatibleCounters = (() => {
    return counters.filter((c) => objectIds.some((id) => supportsObject(c, id)))
  })()

  const defaultCounters = (() => {
    return compatibleCounters.filter(
      (c) =>
        template.defaultCounterIds.includes(c.id) ||
        template.defaultCounterIds.includes(c.native_name ?? '')
    )
  })()

  const counterIds = (() => {
    if (selection?.counters !== undefined) {
      return selection.counters
        .filter((id) => compatibleCounters.some((c) => c.id === id))
        .slice(0, 1)
    }
    return (
      defaultCounters.length ? defaultCounters : compatibleCounters.slice(0, 2)
    )
      .slice(0, 1)
      .map((c) => c.id)
  })()

  const requires30m = counterIds.some((id) => {
    const counter = catalog.data?.counters.find((c) => c.id === id)
    return counter?.min_granularity_seconds && counter.min_granularity_seconds > 300
  })

  useEffect(() => {
    if (requires30m && granularity < 1800) {
      setGranularity(1800)
    }
  }, [requires30m, granularity])

  const draft: KpiQueryDraft = {
    scenario_id: scenario,
    object_ids: objectIds,
    counter_ids: counterIds,
    range_key: range,
    aggregation,
    granularity_seconds: granularity,
  }

  const result = useQuery({
    queryKey: ['performance-result', draft],
    queryFn: async () =>
      (await api.post<KpiQueryResult>('/performance/query', draft)).data,
    enabled: catalog.isSuccess && objectIds.length > 0 && counterIds.length > 0,
    refetchInterval: 10_000,
  })

  const applyTemplate = (t: PerformanceTemplate) => {
    setTemplateId(t.id)
    setSelection(null)
    setSavedName('')
    setObjectSearch('')
    setCounterSearch('')
    setAggregation(t.defaultAggregation ?? 'avg')
    setRange(t.defaultRangeKey ?? '1h')
    setGranularity(
      t.id === 'upf-slice-performance' || t.id === 'upf-xdp-acceleration'
        ? 5
        : 300
    )
  }

  const toggleObject = (id: string) => {
    const next = objectIds.includes(id)
      ? objectIds.filter((v) => v !== id)
      : [...objectIds, id]
    setSelection({
      objects: next,
      counters: counterIds.filter((key) =>
        counters.some(
          (c) => c.id === key && next.some((o) => supportsObject(c, o))
        )
      ),
    })
  }

  const toggleAllObjects = () => {
    if (objectIds.length === objects.length) {
      setSelection({ objects: [], counters: [] })
    } else {
      setSelection({
        objects: objects.map((o) => o.id),
        counters: counterIds,
      })
    }
  }

  const toggleCounter = (id: string) => {
    setSelection({
      objects: objectIds,
      counters: [id],
    })
  }

  const clearCounters = () => {
    setSelection({
      objects: objectIds,
      counters: [],
    })
  }

  const save = useMutation({
    mutationFn: async () =>
      api.post('/performance/saved-queries', {
        ...draft,
        name: name.trim(),
        folder_id: null,
        scope: 'personal',
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['performance-saved-queries'] })
      setSaveOpen(false)
      toast.success('Consulta guardada exitosamente')
    },
    onError: (e) =>
      toast.error(apiErrorMessage(e, 'No se pudo guardar la consulta')),
  })

  const remove = useMutation({
    mutationFn: async (id: string) =>
      api.delete(`/performance/saved-queries/${id}`),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['performance-saved-queries'] })
      toast.success('Consulta eliminada')
    },
    onError: (e) => toast.error(apiErrorMessage(e, 'No se pudo eliminar')),
  })

  const load = (query: SavedKpiQuery) => {
    const match = templates.find(
      (t) =>
        query.object_ids.every((id) =>
          catalog.data?.objects.some((o) => o.id === id && t.matchObjects(o))
        ) &&
        query.counter_ids.every((id) =>
          catalog.data?.counters.some((c) => c.id === id && matches(t, c))
        )
    )
    if (!match) {
      toast.info('Esta consulta no corresponde a una plantilla disponible')
      return
    }
    applyTemplate(match)
    setSavedName(query.name)
    setSelection({
      objects: query.object_ids,
      counters: query.counter_ids.slice(0, 1),
    })
    setRange(query.range_key)
    setAggregation(query.aggregation)
    setGranularity(query.granularity_seconds)
  }

  const groups = [...new Set(templates.map((t) => t.nf))]

  const displayedResult = useMemo(() => {
    if (!result.data) return undefined
    return {
      ...result.data,
      series: result.data.series.map((series) => {
        const counter = catalog.data?.counters.find(
          (c) => c.id === series.counter_id
        )
        const objPres = getObjectPresentation(
          series.object_id,
          catalog.data?.objects
        )
        return {
          ...series,
          label: `${objPres.label} · ${counter?.label ?? series.label}`,
        }
      }),
    }
  }, [result.data, catalog.data])

  const visibleObjects = (() => {
    return objects.filter((o) => {
      const pres = getObjectPresentation(o.id, catalog.data?.objects)
      const q = objectSearch.toLowerCase()
      return (
        o.label.toLowerCase().includes(q) ||
        pres.label.toLowerCase().includes(q) ||
        (pres.badge && pres.badge.toLowerCase().includes(q))
      )
    })
  })()

  const visibleCounters = (() => {
    return compatibleCounters.filter((c) => {
      const q = counterSearch.toLowerCase()
      return (
        c.label.toLowerCase().includes(q) ||
        (c.native_name && c.native_name.toLowerCase().includes(q)) ||
        c.id.toLowerCase().includes(q)
      )
    })
  })()

  const title = savedName || template.title

  return (
    <EmsPage title='Performance'>
      <div className='grid min-h-[600px] grid-cols-1 overflow-hidden rounded-lg border bg-card lg:h-[calc(100dvh-88px)] lg:grid-cols-[210px_minmax(0,1fr)_300px] 2xl:grid-cols-[230px_minmax(0,1fr)_340px]'>
        <aside className='flex min-h-0 flex-col border-b lg:border-r lg:border-b-0'>
          <div className='border-b p-3'>
            <SearchField
              label='Buscar plantilla'
              value={treeSearch}
              onChange={setTreeSearch}
            />
          </div>
          <div className='min-h-0 flex-1 overflow-auto p-2'>
            {groups.map((group) => {
              const children = templates.filter(
                (t) =>
                  t.nf === group &&
                  `${t.title} ${t.nfName}`
                    .toLowerCase()
                    .includes(treeSearch.toLowerCase())
              )
              if (!children.length) return null
              const expanded = !closed.includes(group) || Boolean(treeSearch)
              return (
                <div key={group} className='mb-2'>
                  <button
                    className='flex w-full items-center gap-1.5 rounded px-2 py-2 text-left text-xs font-medium hover:bg-muted'
                    aria-expanded={expanded}
                    onClick={() =>
                      setClosed((v) =>
                        v.includes(group)
                          ? v.filter((g) => g !== group)
                          : [...v, group]
                      )
                    }
                  >
                    {expanded ? (
                      <ChevronDown className='size-3' />
                    ) : (
                      <ChevronRight className='size-3' />
                    )}
                    <Folder className='size-3.5 text-muted-foreground' />
                    {NF_GROUPS[group]?.code ?? group.toUpperCase()}
                  </button>
                  {expanded && (
                    <div className='ml-4 border-l pl-2'>
                      {children.map((t) => (
                        <button
                          key={t.id}
                          title={t.subtitle}
                          onClick={() => applyTemplate(t)}
                          className={`mb-0.5 block w-full rounded px-2 py-2 text-left text-xs ${template.id === t.id && !savedName ? 'bg-accent font-medium text-accent-foreground' : 'text-muted-foreground hover:bg-muted hover:text-foreground'}`}
                        >
                          {t.title}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )
            })}
            <div className='mt-4 border-t pt-3'>
              <div className='px-2 pb-2 text-xs font-medium'>Mis consultas</div>
              {(saved.data ?? [])
                .filter(
                  (q) =>
                    q.scenario_id === scenario &&
                    q.name.toLowerCase().includes(treeSearch.toLowerCase())
                )
                .map((q) => (
                  <div
                    key={q.id}
                    className='group flex items-center rounded hover:bg-muted'
                  >
                    <button
                      className='min-w-0 flex-1 truncate px-2 py-2 text-left text-xs'
                      onClick={() => load(q)}
                    >
                      {q.name}
                    </button>
                    <Button
                      size='icon'
                      variant='ghost'
                      className='size-7'
                      aria-label={`Eliminar ${q.name}`}
                      disabled={remove.isPending}
                      onClick={() => remove.mutate(q.id)}
                    >
                      <Trash2 className='size-3' />
                    </Button>
                  </div>
                ))}
              {!saved.data?.some((q) => q.scenario_id === scenario) && (
                <p className='px-2 text-xs text-muted-foreground'>
                  Sin consultas guardadas
                </p>
              )}
            </div>
          </div>
        </aside>
        <main className='flex min-h-[500px] min-w-0 flex-col'>
          <div className='flex min-h-11 items-center gap-2 border-b px-3'>
            <span className='truncate text-sm font-medium' title={title}>
              {title}
            </span>
            <div className='ml-auto flex shrink-0 items-center gap-1'>
              <Button
                size='sm'
                variant='ghost'
                className='h-8 text-xs'
                onClick={() => {
                  setSelection({ objects: [], counters: [] })
                  setSavedName('Nueva consulta')
                }}
              >
                <Plus className='size-3.5' />
                Nueva consulta
              </Button>
              <Button
                size='icon'
                variant='ghost'
                className='size-8'
                aria-label='Guardar consulta'
                disabled={!objectIds.length || !counterIds.length}
                onClick={() => {
                  setName(title)
                  setSaveOpen(true)
                }}
              >
                <Save className='size-3.5' />
              </Button>
              <Button
                size='icon'
                variant='ghost'
                className='size-8'
                aria-label='Actualizar consulta'
                disabled={result.isFetching || !counterIds.length}
                onClick={() => {
                  void catalog.refetch()
                  void result.refetch()
                }}
              >
                <RefreshCw
                  className={`size-3.5 ${result.isFetching ? 'animate-spin' : ''}`}
                />
              </Button>
            </div>
          </div>
          <div className='flex flex-wrap items-center gap-2 border-b px-3 py-2'>
            <div className='flex items-center rounded border p-0.5'>
              {(
                [
                  { id: 'line', label: 'Líneas', icon: LineChart },
                  { id: 'bar', label: 'Barras', icon: BarChart3 },
                  { id: 'table', label: 'Datos', icon: Table2 },
                ] as const
              ).map((v) => (
                <Button
                  key={v.id}
                  size='icon'
                  variant={mode === v.id ? 'secondary' : 'ghost'}
                  className='size-7'
                  aria-label={v.label}
                  title={v.label}
                  aria-pressed={mode === v.id}
                  onClick={() => setMode(v.id)}
                >
                  <v.icon className='size-3.5' />
                </Button>
              ))}
            </div>
            <CompactSelect
              label='Rango'
              value={range}
              options={['15m', '1h', '6h', '24h', '7d'].map((v) => [v, v])}
              onChange={(v) => setRange(v as RangeKey)}
            />
            <CompactSelect
              label='Agregación'
              value={aggregation}
              options={[
                ['avg', 'Promedio'],
                ['min', 'Mínimo'],
                ['max', 'Máximo'],
                ['sum', 'Suma'],
                ['last', 'Último'],
              ]}
              onChange={(v) => setAggregation(v as Aggregation)}
            />
            <CompactSelect
              label='Resolución'
              value={String(granularity)}
              options={[
                ...(template.id === 'upf-slice-performance' ||
                template.id === 'upf-xdp-acceleration'
                  ? [['5', '5 s'], ['30', '30 s'], ['60', '1 min']]
                  : []),
                [
                  '300',
                  requires30m ? '5 min (Bloqueado)' : '5 min',
                  requires30m,
                ],
                ['1800', '30 min', false],
              ]}
              onChange={(v) => setGranularity(Number(v))}
            />
            {(['csv', 'json'] as const).map((format) => (
              <Button key={format} size='sm' variant='ghost'
                aria-label={`Exportar ${format.toUpperCase()}`}
                disabled={!result.data?.series.length}
                onClick={() => result.data && exportPerformance(result.data, format)}>
                {format.toUpperCase()}
              </Button>
            ))}
          </div>

          {template.id === 'upf-xdp-acceleration' && (

            <div className='border-b bg-muted/10 p-3'>

              <UpfXdpPanel />

            </div>

          )}

          <div className='min-h-0 flex-1 p-3'>
            {catalog.isError || result.isError ? (
              <div className='flex h-full items-center justify-center text-sm text-destructive'>
                {apiErrorMessage(
                  catalog.error ?? result.error,
                  'No se pudo cargar la consulta'
                )}
              </div>
            ) : catalog.isLoading || result.isLoading ? (
              <div className='flex h-full items-center justify-center text-xs text-muted-foreground'>
                Cargando mediciones…
              </div>
            ) : !objectIds.length || !counterIds.length ? (
              <div className='flex h-full items-center justify-center text-center text-sm text-muted-foreground'>
                Seleccione objetos y un contador en el panel derecho.
              </div>
            ) : (
              <PerformanceChart result={displayedResult} mode={mode} />
            )}
          </div>
          <div className='flex flex-wrap items-center gap-3 border-t px-3 py-2 text-[11px] text-muted-foreground'>
            <span>{result.data?.series.length ?? 0} series</span>
            <span>{result.data?.sample_count ?? 0} muestras</span>
            {!!result.data?.missing_series?.length && (
              <span>
                {result.data.missing_series.length} series sin muestras
              </span>
            )}
            <div className='ml-auto'>
              <ReportDialog
                draft={draft}
                title={title}
                savedQueries={(saved.data ?? []).filter(
                  (q) => q.scenario_id === scenario
                )}
                disabled={!result.data?.series.length}
              />
            </div>
          </div>
        </main>
        <aside className='flex min-h-0 flex-col border-t lg:border-t-0 lg:border-l'>
          <div className='flex h-11 shrink-0 items-center justify-between border-b px-3 text-xs font-medium'>
            Control de consulta
            <button
              className='font-normal text-muted-foreground hover:text-foreground'
              onClick={() => applyTemplate(template)}
            >
              Restablecer
            </button>
          </div>
          <div className='flex min-h-48 flex-col border-b lg:h-[35%]'>
            <div className='flex items-center justify-between px-3 pt-3 text-xs font-medium'>
              <span>
                Objetos{' '}
                <span className='text-muted-foreground'>
                  ({objectIds.length})
                </span>
              </span>
              <button
                className='font-normal text-muted-foreground hover:text-foreground'
                onClick={toggleAllObjects}
              >
                {objectIds.length === objects.length
                  ? 'Limpiar'
                  : 'Seleccionar todos'}
              </button>
            </div>
            <div className='p-3'>
              <SearchField
                label='Buscar objeto'
                value={objectSearch}
                onChange={setObjectSearch}
              />
            </div>
            <div className='min-h-0 flex-1 overflow-auto px-2 pb-2'>
              {visibleObjects.map((o) => (
                <label
                  key={o.id}
                  className='flex cursor-pointer items-center gap-2 rounded px-2 py-2 text-xs hover:bg-muted'
                >
                  <Checkbox
                    checked={objectIds.includes(o.id)}
                    onCheckedChange={() => toggleObject(o.id)}
                  />
                  <span className='min-w-0 flex-1 truncate' title={o.label}>
                    {getObjectPresentation(o.id, catalog.data?.objects).label}
                  </span>
                </label>
              ))}
              {catalog.isSuccess && !objects.length && (
                <p className='p-2 text-xs text-muted-foreground'>
                  Sin objetos disponibles
                </p>
              )}
            </div>
          </div>
          <div className='flex min-h-0 flex-1 flex-col'>
            <div className='flex items-center justify-between px-3 pt-3 text-xs font-medium'>
              <span>
                Contador{' '}
                <span className='text-muted-foreground'>
                  ({counterIds.length})
                </span>
              </span>
              <button
                className='font-normal text-muted-foreground hover:text-foreground'
                onClick={clearCounters}
              >
                Limpiar
              </button>
            </div>
            <div className='p-3'>
              <SearchField
                label='Buscar contador'
                value={counterSearch}
                onChange={setCounterSearch}
              />
            </div>
            <div className='min-h-0 flex-1 overflow-auto px-2 pb-2'>
              {visibleCounters.map((c) => (
                <label
                  key={c.id}
                  title={`${c.native_name ?? c.label}\nFuente: ${c.source}\n${c.description ?? ''}`}
                  className={`flex cursor-pointer items-start gap-2 rounded-sm border-b border-border/40 px-2 py-2.5 text-xs transition-colors ${counterIds.includes(c.id) ? 'bg-blue-500/10' : 'hover:bg-muted'}`}
                >
                  <input
                    type='radio'
                    name='performance-counter'
                    aria-label={c.label}
                    className='peer sr-only'
                    checked={counterIds.includes(c.id)}
                    onChange={() => toggleCounter(c.id)}
                  />
                  <span
                    aria-hidden='true'
                    className={`mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full border bg-transparent peer-focus-visible:ring-2 peer-focus-visible:ring-blue-500 ${counterIds.includes(c.id) ? 'border-blue-600 dark:border-blue-400' : 'border-muted-foreground/60'}`}
                  >
                    {counterIds.includes(c.id) && (
                      <span className='size-2 rounded-full bg-blue-600 dark:bg-blue-400' />
                    )}
                  </span>
                  <span className='min-w-0 flex-1 leading-5'>{c.label}</span>
                  <span className='pt-0.5 text-[10px] text-muted-foreground'>
                    {c.unit}
                  </span>
                </label>
              ))}
              {!visibleCounters.length && (
                <p className='p-2 text-xs text-muted-foreground'>
                  {!objectIds.length
                    ? 'Seleccione un objeto'
                    : 'Sin contadores disponibles'}
                </p>
              )}
            </div>
          </div>
        </aside>
      </div>

      {/* DIÁLOGO PARA GUARDAR CONSULTA */}
      <Dialog open={saveOpen} onOpenChange={setSaveOpen}>
        <DialogContent className='sm:max-w-md'>
          <DialogHeader>
            <DialogTitle>Guardar consulta de rendimiento</DialogTitle>
            <DialogDescription>
              Guarde los objetos, contadores seleccionados, agregación y rango
              temporal para consultarlos rápidamente.
            </DialogDescription>
          </DialogHeader>
          <Input
            aria-label='Nombre de la consulta'
            placeholder='Ej: Monitoreo Throughput UDG 1 y 2'
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <DialogFooter>
            <Button variant='outline' onClick={() => setSaveOpen(false)}>
              Cancelar
            </Button>
            <Button
              disabled={!name.trim() || save.isPending}
              onClick={() => save.mutate()}
            >
              {save.isPending ? 'Guardando…' : 'Guardar'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </EmsPage>
  )
}

function SearchField({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <div className='relative'>
      <Search className='absolute top-2.5 left-2.5 size-3.5 text-muted-foreground' />
      <Input
        aria-label={label}
        placeholder={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className='h-8 pl-8 text-xs'
      />
    </div>
  )
}

function CompactSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string
  value: string
  options: (string[] | [string, string, boolean?])[]
  onChange: (v: string) => void
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger
        aria-label={label}
        className='h-7 w-auto min-w-20 text-[11px]'
      >
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((opt) => {
          const id = opt[0]
          const text = opt[1]
          const disabled = Boolean(opt[2])
          return (
            <SelectItem
              key={id}
              value={id}
              disabled={disabled}
              className='text-xs'
            >
              {text}
            </SelectItem>
          )
        })}
      </SelectContent>
    </Select>
  )
}
