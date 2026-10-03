import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  ArrowLeft,
  ChevronRight,
  ListChecks,
  Loader2,
  Network,
  Plus,
  Play,
  RefreshCw,
  Square,
  UserRoundSearch,
} from 'lucide-react'
import { api } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  isActiveTrace,
  type InterfaceTraceDraft,
  type NodeTraceCatalogItem,
  type TraceAnalysis,
  type TraceCapabilities,
  type TraceEvent,
  type TraceTask,
} from '../types'
import { MessageCodeStreamsModal } from './message-code-streams-modal'

const PAGE_SIZE = 50
const SUPPORTED_NODES = new Set(['amf', 'smf', 'upf', 'nrf', 'scp'])

type Props = {
  tasks: TraceTask[]
  taskId: string | null
  onTaskChange: (id: string | null) => void
  analysis?: TraceAnalysis
  loading?: boolean
  capabilities?: TraceCapabilities
  canCreate: boolean
  isSubmitting: boolean
  pendingStopId?: string
  onRefresh?: () => void
  onStart: (draft: InterfaceTraceDraft) => void
  onStop: (task: TraceTask) => void
}

export function NodeTraceView({
  tasks,
  taskId,
  onTaskChange,
  analysis,
  loading,
  capabilities,
  canCreate,
  isSubmitting,
  pendingStopId,
  onRefresh,
  onStart,
  onStop,
}: Props) {
  const catalog = useQuery({
    queryKey: ['trace-node-catalog', '5g-sa'],
    queryFn: async () =>
      (
        await api.get<NodeTraceCatalogItem[]>(
          '/traces/node-catalog/5g-sa'
        )
      ).data,
    staleTime: 300_000,
  })
  const [nodeId, setNodeId] = useState('amf')
  const [interfaceId, setInterfaceId] = useState('n2')
  const [duration, setDuration] = useState('60')
  const [mode, setMode] = useState<'interface' | 'user'>('interface')
  const [query, setQuery] = useState('')
  const [page, setPage] = useState(0)
  const [messageIndex, setMessageIndex] = useState<number | null>(null)
  const [workspace, setWorkspace] = useState<'results' | 'new'>('results')
  const [taskView, setTaskView] = useState<'list' | 'detail'>('list')

  const node = catalog.data?.find((item) => item.id === nodeId)
  const component = capabilities?.network_functions.find(
    (item) => (item.component_id ?? item.id) === nodeId
  )
  const interfaces = useMemo(
    () =>
      (node?.interfaces ?? []).filter((item) =>
        capabilities?.capture_targets.some(
          (target) =>
            target.id === item.id &&
            (target.nf_ids ?? []).includes(nodeId)
        )
      ),
    [capabilities?.capture_targets, node?.interfaces, nodeId]
  )
  const selectedInterface =
    interfaces.find((item) => item.id === interfaceId) ?? interfaces[0]
  const nodeTasks = useMemo(
    () =>
      tasks.filter(
        (task) =>
          task.scenario_id === '5g-sa' &&
          (task.trace_type ?? task.type) === 'interface' &&
          SUPPORTED_NODES.has(task.component_id ?? '')
      ),
    [tasks]
  )
  const selectedTask = nodeTasks.find((task) => task.id === taskId)
  const interfaceHistory = nodeTasks.filter((task) => {
    if (task.component_id !== nodeId) return false
    const subscriberScoped = Boolean(
      task.selector_kind ?? task.identifier_type ?? task.target?.kind
    )
    if (mode === 'user') return subscriberScoped
    return !subscriberScoped && task.capture_point === selectedInterface?.id
  })

  useEffect(() => {
    if (
      selectedTask?.component_id &&
      SUPPORTED_NODES.has(selectedTask.component_id)
    ) {
      // The selected task is controlled by the parent results/history query.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setNodeId(selectedTask.component_id)
      if (selectedTask.capture_point) {
        setInterfaceId(selectedTask.capture_point)
      }
    }
  }, [selectedTask?.capture_point, selectedTask?.component_id])

  const events = useMemo(() => {
    const activeNode = (selectedTask?.component_id ?? nodeId).toLowerCase()
    const activeInterface = selectedTask?.capture_point ?? interfaceId
    const text = query.trim().toLowerCase()
    return (analysis?.events ?? []).filter((event) => {
      const source = endpointName(event.source_nf, event.source).toLowerCase()
      const target = endpointName(event.target_nf, event.target).toLowerCase()
      if (source !== activeNode && target !== activeNode) return false
      if (
        selectedInterface &&
        !selectedInterface.protocols.includes(event.protocol ?? '') &&
        !(activeInterface === 'sbi' && (event.protocol?.startsWith('HTTP') || event.protocol === 'SBI'))
      ) {
        return false
      }
      if (!text) return true
      const identifiers =
        event.identifiers?.map((item) => item.value).join(' ') ?? ''
      return `${event.message} ${event.protocol} ${source} ${target} ${identifiers}`
        .toLowerCase()
        .includes(text)
    })
  }, [analysis?.events, interfaceId, nodeId, query, selectedInterface, selectedTask?.capture_point, selectedTask?.component_id])

  const pageCount = Math.max(1, Math.ceil(events.length / PAGE_SIZE))
  const currentPage = Math.min(page, pageCount - 1)
  const visible = events.slice(
    currentPage * PAGE_SIZE,
    (currentPage + 1) * PAGE_SIZE
  )

  const start = () => {
    if (!component || !selectedInterface || !capabilities) return
    onStart({
      name: `${nodeId.toUpperCase()}-${selectedInterface.id.toUpperCase()}-${clockPart()}`,
      scenario_id: '5g-sa',
      testbed_id: capabilities.testbed_id ?? 'local',
      capture_agent_id: capabilities.capture_agents[0]?.id ?? 'primary',
      node_id: component.node_id,
      component_id: component.component_id ?? component.id,
      capture_point: selectedInterface.id,
      duration_seconds: Number(duration),
      max_megabytes: 25,
      ...(mode === 'user'
        ? { identifier_type: 'imsi' as const, identifier: query.trim() }
        : {}),
    })
    setWorkspace('results')
    setTaskView('detail')
  }

  const selectNode = (value: string) => {
    setNodeId(value)
    const first = catalog.data?.find((item) => item.id === value)?.interfaces[0]
    if (first) setInterfaceId(first.id)
    setQuery('')
    setPage(0)
    onTaskChange(null)
    setWorkspace('results')
    setTaskView('list')
  }

  const selectInterface = (value: string) => {
    setInterfaceId(value)
    setQuery('')
    setPage(0)
    onTaskChange(null)
    setWorkspace('results')
    setTaskView('list')
  }

  return (
    <div className='overflow-hidden rounded-xl border bg-card'>
      <div className='flex flex-wrap items-center gap-2 border-b px-3 py-3'>
        <div className='flex h-9 items-center rounded-md bg-muted p-0.5'>
          <button
            className={`flex h-8 items-center gap-1.5 rounded px-3 text-xs font-medium ${workspace === 'results' ? 'bg-background shadow-sm' : 'text-muted-foreground'}`}
            onClick={() => {
              setWorkspace('results')
              setTaskView('list')
              onTaskChange(null)
            }}
          >
            <ListChecks className='size-3.5' /> Capturas
            {interfaceHistory.length > 0 && (
              <span className='rounded bg-muted px-1.5 py-0.5 text-[10px] tabular-nums'>
                {interfaceHistory.length}
              </span>
            )}
          </button>
          <button
            className={`flex h-8 items-center gap-1.5 rounded px-3 text-xs font-medium ${workspace === 'new' ? 'bg-background shadow-sm' : 'text-muted-foreground'}`}
            onClick={() => setWorkspace('new')}
          >
            <Plus className='size-3.5' /> Nueva traza
          </button>
        </div>

        <div className='ml-auto flex items-center gap-2'>
          {workspace === 'results' && taskView === 'detail' && (
            <>
              {selectedTask && <TaskState task={selectedTask} />}
              {selectedTask && isActiveTrace(selectedTask) && (
                <Button
                  variant='destructive'
                  size='sm'
                  className='h-9'
                  disabled={pendingStopId === selectedTask.id}
                  onClick={() => onStop(selectedTask)}
                >
                  {pendingStopId === selectedTask.id ? (
                    <Loader2 className='animate-spin' />
                  ) : (
                    <Square />
                  )}
                  Detener
                </Button>
              )}
            </>
          )}
        </div>
      </div>

      <div className='grid min-h-[600px] grid-cols-[210px_minmax(0,1fr)]'>
        <aside className='border-r bg-muted/10 p-2'>
          <Select value={nodeId} onValueChange={selectNode}>
            <SelectTrigger className='mb-2 h-9 w-full bg-background'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {catalog.data?.map((item) => (
                <SelectItem key={item.id} value={item.id}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <button
            className={`flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm ${mode === 'interface' ? 'bg-accent font-medium' : 'hover:bg-muted'}`}
            onClick={() => {
              setMode('interface')
              setQuery('')
              setPage(0)
              onTaskChange(null)
              setTaskView('list')
            }}
          >
            <Network className='size-4' /> Interface Trace
          </button>
          <div className='ml-5 border-l pl-2'>
            {interfaces.map((item) => (
              <button
                key={item.id}
                className={`mt-1 flex w-full items-center gap-1 rounded px-2 py-1.5 text-left text-xs ${selectedInterface?.id === item.id ? 'bg-accent text-foreground' : 'text-muted-foreground hover:bg-muted'}`}
                onClick={() => {
                  setMode('interface')
                  selectInterface(item.id)
                }}
              >
                <ChevronRight className='size-3' />
                {shortInterface(item.label)}
              </button>
            ))}
          </div>
          <button
            className={`mt-3 flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm ${mode === 'user' ? 'bg-accent font-medium' : 'hover:bg-muted'}`}
            onClick={() => {
              setMode('user')
              setQuery('')
              setPage(0)
              onTaskChange(null)
              setWorkspace('results')
              setTaskView('list')
            }}
          >
            <UserRoundSearch className='size-4' /> User Trace
          </button>
        </aside>

        <section className='min-w-0'>
          {workspace === 'new' ? (
            <div className='flex min-h-[600px] flex-col'>
              <div className='flex h-12 items-center gap-3 border-b px-5'>
                <span className='text-sm font-semibold'>Nueva tarea</span>
                <span className='text-xs text-muted-foreground'>
                  {mode === 'user' ? 'User Trace' : 'Interface Trace'}
                  <span className='px-1.5'>·</span>
                  {node?.label ?? nodeId.toUpperCase()}
                  <span className='px-1.5'>/</span>
                  {selectedInterface
                    ? shortInterface(selectedInterface.label)
                    : 'Interfaz'}
                </span>
              </div>
              <div className='flex flex-wrap items-end gap-4 border-b bg-muted/5 p-5'>
                <div className='w-40 space-y-1.5'>
                  <div className='text-[11px] font-medium uppercase tracking-wide text-muted-foreground'>
                    Duración
                  </div>
                  <Select value={duration} onValueChange={setDuration}>
                    <SelectTrigger className='h-10 w-full'>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value='30'>30 segundos</SelectItem>
                      <SelectItem value='60'>1 minuto</SelectItem>
                      <SelectItem value='120'>2 minutos</SelectItem>
                      <SelectItem value='300'>5 minutos</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                {mode === 'user' && (
                  <div className='min-w-64 flex-1 space-y-1.5'>
                    <div className='text-[11px] font-medium uppercase tracking-wide text-muted-foreground'>
                      IMSI / SUPI
                    </div>
                    <Input
                      className='h-10 max-w-md font-mono'
                      placeholder='999700000000001'
                      maxLength={15}
                      value={query}
                      onChange={(event) =>
                        setQuery(event.target.value.replace(/\D/g, ''))
                      }
                    />
                  </div>
                )}
                <Button
                  className='h-10 min-w-36'
                  disabled={
                    !canCreate ||
                    !component ||
                    !selectedInterface ||
                    isSubmitting ||
                    (mode === 'user' && !/^\d{14,15}$/.test(query.trim()))
                  }
                  onClick={start}
                >
                  {isSubmitting ? <Loader2 className='animate-spin' /> : <Play />}
                  Iniciar traza
                </Button>
              </div>
            </div>
          ) : taskView === 'list' ? (
            <div className='min-h-[600px]'>
              <div className='flex h-12 items-center border-b px-4'>
                <div>
                  <div className='text-sm font-semibold'>Lista de tareas</div>
                  <div className='text-[11px] text-muted-foreground'>
                    {node?.label ?? nodeId.toUpperCase()}
                    <span className='px-1.5'>/</span>
                    {mode === 'user'
                      ? 'User Trace'
                      : selectedInterface
                        ? shortInterface(selectedInterface.label)
                        : 'Interface Trace'}
                  </div>
                </div>
                <span className='ml-auto text-xs tabular-nums text-muted-foreground'>
                  {interfaceHistory.length}{' '}
                  {interfaceHistory.length === 1 ? 'tarea' : 'tareas'}
                </span>
              </div>
              <div className='max-h-[548px] overflow-auto'>
                <Table>
                  <TableHeader className='sticky top-0 z-10 bg-card'>
                    <TableRow>
                      <TableHead>Tarea</TableHead>
                      <TableHead className='w-28'>Tipo</TableHead>
                      <TableHead className='w-36'>Inicio</TableHead>
                      <TableHead className='w-24'>Duración</TableHead>
                      <TableHead className='w-28'>Estado</TableHead>
                      <TableHead className='w-24 text-right'>Paquetes</TableHead>
                      <TableHead className='w-20 text-right'>Acción</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {interfaceHistory.map((task) => (
                      <TableRow
                        key={task.id}
                        className='cursor-pointer hover:bg-muted/50'
                        onClick={() => {
                          if (task.component_id) setNodeId(task.component_id)
                          if (task.capture_point) setInterfaceId(task.capture_point)
                          onTaskChange(task.id)
                          setPage(0)
                          setTaskView('detail')
                          onRefresh?.()
                        }}
                      >
                        <TableCell>
                          <div className='flex items-center gap-2'>
                            <span
                              className={`size-1.5 shrink-0 rounded-full ${taskStateDot(task)}`}
                            />
                            <span className='font-medium text-primary'>
                              {task.name ?? task.id.slice(0, 8)}
                            </span>
                          </div>
                        </TableCell>
                        <TableCell className='text-xs text-muted-foreground'>
                          {mode === 'user'
                            ? 'User Trace'
                            : shortInterface(
                                task.capture_point_label ??
                                  task.capture_point ??
                                  'Interfaz'
                              )}
                        </TableCell>
                        <TableCell className='text-xs text-muted-foreground'>
                          {formatTaskDate(task.created_at)}
                        </TableCell>
                        <TableCell className='text-xs text-muted-foreground'>
                          {formatDuration(task.duration_seconds)}
                        </TableCell>
                        <TableCell>
                          <TaskState task={task} />
                        </TableCell>
                        <TableCell className='text-right font-mono text-xs'>
                          {task.packet_count ?? 0}
                        </TableCell>
                        <TableCell className='text-right text-xs font-medium text-primary'>
                          Abrir
                        </TableCell>
                      </TableRow>
                    ))}
                    {!interfaceHistory.length && (
                      <TableRow>
                        <TableCell colSpan={7} className='h-64 text-center'>
                          <span className='text-sm text-muted-foreground'>
                            No hay tareas para este tipo de traza.
                          </span>
                        </TableCell>
                      </TableRow>
                    )}
                  </TableBody>
                </Table>
              </div>
            </div>
          ) : (
          <>
          <div className='flex h-11 items-center border-b px-4 text-xs text-muted-foreground'>
            <button
              type='button'
              className='mr-3 flex items-center gap-1 font-medium text-foreground hover:text-primary'
              onClick={() => {
                setTaskView('list')
                onTaskChange(null)
              }}
            >
              <ArrowLeft className='size-3.5' /> Tareas
            </button>
            <span className='mr-3 h-4 w-px bg-border' />
            <span>{node?.label ?? nodeId.toUpperCase()}</span>
            <span className='px-2'>/</span>
            <span>
              {selectedInterface
                ? shortInterface(selectedInterface.label)
                : 'Interfaz'}
            </span>
            <span className='ml-auto flex items-center gap-2'>
              {onRefresh && (
                <Button
                  variant='ghost'
                  size='icon'
                  className='size-6 text-muted-foreground hover:text-foreground'
                  title='Actualizar mensajes'
                  onClick={() => onRefresh()}
                >
                  <RefreshCw className={`size-3.5 ${loading ? 'animate-spin' : ''}`} />
                </Button>
              )}
              <span>{events.length} mensajes</span>
            </span>
          </div>
          <div className='max-h-[520px] overflow-auto'>
            <Table>
              <TableHeader className='sticky top-0 z-10 bg-card'>
                <TableRow>
                  <TableHead className='w-16'>N.º</TableHead>
                  <TableHead className='w-32'>Hora</TableHead>
                  <TableHead className='w-44'>Ruta</TableHead>
                  <TableHead className='w-24'>Dirección</TableHead>
                  <TableHead>Mensaje</TableHead>
                  <TableHead className='w-28'>Protocolo</TableHead>
                  <TableHead className='w-20 text-right'>Bytes</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {visible.map((event, rowIndex) => {
                  const absoluteIndex = currentPage * PAGE_SIZE + rowIndex
                  const source = endpointName(event.source_nf, event.source)
                  const target = endpointName(event.target_nf, event.target)
                  return (
                    <TableRow
                      key={event.id}
                      className='cursor-pointer'
                      onClick={() => setMessageIndex(absoluteIndex)}
                    >
                      <TableCell className='font-mono text-xs'>
                        {event.ordinal ?? absoluteIndex + 1}
                      </TableCell>
                      <TableCell className='font-mono text-xs'>
                        {formatTimestamp(event.timestamp)}
                      </TableCell>
                      <TableCell>
                        <span className='whitespace-nowrap font-mono text-xs font-medium'>
                          {source} → {target}
                        </span>
                      </TableCell>
                      <TableCell>
                        <DirectionBadge
                          nodeId={nodeId}
                          source={source}
                          target={target}
                        />
                      </TableCell>
                      <TableCell className='max-w-[460px]'>
                        <div className='truncate text-sm font-medium'>
                          {event.message}
                        </div>
                        {mode === 'user' && subscriberFor(event) !== '—' && (
                          <div className='mt-0.5 font-mono text-[11px] text-muted-foreground'>
                            {subscriberFor(event)}
                          </div>
                        )}
                      </TableCell>
                      <TableCell className='text-xs text-muted-foreground'>
                        {event.protocol ?? '—'}
                      </TableCell>
                      <TableCell className='text-right font-mono text-xs'>
                        {event.length_bytes || '—'}
                      </TableCell>
                    </TableRow>
                  )
                })}
                {!visible.length && (
                  <TableRow>
                    <TableCell colSpan={7} className='h-64 text-center'>
                      <EmptyState task={selectedTask} loading={loading} />
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </div>
          {events.length > PAGE_SIZE && (
            <div className='flex items-center justify-end gap-2 border-t px-4 py-2 text-xs'>
              <span className='mr-auto text-muted-foreground'>
                {currentPage + 1} / {pageCount}
              </span>
              <Button
                variant='outline'
                size='sm'
                disabled={currentPage === 0}
                onClick={() => setPage(currentPage - 1)}
              >
                Anterior
              </Button>
              <Button
                variant='outline'
                size='sm'
                disabled={currentPage + 1 >= pageCount}
                onClick={() => setPage(currentPage + 1)}
              >
                Siguiente
              </Button>
            </div>
          )}
          </>
          )}
        </section>
      </div>

      {taskId && (
        <MessageCodeStreamsModal
          taskId={taskId}
          events={events}
          index={messageIndex}
          onIndexChange={setMessageIndex}
          onClose={() => setMessageIndex(null)}
        />
      )}
    </div>
  )
}

function DirectionBadge({
  nodeId,
  source,
  target,
}: {
  nodeId: string
  source: string
  target: string
}) {
  const normalized = nodeId.toLowerCase()
  const direction =
    source.toLowerCase() === normalized && target.toLowerCase() === normalized
      ? 'Interno'
      : source.toLowerCase() === normalized
        ? 'Saliente'
        : 'Entrante'
  const style =
    direction === 'Saliente'
      ? 'border-sky-500/30 bg-sky-500/10 text-sky-600 dark:text-sky-400'
      : direction === 'Interno'
        ? 'border-fuchsia-500/30 bg-fuchsia-500/10 text-fuchsia-600 dark:text-fuchsia-400'
        : 'border-slate-500/30 bg-slate-950 text-white dark:bg-slate-100 dark:text-slate-950'
  return <Badge className={style}>{direction}</Badge>
}

function TaskState({ task }: { task: TraceTask }) {
  const active = isActiveTrace(task)
  const noTraffic = (task.outcome ?? task.result) === 'no_traffic'
  return (
    <Badge
      variant='outline'
      className={
        active
          ? 'border-emerald-500/30 text-emerald-600'
          : noTraffic
            ? 'border-amber-500/30 text-amber-600'
            : ''
      }
    >
      <span
        className={`mr-1.5 size-1.5 rounded-full ${active ? 'animate-pulse bg-emerald-500' : 'bg-muted-foreground'}`}
      />
      {taskStateLabel(task)}
    </Badge>
  )
}

function EmptyState({
  task,
  loading,
}: {
  task?: TraceTask
  loading?: boolean
}) {
  if (loading) {
    return (
      <span className='text-sm text-muted-foreground'>
        Procesando mensajes…
      </span>
    )
  }
  if (task && isActiveTrace(task)) {
    return (
      <span className='text-sm text-muted-foreground'>
        Captura activa. Los mensajes estarán disponibles al detenerla.
      </span>
    )
  }
  if (task) {
    const noTraffic = (task.outcome ?? task.result) === 'no_traffic'
    return (
      <div className='space-y-1 text-sm text-muted-foreground'>
        <div>
          {noTraffic
            ? 'Captura finalizada sin paquetes.'
            : 'No se observaron mensajes en esta interfaz.'}
        </div>
        {noTraffic && (
          <div className='text-xs'>
            Inicie la captura antes de generar señalización en la interfaz.
          </div>
        )}
      </div>
    )
  }
  return (
    <span className='text-sm text-muted-foreground'>
      Seleccione una interfaz e inicie la traza.
    </span>
  )
}

function taskStateLabel(task: TraceTask) {
  if (isActiveTrace(task)) return 'Capturando'
  if ((task.outcome ?? task.result) === 'no_traffic') return 'Sin tráfico'
  if (task.status === 'completed') return 'Finalizada'
  return task.status
}

function taskStateDot(task: TraceTask) {
  if (isActiveTrace(task)) return 'animate-pulse bg-emerald-500'
  if ((task.outcome ?? task.result) === 'no_traffic') return 'bg-amber-500'
  if (task.status === 'completed') return 'bg-sky-500'
  if (task.status === 'failed') return 'bg-red-500'
  return 'bg-muted-foreground'
}

function formatTaskDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('es-PE', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

function formatDuration(seconds?: number) {
  if (!seconds) return '—'
  if (seconds < 60) return `${seconds} s`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return remainder ? `${minutes} min ${remainder} s` : `${minutes} min`
}

function endpointName(
  explicit: string | undefined,
  endpoint: TraceEvent['source'] | TraceEvent['target']
) {
  const value = explicit
    ? explicit
    : typeof endpoint === 'string'
      ? endpoint
      : endpoint?.nf ?? endpoint?.label ?? endpoint?.id ?? '—'
  if (value === '127.0.0.1' || value === 'localhost') return 'Proceso local'
  if (value === '5GC NF') return 'NF local'
  return value
}

function subscriberFor(event: TraceEvent) {
  return (
    event.identifiers?.find((item) =>
      ['supi', 'imsi'].includes(item.kind ?? item.type ?? '')
    )?.value ?? '—'
  )
}

function shortInterface(label: string) {
  const serviceName = label.match(/\(([^)]+)\)/)?.[1]
  if (serviceName) return serviceName
  return label
    .replace(/\s*Interface Trace\s*/i, '')
    .replace(/^HTTP\s*/i, '')
    .trim()
}

function formatTimestamp(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const time = date.toLocaleTimeString('es-PE', { hour12: false })
  return `${time}.${String(date.getMilliseconds()).padStart(3, '0')}`
}

function clockPart() {
  const now = new Date()
  return [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((value) => String(value).padStart(2, '0'))
    .join('')
}
