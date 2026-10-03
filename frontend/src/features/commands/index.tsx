import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  BookOpen,
  CheckCircle2,
  ChevronDown,
  Copy,
  Database,
  Download,
  History,
  Network,
  Play,
  List,
  SlidersHorizontal,
  Search,
  Server,
  ShieldAlert,
  Terminal,
  Wrench,
} from 'lucide-react'
import { toast } from 'sonner'
import { useScenarioStore } from '@/stores/scenario-store'
import { api, apiErrorMessage } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog'
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { EmsPage } from '@/features/ems-page'
import {
  formatTelcoReport,
  parseMmlCommand,
  redactMml,
  toMmlSyntax,
} from './mml-formatter'
import type {
  ComponentOperations,
  OperationDefinition,
  OperationExecutePayload,
  OperationResult,
  OperationRun,
  OperationsCatalog,
} from './types'

type ResultTab = 'result' | 'history'

export type NetworkDomainId = 'all' | 'control' | 'user' | 'infra'

export interface DomainMeta {
  id: Exclude<NetworkDomainId, 'all'>
  label: string
  shortLabel: string
  icon: typeof Server
  order: number
}

export const DOMAINS: Record<Exclude<NetworkDomainId, 'all'>, DomainMeta> = {
  control: {
    id: 'control',
    label: 'Plano de Control (5GC)',
    shortLabel: 'Control',
    icon: Server,
    order: 1,
  },
  user: {
    id: 'user',
    label: 'Plano de Usuario (UPF)',
    shortLabel: 'Usuario',
    icon: Network,
    order: 2,
  },
  infra: {
    id: 'infra',
    label: 'Datos & Infraestructura',
    shortLabel: 'Datos',
    icon: Database,
    order: 3,
  },
}

export const NF_SIGNALING_ORDER: Record<string, number> = {
  // 5G Core Control Plane (Flujo de señalización de trazas 3GPP: AMF -> SMF -> AUSF -> UDM -> PCF -> NSSF -> NRF -> SCP -> UDR)
  amf: 1,
  smf: 2,
  ausf: 3,
  udm: 4,
  pcf: 5,
  nssf: 6,
  nrf: 7,
  scp: 8,
  udr: 9,
  chf: 10,
  nwdaf: 11,

  // Plano de Usuario
  upf: 1,
  upf2: 2,

  // Datos
  mongodb: 1,
}

export function getComponentDomain(
  component: ComponentOperations
): Exclude<NetworkDomainId, 'all'> {
  const kind = (component.kind ?? '').toLowerCase()
  const id = component.id.toLowerCase()
  const unit = (component.unit ?? '').toLowerCase()

  if (kind === 'user-plane' || id.startsWith('upf') || unit.includes('upf')) {
    return 'user'
  }
  if (kind === 'database' || id === 'mongodb' || unit.includes('mongod')) {
    return 'infra'
  }
  return 'control'
}

export function parseNodeLabel(label: string): {
  title: string
  subtitle?: string
} {
  const match = /^([^(]+?)(?:\s*\(([^)]+)\))?$/.exec(label.trim())
  if (!match) return { title: label }
  return {
    title: match[1].trim(),
    subtitle: match[2]?.trim(),
  }
}

export function CommandsPage() {
  const queryClient = useQueryClient()
  const searchParams = new URLSearchParams(
    typeof window !== 'undefined' ? window.location.search : ''
  )
  const initialNode =
    searchParams.get('component') || searchParams.get('node') || ''

  const scenario = useScenarioStore((state) => state.scenario)
  const [selectedComponentId, setSelectedComponentId] = useState(initialNode)
  const [selectedOperationId, setSelectedOperationId] = useState('')
  const [collapsedDomains, setCollapsedDomains] = useState<
    Record<string, boolean>
  >({})
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({})
  const [nodeSearch, setNodeSearch] = useState('')
  const [operationSearch, setOperationSearch] = useState('')
  const [catalogOpen, setCatalogOpen] = useState(false)
  const [manualOpen, setManualOpen] = useState(false)
  const [parametersOpen, setParametersOpen] = useState(false)
  const [confirmation, setConfirmation] =
    useState<OperationExecutePayload | null>(null)
  const executionLock = useRef(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const [commandInput, setCommandInput] = useState('')
  const [lastResult, setLastResult] = useState<OperationResult | null>(null)
  const [lastMmlCommand, setLastMmlCommand] = useState('')
  const [resultTab, setResultTab] = useState<ResultTab>('result')
  const [showSuggestions, setShowSuggestions] = useState(false)
  const [activeSuggestionIndex, setActiveSuggestionIndex] = useState(0)
  const suggestionsContainerRef = useRef<HTMLDivElement>(null)

  const catalogQuery = useQuery({
    queryKey: ['operations-catalog', scenario],
    queryFn: async () =>
      (await api.get<OperationsCatalog>(`/operations/catalog/${scenario}`))
        .data,
    refetchInterval: 15_000,
  })

  const historyQuery = useQuery({
    queryKey: ['operations-history', scenario],
    queryFn: async () =>
      (
        await api.get<OperationRun[]>(
          `/operations/history?scenario_id=${scenario}&limit=50`
        )
      ).data,
    refetchInterval: 10_000,
  })

  const components = useMemo(() => {
    const raw = catalogQuery.data?.components ?? []
    // Enfoque 3GPP Core O&M: Excluir gNodeB (RAN) y UE (Terminales).
    // En una red de operador real, los UEs y gNodeBs se consultan centralizadamente desde el AMF/SMF.
    return raw.filter((c) => {
      const kind = (c.kind ?? '').toLowerCase()
      const id = c.id.toLowerCase()
      return (
        kind !== 'ran' &&
        kind !== 'ue' &&
        !id.startsWith('gnb') &&
        !id.startsWith('ue') &&
        !id.startsWith('enb')
      )
    })
  }, [catalogQuery.data?.components])

  const currentComponent = useMemo(
    () =>
      components.find((component) => component.id === selectedComponentId) ??
      components.find((component) => component.id === 'amf') ??
      components.find((component) => component.id === 'smf') ??
      components[0],
    [components, selectedComponentId]
  )

  const selectComponent = (componentId: string) => {
    setSelectedComponentId(componentId)
    const component = components.find((item) => item.id === componentId)
    if (component) {
      const dom = getComponentDomain(component)
      setCollapsedDomains((prev) =>
        prev[dom] ? { ...prev, [dom]: false } : prev
      )
    }
  }
  const currentOperation = useMemo(
    () =>
      currentComponent?.operations.find(
        (operation) => operation.id === selectedOperationId
      ),
    [currentComponent, selectedOperationId]
  )
  const executeMutation = useMutation({
    mutationFn: async ({
      original_command: _original,
      ...payload
    }: OperationExecutePayload) =>
      (await api.post<OperationResult>('/operations/execute', payload)).data,
    onSuccess: (result, variables) => {
      const component = components.find(
        (item) => item.id === variables.component_id
      )
      const operation = component?.operations.find(
        (item) => item.id === variables.operation_id
      )
      const syntax =
        variables.original_command ??
        (operation
          ? toMmlSyntax(
              operation,
              variables.component_id,
              component?.label ?? result.component_label,
              variables.parameters
            )
          : `%%${commandInput}%%`)
      setLastResult(result)
      setLastMmlCommand(redactMml(syntax))
      setResultTab('result')
      void queryClient.invalidateQueries({
        queryKey: ['operations-history', scenario],
      })
      toast[result.status === 'success' ? 'success' : 'error'](
        result.status === 'success'
          ? 'Operación completada'
          : 'Operación fallida',
        {
          description: `${result.component_label} · ${result.operation_label} · ${result.duration_ms} ms`,
        }
      )
    },
    onSettled: () => {
      executionLock.current = false
    },
    onError: (error, variables) => {
      showCommandError(
        apiErrorMessage(error, 'El nodo rechazó el comando.'),
        variables.original_command ?? commandInput
      )
      toast.error('No se pudo ejecutar la operación', {
        description: apiErrorMessage(error, 'El nodo rechazó el comando.'),
      })
    },
  })

  const showCommandError = (detail: string, command: string) => {
    const timestamp = new Date().toISOString()
    setLastMmlCommand(redactMml(command))
    setLastResult({
      id: crypto.randomUUID(),
      scenario_id: scenario,
      testbed_id: '',
      component_id: currentComponent?.id ?? '',
      component_label: currentComponent?.label ?? '',
      operation_id: currentOperation?.id ?? '',
      operation_label: 'COMMAND EXECUTION',
      category: '',
      mutating: false,
      username: '',
      role: '',
      parameters: {},
      status: 'failed',
      source: 'mml-console',
      output: detail,
      data: null,
      error: detail,
      started_at: timestamp,
      completed_at: timestamp,
      duration_ms: 0,
    })
    setResultTab('result')
  }

  const submit = (payload: OperationExecutePayload) => {
    if (executionLock.current) return
    executionLock.current = true
    setConfirmation(null)
    executeMutation.mutate(payload)
  }

  const handleExecute = () => {
    if (
      executionLock.current ||
      confirmation ||
      manualOpen ||
      catalogOpen ||
      !currentComponent ||
      catalogQuery.isError
    )
      return
    const input = stripEnvelope(commandInput.trim()).replace(/;$/, '').trim()
    if (!input) return
    // An omitted NF means the selected destination for infrastructure commands.
    // Analytics NF is a measurement target, not the command destination.
    const parsed = parseMmlCommand(
      input + ';',
      catalogQuery.data,
      currentComponent.id
    )
    if (!parsed.success) {
      toast.error('Comando no válido', { description: parsed.error })
      showCommandError(parsed.error, commandInput)
      return
    }
    if (parsed.componentId !== currentComponent.id) {
      toast.error('El destino del comando no coincide con el nodo seleccionado')
      showCommandError(
        'El destino del comando no coincide con el nodo seleccionado',
        commandInput
      )
      return
    }
    const operation = currentComponent.operations.find(
      (item) => item.id === parsed.operationId
    )
    if (!operation?.allowed) {
      toast.error('Operación no autorizada para su rol')
      showCommandError('Operación no autorizada para su rol', commandInput)
      return
    }
    setSelectedOperationId(operation.id)
    setParamValues(parsed.parameters)
    const payload: OperationExecutePayload = {
      scenario_id: scenario,
      component_id: currentComponent.id,
      operation_id: operation.id,
      parameters: parsed.parameters,
      original_command: redactMml(commandInput),
    }
    if (operation.mutating) setConfirmation(payload)
    else submit(payload)
  }

  const chooseOperation = (
    operation: OperationDefinition,
    targetComponent?: ComponentOperations
  ) => {
    const comp = targetComponent ?? currentComponent
    if (!comp) return
    if (comp.id !== currentComponent?.id) {
      selectComponent(comp.id)
    }
    const defaults = parameterDefaults(operation)
    setSelectedOperationId(operation.id)
    setParamValues(defaults)
    setCommandInput(
      stripEnvelope(toMmlSyntax(operation, comp.id, comp.label, defaults))
    )
    setParametersOpen(operation.parameters.length > 0)
    setCatalogOpen(false)
    setShowSuggestions(false)
    requestAnimationFrame(() => inputRef.current?.focus())
  }

  const allMmlSuggestions = useMemo(() => {
    const targetComponents = currentComponent ? [currentComponent] : components
    const list: {
      code: string
      syntax: string
      operation: OperationDefinition
      component: ComponentOperations
      isCurrentNode: boolean
    }[] = []

    for (const comp of targetComponents) {
      for (const op of comp.operations) {
        const code = operationCode(op, comp)
        const defaults = parameterDefaults(op)
        const syntax = stripEnvelope(
          toMmlSyntax(op, comp.id, comp.label, defaults)
        )
        list.push({
          code,
          syntax,
          operation: op,
          component: comp,
          isCurrentNode: true,
        })
      }
    }
    return list
  }, [components, currentComponent])

  const suggestions = useMemo(() => {
    const raw = commandInput.trim()
    if (!raw) return []
    const cleanQuery = raw
      .replace(/^%%/, '')
      .replace(/;$/, '')
      .trim()
      .toUpperCase()
    if (!cleanQuery) return []

    const tokens = cleanQuery.split(/\s+/).filter(Boolean)

    const matches = allMmlSuggestions.filter((item) => {
      const targetText =
        `${item.code} ${item.syntax} ${item.operation.label} ${item.component.id} ${item.component.label} ${item.operation.id}`.toUpperCase()
      return tokens.every((token) => targetText.includes(token))
    })

    return matches
      .sort((a, b) => {
        if (a.isCurrentNode && !b.isCurrentNode) return -1
        if (!a.isCurrentNode && b.isCurrentNode) return 1

        const aStarts = a.code.toUpperCase().startsWith(cleanQuery)
        const bStarts = b.code.toUpperCase().startsWith(cleanQuery)
        if (aStarts && !bStarts) return -1
        if (!aStarts && bStarts) return 1

        return a.code.localeCompare(b.code)
      })
      .slice(0, 8)
  }, [commandInput, allMmlSuggestions])

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        suggestionsContainerRef.current &&
        !suggestionsContainerRef.current.contains(event.target as Node) &&
        inputRef.current &&
        !inputRef.current.contains(event.target as Node)
      ) {
        setShowSuggestions(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [])

  const visibleComponents = useMemo(() => {
    const query = nodeSearch.trim().toLowerCase()
    if (!query) return components
    return components.filter((component) =>
      [
        component.id,
        component.label,
        component.unit,
        component.node_id,
        ...(component.interfaces ?? []),
      ]
        .filter(Boolean)
        .some((value) => value?.toLowerCase().includes(query))
    )
  }, [components, nodeSearch])

  const groupedComponents = useMemo(() => {
    const domainList = Object.values(DOMAINS).sort((a, b) => a.order - b.order)
    const groups: {
      domain: DomainMeta
      items: ComponentOperations[]
    }[] = []

    for (const domain of domainList) {
      const items = visibleComponents
        .filter((c) => getComponentDomain(c) === domain.id)
        .sort((a, b) => {
          const orderA = NF_SIGNALING_ORDER[a.id.toLowerCase()] ?? 99
          const orderB = NF_SIGNALING_ORDER[b.id.toLowerCase()] ?? 99
          if (orderA !== orderB) return orderA - orderB
          return a.label.localeCompare(b.label)
        })
      if (items.length > 0) {
        groups.push({ domain, items })
      }
    }
    return groups
  }, [visibleComponents])

  const visibleOperations = useMemo(() => {
    const query = operationSearch.trim().toLowerCase()
    const operations = currentComponent?.operations ?? []
    if (!query) return operations
    return operations.filter((operation) =>
      [
        operation.id,
        operation.label,
        operation.description,
        operation.category,
        operationCode(operation, currentComponent),
      ].some((value) => value.toLowerCase().includes(query))
    )
  }, [currentComponent, operationSearch])

  const groupedOperations = useMemo(
    () => groupOperations(visibleOperations),
    [visibleOperations]
  )
  const copyResult = () => {
    if (!lastResult) return
    void navigator.clipboard
      .writeText(formatTelcoReport(lastResult, lastMmlCommand))
      .then(() => toast.success('Resultado copiado'))
      .catch(() => toast.error('No se pudo copiar el resultado'))
  }

  const downloadResult = () => {
    if (!lastResult) return
    const content = formatTelcoReport(lastResult, lastMmlCommand)
    const url = URL.createObjectURL(
      new Blob([content], { type: 'text/plain;charset=utf-8' })
    )
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = `command_${lastResult.component_id}_${lastResult.id.slice(0, 8)}.txt`
    anchor.click()
    URL.revokeObjectURL(url)
  }

  const loadHistory = (run: OperationRun) => {
    const component = components.find((item) => item.id === run.component_id)
    const operation = component?.operations.find(
      (item) => item.id === run.operation_id
    )
    selectComponent(run.component_id)
    setSelectedOperationId(run.operation_id)
    setParamValues(run.parameters)
    setLastResult(run)
    if (component && operation) {
      const syntax = toMmlSyntax(
        operation,
        component.id,
        component.label,
        run.parameters
      )
      setLastMmlCommand(syntax)
      setCommandInput(stripEnvelope(syntax))
    }
    setResultTab('result')
  }

  return (
    <EmsPage title='Comandos' description=''>
      {catalogQuery.error && (
        <Alert variant='destructive' className='mb-3'>
          <AlertTitle>Catálogo no disponible</AlertTitle>
          <AlertDescription>
            {apiErrorMessage(
              catalogQuery.error,
              'Verifique la conexión con el backend.'
            )}
          </AlertDescription>
        </Alert>
      )}
      <Card className='mb-6 overflow-hidden p-0'>
        <div className='grid h-[calc(100dvh-110px)] min-h-[520px] grid-cols-1 md:grid-cols-[260px_minmax(0,1fr)] xl:grid-cols-[290px_minmax(0,1fr)]'>
          <aside className='flex min-h-0 flex-col border-b md:border-r md:border-b-0'>
            <div className='flex items-center justify-between border-b bg-muted/20 px-3 py-2.5 text-xs font-semibold'>
              <span>Funciones de Red Core</span>
              <span className='font-mono text-xs text-muted-foreground'>
                {visibleComponents.length}
              </span>
            </div>
            <div className='border-b p-2'>
              <SearchInput
                value={nodeSearch}
                onChange={setNodeSearch}
                placeholder='Buscar función de red…'
                label='Buscar funciones de red'
              />
            </div>
            {/* Lista agrupada y plegable por dominios */}
            <div className='max-h-56 flex-1 space-y-3 overflow-y-auto p-2 md:max-h-none'>
              {catalogQuery.isLoading ? (
                <LoadingRows count={6} />
              ) : groupedComponents.length > 0 ? (
                groupedComponents.map(({ domain, items }) => {
                  const DomainIcon = domain.icon
                  const isCollapsed = Boolean(collapsedDomains[domain.id])
                  const showItems = nodeSearch.trim() || !isCollapsed

                  return (
                    <div key={domain.id} className='space-y-1'>
                      <button
                        type='button'
                        onClick={() =>
                          setCollapsedDomains((prev) => ({
                            ...prev,
                            [domain.id]: !prev[domain.id],
                          }))
                        }
                        className='flex w-full items-center justify-between rounded px-1.5 py-1 text-[10px] font-bold tracking-wider text-muted-foreground uppercase transition-colors hover:bg-muted/50 hover:text-foreground'
                      >
                        <div className='flex items-center gap-1.5'>
                          <ChevronDown
                            className={cn(
                              'size-3 text-muted-foreground transition-transform duration-200',
                              !showItems && '-rotate-90'
                            )}
                          />
                          <DomainIcon className='size-3 text-primary/70' />
                          <span>{domain.label}</span>
                        </div>
                        <span className='py-0.2 rounded bg-muted/80 px-1.5 font-mono text-[9px] text-muted-foreground'>
                          {items.length}
                        </span>
                      </button>

                      {showItems && (
                        <div className='space-y-1 pl-1'>
                          {items.map((component) => (
                            <NodeButton
                              key={component.id}
                              component={component}
                              selected={component.id === currentComponent?.id}
                              onSelect={() => {
                                if (executeMutation.isPending) return
                                selectComponent(component.id)
                                setSelectedOperationId('')
                                setParamValues({})
                                setCommandInput('')
                                setParametersOpen(false)
                                setOperationSearch('')
                                inputRef.current?.focus()
                              }}
                            />
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })
              ) : (
                <EmptyList text='Sin funciones de red coincidentes' />
              )}
            </div>
          </aside>
          <section className='flex min-h-0 min-w-0 flex-col'>
            <div className='shrink-0 space-y-2 border-b bg-muted/10 p-3'>
              <div className='flex items-center justify-between gap-3'>
                <div className='flex min-w-0 items-center gap-2.5 text-xs'>
                  <span className='text-sm font-bold text-foreground'>
                    {currentComponent?.label ?? 'Sin destino'}
                  </span>
                  {currentComponent && (
                    <Badge
                      variant='outline'
                      className='h-4.5 border-border/80 px-1.5 font-sans text-[10px] font-normal text-muted-foreground'
                    >
                      {DOMAINS[getComponentDomain(currentComponent)].label}
                    </Badge>
                  )}
                  {currentComponent?.status && (
                    <span
                      className={cn(
                        'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[10px] font-medium',
                        currentComponent.status === 'running'
                          ? 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
                          : currentComponent.status === 'stopped'
                            ? 'bg-destructive/10 text-destructive'
                            : 'bg-amber-500/10 text-amber-600 dark:text-amber-400'
                      )}
                    >
                      <span
                        className={cn(
                          'size-1.5 rounded-full',
                          currentComponent.status === 'running'
                            ? 'bg-emerald-500 shadow-[0_0_5px_rgba(16,185,129,0.6)]'
                            : currentComponent.status === 'stopped'
                              ? 'bg-destructive'
                              : 'bg-amber-500'
                        )}
                      />
                      {statusLabel(currentComponent.status)}
                    </span>
                  )}
                </div>
                <div className='flex flex-wrap items-center gap-1'>
                  <Button
                    variant='ghost'
                    size='sm'
                    onClick={() => setManualOpen(true)}
                  >
                    <BookOpen className='size-4' />
                    Manual
                  </Button>
                  <Button
                    variant='ghost'
                    size='sm'
                    disabled={!currentComponent || executeMutation.isPending}
                    onClick={() => {
                      setOperationSearch('')
                      setCatalogOpen(true)
                    }}
                  >
                    <List className='size-3.5' />
                    Catálogo
                  </Button>
                  <Button
                    variant={parametersOpen ? 'secondary' : 'ghost'}
                    size='sm'
                    disabled={
                      !currentOperation?.parameters.length ||
                      !commandInput ||
                      executeMutation.isPending
                    }
                    onClick={() => setParametersOpen((value) => !value)}
                  >
                    <SlidersHorizontal className='size-3.5' />
                    Parámetros
                  </Button>
                </div>
              </div>
              <div className='relative'>
                {showSuggestions && suggestions.length > 0 && (
                  <div
                    ref={suggestionsContainerRef}
                    className='absolute top-full right-0 left-0 z-50 mt-1.5 animate-in overflow-hidden rounded-lg border border-border/80 bg-popover/95 text-popover-foreground shadow-2xl backdrop-blur-sm duration-100 fade-in-0 zoom-in-95'
                  >
                    <div className='flex items-center justify-between border-b border-border/60 bg-muted/40 px-3 py-1.5 text-[10px] text-muted-foreground'>
                      <div className='flex items-center gap-1.5 font-medium'>
                        <Terminal className='size-3 text-primary' />
                        <span>Sugerencias MML ({suggestions.length})</span>
                      </div>
                      <span className='font-mono text-[9px] text-muted-foreground/80'>
                        ↑ ↓ navegar · Tab o ↵ autocompletar · Esc cerrar
                      </span>
                    </div>
                    <div className='max-h-56 divide-y divide-border/20 overflow-y-auto p-1'>
                      {suggestions.map((item, idx) => (
                        <button
                          key={`${item.component.id}-${item.operation.id}-${idx}`}
                          type='button'
                          className={cn(
                            'flex w-full items-center justify-between gap-3 rounded-md px-2.5 py-2 text-left font-mono transition-colors',
                            idx === activeSuggestionIndex
                              ? 'bg-primary font-semibold text-primary-foreground'
                              : 'text-foreground hover:bg-muted/70'
                          )}
                          onMouseDown={(e) => {
                            e.preventDefault()
                            chooseOperation(item.operation, item.component)
                          }}
                          onMouseEnter={() => setActiveSuggestionIndex(idx)}
                        >
                          <div className='flex min-w-0 flex-1 items-center gap-2 text-xs'>
                            <Badge
                              variant={
                                idx === activeSuggestionIndex
                                  ? 'outline'
                                  : 'secondary'
                              }
                              className={cn(
                                'h-4 shrink-0 px-1 font-sans text-[9px] font-semibold uppercase',
                                idx === activeSuggestionIndex &&
                                  'border-primary-foreground/40 text-primary-foreground'
                              )}
                            >
                              {item.component.id}
                            </Badge>
                            <span className='truncate font-mono'>
                              {item.syntax}
                            </span>
                          </div>
                          <div className='flex shrink-0 items-center gap-1.5'>
                            {item.operation.mutating ? (
                              <Wrench
                                className={cn(
                                  'size-3',
                                  idx === activeSuggestionIndex
                                    ? 'text-primary-foreground'
                                    : 'text-amber-500'
                                )}
                              />
                            ) : (
                              <Activity
                                className={cn(
                                  'size-3',
                                  idx === activeSuggestionIndex
                                    ? 'text-primary-foreground'
                                    : 'text-sky-500'
                                )}
                              />
                            )}
                            <span
                              className={cn(
                                'max-w-[150px] truncate font-sans text-[11px]',
                                idx === activeSuggestionIndex
                                  ? 'text-primary-foreground/90'
                                  : 'text-muted-foreground'
                              )}
                            >
                              {item.operation.label}
                            </span>
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                <form
                  className='flex items-center gap-2'
                  onSubmit={(event) => {
                    event.preventDefault()
                    handleExecute()
                  }}
                >
                  <div className='flex min-w-0 flex-1 items-center rounded-md border bg-background px-3 focus-within:ring-1 focus-within:ring-ring'>
                    <span className='mr-2 shrink-0 font-mono text-xs text-primary'>
                      {currentComponent?.id.toUpperCase() ?? 'EMS'}&gt;
                    </span>
                    <Input
                      ref={inputRef}
                      id='command-line'
                      aria-label='Comando MML'
                      placeholder='Escribir comando…'
                      value={commandInput}
                      disabled={!currentComponent || executeMutation.isPending}
                      onFocus={() => {
                        if (commandInput.trim()) setShowSuggestions(true)
                      }}
                      onChange={(event) => {
                        setCommandInput(event.target.value)
                        setShowSuggestions(true)
                        setActiveSuggestionIndex(0)
                        setParametersOpen(false)
                        setSelectedOperationId('')
                        setParamValues({})
                      }}
                      onKeyDown={(event) => {
                        if (showSuggestions && suggestions.length > 0) {
                          if (event.key === 'ArrowDown') {
                            event.preventDefault()
                            setActiveSuggestionIndex(
                              (prev) => (prev + 1) % suggestions.length
                            )
                            return
                          }
                          if (event.key === 'ArrowUp') {
                            event.preventDefault()
                            setActiveSuggestionIndex(
                              (prev) =>
                                (prev - 1 + suggestions.length) %
                                suggestions.length
                            )
                            return
                          }
                          if (event.key === 'Tab') {
                            event.preventDefault()
                            if (suggestions[activeSuggestionIndex]) {
                              chooseOperation(
                                suggestions[activeSuggestionIndex].operation,
                                suggestions[activeSuggestionIndex].component
                              )
                            }
                            return
                          }
                          if (event.key === 'Escape') {
                            event.preventDefault()
                            setShowSuggestions(false)
                            return
                          }
                          if (
                            event.key === 'Enter' &&
                            !event.ctrlKey &&
                            !event.metaKey
                          ) {
                            const currentTrim = commandInput
                              .trim()
                              .toUpperCase()
                            const selected = suggestions[activeSuggestionIndex]
                            if (
                              selected &&
                              currentTrim !==
                                selected.syntax
                                  .replace(/;$/, '')
                                  .toUpperCase() &&
                              currentTrim !== selected.code.toUpperCase()
                            ) {
                              event.preventDefault()
                              chooseOperation(
                                selected.operation,
                                selected.component
                              )
                              return
                            }
                          }
                        }

                        if (
                          (event.ctrlKey || event.metaKey) &&
                          event.key === 'Enter'
                        ) {
                          event.preventDefault()
                          handleExecute()
                        }
                      }}
                      className='h-10 min-w-0 border-0 bg-transparent px-0 font-mono text-xs shadow-none focus-visible:ring-0'
                      autoComplete='off'
                      spellCheck={false}
                    />
                  </div>
                  <Button
                    type='submit'
                    disabled={
                      !commandInput.trim() ||
                      !currentComponent ||
                      catalogQuery.isError ||
                      executeMutation.isPending
                    }
                  >
                    <Play className='size-4' />
                    Ejecutar
                  </Button>
                </form>
              </div>
              {parametersOpen && currentOperation && currentComponent && (
                <div className='grid max-h-40 gap-3 overflow-y-auto pt-2 sm:grid-cols-2 lg:grid-cols-3'>
                  {currentOperation.parameters.map((parameter) => (
                    <ParameterField
                      key={parameter.id}
                      parameter={parameter}
                      value={paramValues[parameter.id]}
                      onChange={(value) => {
                        const next = { ...paramValues, [parameter.id]: value }
                        setParamValues(next)
                        setCommandInput(
                          stripEnvelope(
                            toMmlSyntax(
                              currentOperation,
                              currentComponent.id,
                              currentComponent.label,
                              next
                            )
                          )
                        )
                      }}
                    />
                  ))}
                </div>
              )}
            </div>
            <Tabs
              value={resultTab}
              onValueChange={(value) => setResultTab(value as ResultTab)}
              className='flex min-h-0 flex-1 flex-col gap-0'
            >
              <div className='flex flex-wrap items-center justify-between gap-2 border-b px-3 py-2'>
                <TabsList className='h-8'>
                  <TabsTrigger value='result' className='text-xs'>
                    <Terminal className='size-3.5' />
                    Resultado
                  </TabsTrigger>
                  <TabsTrigger value='history' className='text-xs'>
                    <History className='size-3.5' />
                    Historial
                  </TabsTrigger>
                </TabsList>
                <div className='flex items-center gap-1'>
                  {executeMutation.isPending && (
                    <span
                      role='status'
                      className='mr-2 text-xs text-muted-foreground'
                    >
                      Ejecutando…
                    </span>
                  )}
                  <Button
                    variant='ghost'
                    size='sm'
                    onClick={copyResult}
                    disabled={!lastResult}
                  >
                    <Copy className='size-3.5' />
                    Copiar
                  </Button>
                  <Button
                    variant='ghost'
                    size='sm'
                    onClick={downloadResult}
                    disabled={!lastResult}
                  >
                    <Download className='size-3.5' />
                    Exportar
                  </Button>
                </div>
              </div>
              <TabsContent
                value='result'
                className='m-0 min-h-0 flex-1 overflow-auto'
              >
                <CommandResult result={lastResult} command={lastMmlCommand} />
              </TabsContent>
              <TabsContent
                value='history'
                className='m-0 min-h-0 flex-1 overflow-auto'
              >
                {historyQuery.isError ? (
                  <EmptyList text='No se pudo cargar el historial.' />
                ) : (
                  <CommandHistory
                    runs={historyQuery.data ?? []}
                    loading={historyQuery.isLoading}
                    onLoad={loadHistory}
                  />
                )}
              </TabsContent>
            </Tabs>
          </section>
        </div>
      </Card>

      <Dialog open={catalogOpen} onOpenChange={setCatalogOpen}>
        <DialogContent className='sm:max-w-xl'>
          <DialogHeader>
            <DialogTitle>Catálogo · {currentComponent?.label}</DialogTitle>
            <DialogDescription>Seleccionar comando</DialogDescription>
          </DialogHeader>
          <SearchInput
            value={operationSearch}
            onChange={setOperationSearch}
            placeholder='Buscar comando…'
            label='Buscar comandos'
          />
          <div className='max-h-[55vh] space-y-4 overflow-y-auto'>
            {groupedOperations.map(([category, operations]) => (
              <div key={category}>
                <p className='px-2 py-1 text-xs text-muted-foreground'>
                  {category}
                </p>
                {operations.map((operation) => (
                  <OperationButton
                    key={operation.id}
                    operation={operation}
                    code={operationCode(operation, currentComponent)}
                    selected={operation.id === currentOperation?.id}
                    onSelect={() => chooseOperation(operation)}
                  />
                ))}
              </div>
            ))}
            {!visibleOperations.length && (
              <EmptyList text='Sin coincidencias' />
            )}
          </div>
        </DialogContent>
      </Dialog>
      <Dialog open={manualOpen} onOpenChange={setManualOpen}>
        <DialogContent className='sm:max-w-3xl'>
          <DialogHeader>
            <DialogTitle>Manual de comandos</DialogTitle>
            <DialogDescription>
              Referencia del catálogo disponible · {currentComponent?.label}
            </DialogDescription>
          </DialogHeader>
          <CommandReference component={currentComponent} />
        </DialogContent>
      </Dialog>
      <Dialog
        open={!!confirmation}
        onOpenChange={(open) => {
          if (!open) setConfirmation(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirmar operación</DialogTitle>
            <DialogDescription>
              Esta acción puede interrumpir el servicio o las sesiones del nodo
              seleccionado.
            </DialogDescription>
          </DialogHeader>
          <p className='font-mono text-sm'>
            {confirmation?.component_id.toUpperCase()} ·{' '}
            {confirmation?.operation_id}
          </p>
          <pre className='overflow-auto rounded border p-3 text-xs'>
            {JSON.stringify(
              confirmation?.parameters,
              (key, value: unknown) =>
                ['key', 'opc'].includes(key) ? '[REDACTED]' : value,
              2
            )}
          </pre>
          <div className='flex justify-end gap-2'>
            <Button variant='outline' onClick={() => setConfirmation(null)}>
              Cancelar
            </Button>
            <Button
              variant='destructive'
              disabled={executeMutation.isPending}
              onClick={() => {
                if (confirmation) submit(confirmation)
              }}
            >
              Confirmar ejecución
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </EmsPage>
  )
}

function CommandReference({ component }: { component?: ComponentOperations }) {
  if (!component) return <EmptyList text='Seleccione un elemento de red.' />

  return (
    <ScrollArea className='h-[65vh]'>
      <div className='space-y-5 p-5'>
        <div>
          <h3 className='text-sm font-semibold'>
            Referencia operativa del 5G Core
          </h3>
          <p className='mt-1 text-xs text-muted-foreground'>
            Consola MML de operación centralizada para funciones de red del
            Núcleo 5G (3GPP Rel-16). Cada código se traduce a una operación
            validada del backend sin abrir terminales SSH libres. Para consultar
            el estado de terminales (UE) y enlaces radio (gNodeB), utilice las
            operaciones del AMF (<code>LST AMF-UE-CONTEXT:;</code>,{' '}
            <code>LST AMF-GNB-ASSOC:;</code>) o del SMF (
            <code>LST SMF-PDU-SESSION:;</code>) conforme al estándar 3GPP TS
            28.530.
          </p>
        </div>

        <div className='grid gap-2 sm:grid-cols-2 xl:grid-cols-4'>
          {[
            ['DSP', 'Mostrar estado o información'],
            ['LST', 'Listar registros o contexto'],
            ['CHK', 'Verificar conectividad'],
            ['RST', 'Reiniciar de forma controlada'],
          ].map(([prefix, description]) => (
            <div key={prefix} className='rounded-md border bg-muted/10 p-3'>
              <p className='font-mono text-xs font-semibold text-primary'>
                {prefix}
              </p>
              <p className='mt-1 text-[11px] text-muted-foreground'>
                {description}
              </p>
            </div>
          ))}
        </div>

        <div className='rounded-md border'>
          <div className='border-b bg-muted/20 px-3 py-2'>
            <p className='text-[10px] font-semibold tracking-[0.12em] text-muted-foreground uppercase'>
              Operaciones de {component.label}
            </p>
          </div>
          <div className='divide-y'>
            {component.operations.map((operation) => (
              <div
                key={operation.id}
                className='grid gap-1 px-3 py-2.5 sm:grid-cols-[180px_1fr] sm:gap-4'
              >
                <code className='text-[11px] font-semibold text-primary'>
                  {operationCode(operation, component)}
                </code>
                <div>
                  <p className='text-xs font-medium'>{operation.label}</p>
                  <p className='text-[11px] text-muted-foreground'>
                    {operation.description}
                  </p>
                  <code className='mt-2 block text-[11px] break-all'>
                    {stripEnvelope(
                      toMmlSyntax(
                        operation,
                        component.id,
                        component.label,
                        parameterDefaults(operation)
                      )
                    )}
                  </code>
                  {operation.parameters.map((parameter) => (
                    <p
                      key={parameter.id}
                      className='mt-1 text-xs text-muted-foreground'
                    >
                      <strong>{parameter.id}</strong> · {parameter.type}
                      {parameter.required ? ' · obligatorio' : ''}
                      {parameter.minimum !== undefined
                        ? ' · mínimo ' + parameter.minimum
                        : ''}
                      {parameter.maximum !== undefined
                        ? ' · máximo ' + parameter.maximum
                        : ''}{' '}
                      — {parameter.description ?? parameter.label}
                    </p>
                  ))}
                  {operation.mutating && (
                    <p className='mt-1 text-xs text-amber-600'>
                      Cambia el estado del nodo. Requiere confirmación.
                    </p>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className='rounded-md border border-zinc-800 bg-zinc-950 p-3 font-mono text-xs text-zinc-200'>
          <span className='text-emerald-400'>Formato:</span> VERBO OBJETO:
          PARAMETRO=&quot;valor&quot;;
        </div>
      </div>
    </ScrollArea>
  )
}

function SearchInput({
  value,
  onChange,
  placeholder,
  label,
}: {
  value: string
  onChange: (value: string) => void
  placeholder: string
  label: string
}) {
  return (
    <div className='relative'>
      <Search className='absolute top-2.5 left-2.5 size-4 text-muted-foreground' />
      <Input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        aria-label={label}
        className='h-9 pl-8 text-xs'
      />
    </div>
  )
}

function NodeButton({
  component,
  selected,
  onSelect,
}: {
  component: ComponentOperations
  selected: boolean
  onSelect: () => void
}) {
  const { title, subtitle } = parseNodeLabel(component.label)
  const isRunning = component.status === 'running'
  const isStopped = component.status === 'stopped'

  return (
    <button
      type='button'
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        'group flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-all duration-150',
        selected
          ? 'bg-primary/10 font-semibold text-foreground ring-1 ring-primary/25'
          : 'text-foreground/80 hover:bg-muted/60 hover:text-foreground'
      )}
    >
      <span
        className={cn(
          'flex size-6 shrink-0 items-center justify-center rounded border bg-background/80 transition-colors',
          selected
            ? 'border-primary/40 text-primary'
            : 'border-border/60 text-muted-foreground group-hover:text-foreground'
        )}
      >
        {componentIcon(component)}
      </span>
      <span className='flex min-w-0 flex-1 items-center justify-between gap-2'>
        <span className='flex min-w-0 items-center gap-1.5'>
          <span
            className={cn(
              'truncate text-xs tracking-tight',
              selected ? 'font-bold text-foreground' : 'font-medium'
            )}
          >
            {title}
          </span>
          {subtitle && (
            <Badge
              variant='outline'
              className={cn(
                'h-4 shrink-0 px-1 py-0 text-[9px] leading-none font-normal',
                selected
                  ? 'border-primary/40 bg-primary/15 font-medium text-primary'
                  : 'border-border/70 text-muted-foreground'
              )}
            >
              {subtitle}
            </Badge>
          )}
        </span>
        <span
          className={cn(
            'size-1.5 shrink-0 rounded-full',
            isRunning
              ? 'bg-emerald-500 shadow-[0_0_5px_rgba(16,185,129,0.7)]'
              : isStopped
                ? 'bg-destructive'
                : 'bg-amber-500'
          )}
          title={statusLabel(component.status)}
          aria-label={statusLabel(component.status)}
        />
      </span>
    </button>
  )
}

function OperationButton({
  operation,
  code,
  selected,
  onSelect,
}: {
  operation: OperationDefinition
  code: string
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      type='button'
      onClick={onSelect}
      disabled={!operation.allowed}
      className={cn(
        'flex w-full items-start gap-2 rounded-md border px-3 py-2.5 text-left transition-colors',
        selected
          ? 'border-primary/30 bg-primary/10'
          : 'border-transparent hover:border-border hover:bg-muted/40',
        !operation.allowed && 'cursor-not-allowed opacity-50'
      )}
    >
      {operation.mutating ? (
        <Wrench className='mt-0.5 size-3.5 shrink-0 text-amber-500' />
      ) : (
        <Activity className='mt-0.5 size-3.5 shrink-0 text-sky-500' />
      )}
      <span className='min-w-0 flex-1'>
        <span className='block truncate font-mono text-[10px] font-semibold text-primary'>
          {code}
        </span>
        <span className='mt-0.5 block truncate text-xs'>{operation.label}</span>
      </span>
    </button>
  )
}

function ParameterField({
  parameter,
  value,
  onChange,
}: {
  parameter: OperationDefinition['parameters'][number]
  value: unknown
  onChange: (value: unknown) => void
}) {
  return (
    <div className='space-y-1.5'>
      <Label className='text-xs'>
        {parameter.label}
        {parameter.required && <span className='ml-1 text-destructive'>*</span>}
      </Label>
      {parameter.type === 'select' ? (
        <Select
          value={String(value ?? parameter.default ?? '')}
          onValueChange={(next) => {
            const option = parameter.options?.find(
              (item) => String(item.value) === next
            )
            onChange(option?.value ?? next)
          }}
        >
          <SelectTrigger className='h-9 text-xs'>
            <SelectValue placeholder='Seleccione un valor' />
          </SelectTrigger>
          <SelectContent>
            {parameter.options?.map((option) => (
              <SelectItem
                key={String(option.value)}
                value={String(option.value)}
              >
                {option.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      ) : (
        <Input
          type={
            parameter.secret
              ? 'password'
              : parameter.type === 'number'
                ? 'number'
                : 'text'
          }
          step={parameter.integer === false ? 'any' : 1}
          min={parameter.minimum}
          max={parameter.maximum}
          value={String(value ?? parameter.default ?? '')}
          onChange={(event) =>
            onChange(
              parameter.type === 'number' && event.target.value !== ''
                ? Number(event.target.value)
                : event.target.value
            )
          }
          className='h-9 text-xs'
        />
      )}
    </div>
  )
}

function CommandResult({
  result,
  command,
}: {
  result: OperationResult | null
  command: string
}) {
  if (!result)
    return (
      <div className='flex h-full min-h-40 items-center justify-center font-mono text-xs text-muted-foreground'>
        Consola lista
      </div>
    )
  return (
    <pre
      aria-label='Salida del comando'
      className='min-h-full overflow-auto p-5 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap'
    >
      {formatTelcoReport(result, command)}
    </pre>
  )
}

function CommandHistory({
  runs,
  loading,
  onLoad,
}: {
  runs: OperationRun[]
  loading: boolean
  onLoad: (run: OperationRun) => void
}) {
  return (
    <ScrollArea className='h-full'>
      <div className='divide-y'>
        {loading && !runs.length ? (
          <LoadingRows count={4} />
        ) : (
          runs.map((run) => (
            <button
              type='button'
              key={run.id}
              onClick={() => onLoad(run)}
              className='flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/40'
            >
              {run.status === 'success' ? (
                <CheckCircle2 className='size-4 shrink-0 text-emerald-500' />
              ) : (
                <ShieldAlert className='size-4 shrink-0 text-destructive' />
              )}
              <span className='min-w-0 flex-1'>
                <span className='block truncate text-xs font-medium'>
                  {run.component_label} · {run.operation_label}
                </span>
                <span className='block truncate text-[10px] text-muted-foreground'>
                  {new Date(run.started_at).toLocaleString()} · {run.username} ·{' '}
                  {run.duration_ms} ms
                </span>
              </span>
              <Badge variant='outline' className='font-mono text-[9px]'>
                {run.id.slice(0, 8)}
              </Badge>
            </button>
          ))
        )}
        {!loading && !runs.length && (
          <EmptyList text='Todavía no se han ejecutado operaciones en este escenario.' />
        )}
      </div>
    </ScrollArea>
  )
}

function EmptyList({ text }: { text: string }) {
  return <p className='p-6 text-center text-xs text-muted-foreground'>{text}</p>
}

function LoadingRows({ count }: { count: number }) {
  return (
    <div className='space-y-2 p-2'>
      {Array.from({ length: count }, (_, index) => (
        <div key={index} className='h-12 animate-pulse rounded-md bg-muted' />
      ))}
    </div>
  )
}

function parameterDefaults(operation: OperationDefinition) {
  const defaults: Record<string, unknown> = {}
  for (const parameter of operation.parameters) {
    if (parameter.default !== undefined)
      defaults[parameter.id] = parameter.default
    else if (parameter.required && parameter.options?.length)
      defaults[parameter.id] = parameter.options[0].value
  }
  return defaults
}

function groupOperations(operations: OperationDefinition[]) {
  const groups = new Map<string, OperationDefinition[]>()
  for (const operation of operations) {
    const current = groups.get(operation.category) ?? []
    current.push(operation)
    groups.set(operation.category, current)
  }
  return [...groups.entries()]
}

function operationCode(
  operation: OperationDefinition,
  component?: ComponentOperations
) {
  if (!component) return operation.id.toUpperCase()
  return stripEnvelope(
    toMmlSyntax(operation, component.id, component.label, {})
  ).split(':')[0]
}

function stripEnvelope(value: string) {
  return value.replace(/^%%/, '').replace(/%%$/, '')
}

function statusLabel(status?: string) {
  if (status === 'running') return 'Operativo'
  if (status === 'stopped') return 'Detenido'
  if (status === 'degraded') return 'Degradado'
  return status || 'Desconocido'
}

function componentIcon(component: ComponentOperations) {
  const kind = (component.kind ?? '').toLowerCase()
  const id = component.id.toLowerCase()
  const unit = (component.unit ?? '').toLowerCase()

  if (
    kind === 'user-plane' ||
    id.startsWith('upf') ||
    id.startsWith('sgwu') ||
    id.startsWith('pgwu') ||
    unit.includes('upf')
  ) {
    return <Network className='size-3.5' />
  }
  if (kind === 'database' || id === 'mongodb' || unit.includes('mongod')) {
    return <Database className='size-3.5' />
  }
  return <Server className='size-3.5' />
}
