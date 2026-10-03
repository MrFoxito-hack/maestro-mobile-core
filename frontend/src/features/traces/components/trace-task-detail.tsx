import { useMemo, useState } from 'react'
import {
  Activity,
  ArrowLeft,
  Braces,
  CheckCircle2,
  Download,
  FileJson,
  Fingerprint,
  GitBranch,
  Maximize2,
  Minimize2,
  Network,
  ShieldAlert,
  Square,
  Trash2,
  Wrench,
} from 'lucide-react'
import type { Role } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { formatBytes, formatDuration, formatTimestamp } from '../format'
import {
  isActiveTrace,
  traceKind,
  type TraceAnalysis,
  type TraceArtifact,
  type TraceDiagnostic,
  type TraceEvent,
  type TraceIdentifier,
  type TraceTask,
} from '../types'
import { SequenceDiagram } from './sequence-diagram'
import {
  EmptyState,
  EvidenceBadge,
  ProcedureStatusIcon,
  TraceOutcomeBadge,
  TraceStatusBadge,
} from './trace-status'
import { TraceTimeline } from './trace-timeline'

type TraceTaskDetailProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  task?: TraceTask
  analysis?: TraceAnalysis
  isLoading: boolean
  analysisError?: Error | null
  role?: Role
  currentUsername?: string
  isStopping?: boolean
  isDeleting?: boolean
  onStop: (task: TraceTask) => void
  onDelete: (task: TraceTask) => void
  onDownload: (
    task: TraceTask,
    artifact: 'original' | 'filtered' | 'evidence'
  ) => void
}

export function TraceTaskDetail({
  open,
  onOpenChange,
  task,
  analysis,
  isLoading,
  analysisError,
  role,
  currentUsername,
  isStopping,
  isDeleting,
  onStop,
  onDelete,
  onDownload,
}: TraceTaskDetailProps) {
  const [selectedEvent, setSelectedEvent] = useState<TraceEvent | undefined>()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const canControl =
    !!task &&
    (role === 'admin' ||
      role === 'teacher' ||
      (!!task.owner && task.owner === currentUsername))
  const events = analysis?.events ?? []
  const identifiers = useMemo(
    () => buildIdentifierChain(task, analysis?.identifiers),
    [analysis?.identifiers, task]
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        onPointerDownOutside={(e) => e.preventDefault()}
        onInteractOutside={(e) => e.preventDefault()}
        className={cn(
          'flex flex-col gap-0 p-0 transition-all duration-150',
          isFullscreen
            ? '!fixed !inset-0 !top-0 !left-0 !z-50 !h-screen !max-h-screen !w-screen !max-w-none !translate-x-0 !translate-y-0 !transform-none overflow-hidden !rounded-none !border-none'
            : 'h-[94vh] max-h-[94vh] w-[calc(100%-1rem)] max-w-none overflow-hidden sm:max-w-[min(1600px,calc(100%-2rem))]'
        )}
      >
        <DialogHeader className='shrink-0 border-b px-5 py-4 pr-12'>
          <div className='flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between'>
            <div className='min-w-0'>
              <div className='flex flex-wrap items-center gap-2'>
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() => setIsFullscreen((prev) => !prev)}
                  className='gap-1.5'
                  title={
                    isFullscreen
                      ? 'Salir de pantalla completa'
                      : 'Ver en pantalla completa'
                  }
                >
                  {isFullscreen ? (
                    <Minimize2 className='h-4 w-4' />
                  ) : (
                    <Maximize2 className='h-4 w-4' />
                  )}
                  <span className='hidden sm:inline'>
                    {isFullscreen ? 'Salir' : 'Pantalla completa'}
                  </span>
                </Button>
                <DialogTitle className='truncate text-xl'>
                  {task?.name ?? 'Detalle de tarea'}
                </DialogTitle>
                {task && <TraceStatusBadge status={task.status} />}
                {task && (
                  <TraceOutcomeBadge
                    outcome={analysis?.outcome ?? task.outcome ?? task.result}
                  />
                )}
              </div>
              <DialogDescription className='mt-1 flex flex-wrap items-center gap-x-2 gap-y-1'>
                <span className='font-mono'>
                  {task ? shortId(task.id) : 'Consultando…'}
                </span>
                {task?.owner && (
                  <>
                    <span>·</span>
                    <span>creada por {task.owner}</span>
                  </>
                )}
                {task?.created_at && (
                  <>
                    <span>·</span>
                    <span>{formatTimestamp(task.created_at)}</span>
                  </>
                )}
              </DialogDescription>
            </div>
            {task && (
              <div className='flex flex-wrap items-center gap-2'>
                {isActiveTrace(task) && canControl && (
                  <Button
                    variant='destructive'
                    size='sm'
                    disabled={isStopping}
                    onClick={() => onStop(task)}
                  >
                    <Square /> {isStopping ? 'Deteniendo…' : 'Detener'}
                  </Button>
                )}
                {!isActiveTrace(task) && (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant='outline' size='sm'>
                        <Download /> Descargar
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align='end'>
                      <DropdownMenuItem
                        onSelect={() => onDownload(task, 'original')}
                      >
                        <Download /> PCAP original
                      </DropdownMenuItem>
                      {traceKind(task) === 'subscriber' && (
                        <>
                          <DropdownMenuItem
                            onSelect={() => onDownload(task, 'filtered')}
                          >
                            <Download /> PCAP filtrado
                          </DropdownMenuItem>
                          <DropdownMenuItem
                            onSelect={() => onDownload(task, 'evidence')}
                          >
                            <FileJson /> Evidencia JSON
                          </DropdownMenuItem>
                        </>
                      )}
                    </DropdownMenuContent>
                  </DropdownMenu>
                )}
                {!isActiveTrace(task) && canControl && (
                  <Button
                    variant='ghost'
                    size='icon'
                    aria-label='Eliminar tarea'
                    onClick={() => setDeleteOpen(true)}
                  >
                    <Trash2 className='text-destructive' />
                  </Button>
                )}
              </div>
            )}
          </div>
        </DialogHeader>

        <ScrollArea className='min-h-0 flex-1'>
          <div className='space-y-5 p-4 sm:p-6'>
            {isLoading || !task ? (
              <DetailSkeleton />
            ) : (
              <>
                <TraceSummaryBar task={task} analysis={analysis} />

                {traceKind(task) === 'subscriber' && (
                  <IdentifierChain
                    identifiers={identifiers}
                    status={analysis?.correlation_status}
                  />
                )}

                {analysisError && (
                  <Alert variant='destructive'>
                    <ShieldAlert />
                    <AlertTitle>El análisis no está disponible</AlertTitle>
                    <AlertDescription>
                      {analysisError.message}. El PCAP original y los metadatos
                      de captura siguen disponibles.
                    </AlertDescription>
                  </Alert>
                )}

                <Tabs defaultValue='overview' className='gap-4'>
                  <div className='overflow-x-auto pb-1'>
                    <TabsList>
                      <TabsTrigger value='overview'>
                        <Activity /> Resumen
                      </TabsTrigger>
                      <TabsTrigger value='timeline'>
                        <GitBranch /> Timeline{' '}
                        <Badge variant='secondary'>{events.length}</Badge>
                      </TabsTrigger>
                      <TabsTrigger value='sequence'>
                        <Network /> Secuencia
                      </TabsTrigger>
                      <TabsTrigger value='evidence'>
                        <Braces /> Evidencia
                      </TabsTrigger>
                    </TabsList>
                  </div>
                  <TabsContent value='overview'>
                    <Overview task={task} analysis={analysis} />
                  </TabsContent>
                  <TabsContent value='timeline'>
                    <TraceTimeline
                      events={events}
                      selectedEventId={selectedEvent?.id}
                      onSelectEvent={setSelectedEvent}
                    />
                  </TabsContent>
                  <TabsContent value='sequence'>
                    <SequenceDiagram
                      events={events}
                      participants={analysis?.participants}
                      selectedEventId={selectedEvent?.id}
                      onSelectEvent={setSelectedEvent}
                    />
                    {selectedEvent && (
                      <SelectedEventCard
                        event={selectedEvent}
                        onOpenTimeline={() => undefined}
                      />
                    )}
                  </TabsContent>
                  <TabsContent value='evidence'>
                    <EvidenceView
                      task={task}
                      analysis={analysis}
                      onDownload={onDownload}
                    />
                  </TabsContent>
                </Tabs>
              </>
            )}
          </div>
        </ScrollArea>
      </DialogContent>

      {task && (
        <ConfirmDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          title='Eliminar tarea y artefactos'
          desc='Se eliminarán los PCAP, eventos correlacionados y archivos de evidencia asociados. Esta acción no puede deshacerse.'
          confirmText='Eliminar definitivamente'
          destructive
          isLoading={isDeleting}
          handleConfirm={() => {
            onDelete(task)
            setDeleteOpen(false)
          }}
        />
      )}
    </Dialog>
  )
}

function TraceSummaryBar({
  task,
  analysis,
}: {
  task: TraceTask
  analysis?: TraceAnalysis
}) {
  const mode =
    traceKind(task) === 'subscriber' ? 'Subscriber Trace' : 'Interface Trace'
  const targetKind =
    task.target?.kind ??
    analysis?.target?.kind ??
    task.identifier_type ??
    'IMSI'
  const targetValue =
    task.target?.masked ??
    analysis?.target?.masked ??
    task.identifier_masked ??
    task.identifier
  const target =
    traceKind(task) === 'subscriber'
      ? `${targetKind.toUpperCase()} ${maskIdentifier(targetValue, targetKind)}`
      : (task.capture_point_label ??
        task.capture_point?.toUpperCase() ??
        'Punto de captura')
  return (
    <div className='flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border bg-card px-3 py-2 text-xs shadow-xs'>
      <span className='font-semibold'>{mode}</span>
      <span className='max-w-52 truncate font-mono text-muted-foreground'>
        {target}
      </span>
      <span className='hidden h-4 w-px bg-border sm:block' />
      <span
        title={`${formatTimestamp(task.started_at ?? task.created_at)} - ${formatTimestamp(task.completed_at)}`}
      >
        <strong>{formatDuration(task.duration_seconds)}</strong>
      </span>
      <span className='text-muted-foreground'>
        <strong className='text-foreground'>{task.packet_count ?? 0}</strong>{' '}
        paquetes
      </span>
      <span className='text-muted-foreground'>
        <strong className='text-foreground'>
          {analysis?.events?.length ?? task.event_count ?? 0}
        </strong>{' '}
        eventos
      </span>
      <span className='text-muted-foreground'>
        {formatBytes(task.size_bytes)}
      </span>
      <Badge variant='outline' className='ml-auto gap-1'>
        <Fingerprint className='size-3' />
        IDs: {correlationLabel(analysis?.correlation_status)}
      </Badge>
    </div>
  )
}

function IdentifierChain({
  identifiers,
  status,
}: {
  identifiers: TraceIdentifier[]
  status?: string
}) {
  if (!identifiers.length) {
    return (
      <div className='rounded-lg border bg-card px-3 py-2 text-xs text-muted-foreground'>
        <Fingerprint className='mr-2 inline size-3.5' />
        Identificadores aún no correlacionados
      </div>
    )
  }
  const primary = identifiers[0]
  return (
    <details className='group rounded-lg border bg-card shadow-xs'>
      <summary className='flex cursor-pointer list-none flex-wrap items-center gap-2 px-3 py-2 text-xs select-none'>
        <Fingerprint className='size-3.5 text-primary' />
        <span className='font-semibold'>Identificadores correlacionados</span>
        <span className='font-mono text-muted-foreground'>
          {primary.label ?? identifierLabel(primary.kind ?? primary.type)}{' '}
          {maskIdentifier(
            primary.masked_value ?? primary.value,
            primary.kind ?? primary.type
          )}
        </span>
        <Badge variant='secondary'>{identifiers.length}</Badge>
        <Badge
          variant='outline'
          className={cn(
            'ml-auto',
            status === 'complete' && 'border-emerald-500/40 text-emerald-600'
          )}
        >
          {correlationLabel(status)}
        </Badge>
        <span className='text-muted-foreground group-open:hidden'>Mostrar</span>
        <span className='hidden text-muted-foreground group-open:inline'>
          Ocultar
        </span>
      </summary>
      <div className='border-t px-3 pt-3'>
        <div className='flex items-stretch gap-2 overflow-x-auto pb-2'>
          {identifiers.map((identifier, index) => (
            <div
              key={`${identifier.kind ?? identifier.type}:${index}`}
              className='flex shrink-0 items-center gap-2'
            >
              <div className='min-w-36 rounded-lg border bg-muted/20 p-3'>
                <div className='flex items-center justify-between gap-2'>
                  <p className='text-[10px] font-semibold tracking-wide text-muted-foreground uppercase'>
                    {identifier.label ??
                      identifierLabel(identifier.kind ?? identifier.type)}
                  </p>
                  <EvidenceBadge
                    type={identifier.evidence_type ?? identifier.source}
                  />
                </div>
                <p className='mt-1.5 font-mono text-xs font-medium'>
                  {maskIdentifier(
                    identifier.masked_value ?? identifier.value,
                    identifier.kind ?? identifier.type
                  )}
                </p>
                {identifier.confidence && (
                  <p className='mt-1 text-[10px] text-muted-foreground'>
                    {confidenceLabel(identifier.confidence)}
                  </p>
                )}
              </div>
              {index < identifiers.length - 1 && (
                <span
                  className={cn(
                    'h-px w-7 bg-border',
                    identifier.confidence === 'inferred' &&
                      'border-t border-dashed border-amber-500 bg-transparent'
                  )}
                />
              )}
            </div>
          ))}
        </div>
      </div>
    </details>
  )
}

function Overview({
  task,
  analysis,
}: {
  task: TraceTask
  analysis?: TraceAnalysis
}) {
  const procedures = analysis?.procedures ?? []
  const diagnostics = analysis?.diagnostics ?? []
  return (
    <div className='grid gap-4 xl:grid-cols-[1.25fr_.75fr]'>
      <div className='space-y-4'>
        <Card>
          <CardHeader>
            <CardTitle className='text-base'>Procedimientos 3GPP</CardTitle>
          </CardHeader>
          <CardContent>
            {!procedures.length ? (
              <EmptyState
                title='Resultado aún no disponible'
                description={
                  isActiveTrace(task)
                    ? 'La tarea continúa capturando o procesando evidencia.'
                    : 'No se encontraron suficientes eventos para clasificar un procedimiento.'
                }
              />
            ) : (
              <div className='space-y-3'>
                {procedures.map((procedure, index) => (
                  <div
                    key={procedure.id ?? procedure.type ?? index}
                    className='flex items-start gap-3 rounded-lg border p-3'
                  >
                    <ProcedureStatusIcon status={procedure.status} />
                    <div className='min-w-0 flex-1'>
                      <div className='flex flex-wrap items-center justify-between gap-2'>
                        <p className='font-medium'>
                          {procedure.label ??
                            procedure.name ??
                            procedure.type ??
                            'Procedimiento'}
                        </p>
                        <Badge variant='outline'>
                          {procedure.status ?? 'pendiente'}
                        </Badge>
                      </div>
                      <div className='mt-1 flex flex-wrap gap-x-3 text-xs text-muted-foreground'>
                        {procedure.duration_ms != null && (
                          <span>{procedure.duration_ms.toFixed(0)} ms</span>
                        )}
                        {procedure.interface && (
                          <span>Interfaz {procedure.interface}</span>
                        )}
                        {procedure.affected_nf && (
                          <span>NF {procedure.affected_nf.toUpperCase()}</span>
                        )}
                      </div>
                      {procedure.last_successful_step && (
                        <p className='mt-2 text-xs'>
                          Último paso confirmado:{' '}
                          {procedure.last_successful_step}
                        </p>
                      )}
                      {procedure.failure_cause && (
                        <p className='mt-2 text-xs text-destructive'>
                          {procedure.failure_cause.domain}{' '}
                          {procedure.failure_cause.code}:{' '}
                          {procedure.failure_cause.text}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
        {!!analysis?.summary && (
          <Alert>
            <CheckCircle2 />
            <AlertTitle>Conclusión del motor de trazabilidad</AlertTitle>
            <AlertDescription>{analysis.summary}</AlertDescription>
          </Alert>
        )}
      </div>
      <div className='space-y-4'>
        <Card>
          <CardHeader>
            <CardTitle className='text-base'>Ámbito técnico</CardTitle>
          </CardHeader>
          <CardContent className='space-y-3 text-sm'>
            <MetadataRow
              label='Escenario'
              value={task.scenario_id ?? 'No indicado'}
            />
            <MetadataRow label='Testbed' value={task.testbed_id ?? 'local'} />
            <MetadataRow
              label='Agente'
              value={
                task.capture_agent_label ??
                task.capture_agent_id ??
                task.source ??
                'primary'
              }
            />
            <MetadataRow
              label='Nodo'
              value={task.node_id ?? 'Ámbito automático'}
            />
            <MetadataRow
              label='NF'
              value={
                task.component_label ??
                task.component_id?.toUpperCase() ??
                'Varias NFs'
              }
            />
            <MetadataRow
              label='Interfaz'
              value={
                task.capture_point_label ??
                task.capture_point?.toUpperCase() ??
                'Multipunto'
              }
            />
            <MetadataRow
              label='Propietario'
              value={task.owner ?? 'No registrado'}
            />
          </CardContent>
        </Card>
        <Diagnostics diagnostics={diagnostics} />
      </div>
    </div>
  )
}

function Diagnostics({ diagnostics }: { diagnostics: TraceDiagnostic[] }) {
  if (!diagnostics.length) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle className='text-base'>Diagnóstico contextual</CardTitle>
      </CardHeader>
      <CardContent className='space-y-3'>
        {diagnostics.map((item, index) => (
          <Alert
            key={item.id ?? index}
            variant={
              ['critical', 'major'].includes(item.severity ?? '')
                ? 'destructive'
                : 'default'
            }
          >
            <ShieldAlert />
            <AlertTitle>{item.title}</AlertTitle>
            <AlertDescription>
              {(item.description || item.detail) && (
                <p>{item.description ?? item.detail}</p>
              )}
              {item.evidence && (
                <p className='font-mono text-xs'>{item.evidence}</p>
              )}
              {item.recommendation && (
                <p>
                  <strong>Recomendación:</strong> {item.recommendation}
                </p>
              )}
            </AlertDescription>
          </Alert>
        ))}
      </CardContent>
    </Card>
  )
}

function EvidenceView({
  task,
  analysis,
  onDownload,
}: {
  task: TraceTask
  analysis?: TraceAnalysis
  onDownload: TraceTaskDetailProps['onDownload']
}) {
  const artifacts = analysis?.artifacts ?? task.artifacts ?? []
  return (
    <div className='grid gap-4 xl:grid-cols-[1fr_.8fr]'>
      <Card>
        <CardHeader>
          <CardTitle className='text-base'>Artefactos verificables</CardTitle>
        </CardHeader>
        <CardContent className='space-y-3'>
          <ArtifactRow
            kind='original'
            label='PCAP original'
            description='Captura íntegra obtenida del agente remoto.'
            task={task}
            artifacts={artifacts}
            onDownload={onDownload}
          />
          {traceKind(task) === 'subscriber' && (
            <>
              <ArtifactRow
                kind='filtered'
                label='PCAP del suscriptor'
                description='Paquetes asociados por identificadores confirmados.'
                task={task}
                artifacts={artifacts}
                onDownload={onDownload}
              />
              <ArtifactRow
                kind='evidence'
                label='Evidencia estructurada'
                description='Eventos, relaciones, causas y procedencia en JSON.'
                task={task}
                artifacts={artifacts}
                onDownload={onDownload}
              />
            </>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className='text-base'>
            Trazabilidad de la evidencia
          </CardTitle>
        </CardHeader>
        <CardContent className='space-y-4 text-sm'>
          <p className='text-muted-foreground'>
            Cada evento conserva su origen para distinguir observación directa e
            inferencia del correlador.
          </p>
          <EvidenceLegend
            type='pcap'
            title='PCAP'
            description='Campo decodificado directamente de una trama.'
          />
          <EvidenceLegend
            type='log'
            title='LOG'
            description='Evento emitido por Open5GS o UERANSIM.'
          />
          <EvidenceLegend
            type='runtime'
            title='RUNTIME'
            description='Estado consultado en el nodo durante la tarea.'
          />
          <EvidenceLegend
            type='correlated'
            title='CORRELACIONADO'
            description='Relación determinística o inferida entre evidencias.'
          />
          <Separator />
          <p className='text-xs text-muted-foreground'>
            Las relaciones inferidas se dibujan con línea discontinua y nunca se
            presentan como paquetes observados.
          </p>
        </CardContent>
      </Card>
    </div>
  )
}

function ArtifactRow({
  kind,
  label,
  description,
  task,
  artifacts,
  onDownload,
}: {
  kind: 'original' | 'filtered' | 'evidence'
  label: string
  description: string
  task: TraceTask
  artifacts: TraceArtifact[]
  onDownload: TraceTaskDetailProps['onDownload']
}) {
  const artifact = artifacts.find((item) => (item.kind ?? item.id) === kind)
  const available = artifact
    ? artifact.available !== false
    : kind === 'original' || traceKind(task) === 'subscriber'
  return (
    <div className='flex items-center justify-between gap-3 rounded-lg border p-3'>
      <div className='min-w-0'>
        <p className='font-medium'>{artifact?.label ?? label}</p>
        <p className='mt-1 text-xs text-muted-foreground'>{description}</p>
        {artifact?.sha256 && (
          <p className='mt-1 truncate font-mono text-[10px] text-muted-foreground'>
            SHA-256 {artifact.sha256}
          </p>
        )}
      </div>
      <Button
        variant='outline'
        size='sm'
        disabled={!available}
        onClick={() => onDownload(task, kind)}
      >
        <Download />{' '}
        {artifact?.size_bytes != null
          ? formatBytes(artifact.size_bytes)
          : 'Descargar'}
      </Button>
    </div>
  )
}

function EvidenceLegend({
  type,
  title,
  description,
}: {
  type: 'pcap' | 'log' | 'runtime' | 'correlated'
  title: string
  description: string
}) {
  return (
    <div className='flex items-start gap-3'>
      <EvidenceBadge type={type} />
      <div>
        <p className='font-medium'>{title}</p>
        <p className='text-xs text-muted-foreground'>{description}</p>
      </div>
    </div>
  )
}

function SelectedEventCard({
  event,
}: {
  event: TraceEvent
  onOpenTimeline: () => void
}) {
  const source = eventEndpointDisplay(event.source, event.source_nf)
  const target = eventEndpointDisplay(event.target, event.target_nf)
  const identifiers = event.identifiers ?? []
  return (
    <Card className='mt-4 overflow-hidden border-sky-500/25'>
      <div className='flex flex-col gap-3 border-b bg-muted/20 px-4 py-3 sm:flex-row sm:items-center sm:justify-between'>
        <div className='min-w-0'>
          <p className='text-[10px] font-semibold tracking-[.14em] text-muted-foreground uppercase'>
            Mensaje seleccionado
          </p>
          <p className='mt-1 truncate font-mono text-sm font-semibold'>
            {event.message}
          </p>
        </div>
        <div className='flex shrink-0 flex-wrap items-center gap-2'>
          <Badge>{event.protocol?.toUpperCase() ?? 'EVENTO'}</Badge>
          {(event.interface_3gpp ?? event.interface) && (
            <Badge variant='outline'>
              {event.interface_3gpp ?? event.interface}
            </Badge>
          )}
          <EvidenceBadge type={event.evidence_type} />
        </div>
      </div>
      <CardContent className='p-0'>
        <div className='grid xl:grid-cols-[.78fr_1.22fr]'>
          <div className='border-b xl:border-r xl:border-b-0'>
            <DecodeRow
              label='Timestamp'
              value={eventTimestamp(event.timestamp)}
            />
            <DecodeRow
              label='Enlace'
              value={`${source.label} → ${target.label}`}
            />
            <DecodeRow
              label='Origen'
              value={event.source_ip ?? source.endpoint}
              mono
            />
            <DecodeRow
              label='Destino'
              value={event.target_ip ?? target.endpoint}
              mono
            />
            {event.charging && (
              <>
                <DecodeRow label='Nchf' value={event.charging.operation} />
                <DecodeRow label='HTTP/2' value={event.charging.http_status ?? event.charging.http_method ?? 'No decodificado'} />
                {event.charging.units.map((unit, index) => (
                  <DecodeRow key={index} label={unit.kind} value={[
                    unit.ratingGroup !== undefined ? `RG ${unit.ratingGroup}` : null,
                    unit.totalVolume !== undefined ? `Total ${unit.totalVolume} B` : null,
                    unit.uplinkVolume !== undefined ? `UL ${unit.uplinkVolume} B` : null,
                    unit.downlinkVolume !== undefined ? `DL ${unit.downlinkVolume} B` : null,
                    unit.localSequenceNumber !== undefined ? `Sec. ${unit.localSequenceNumber}` : null,
                  ].filter(Boolean).join(' · ')} />
                ))}
              </>
            )}
            {event.standard_references?.map((ref) => (
              <DecodeRow
                key={`${ref.spec}-${ref.clause}`}
                label={ref.spec}
                value={`V${ref.version} · § ${ref.clause}`}
              />
            ))}
            {event.interpretation_policy && (
              <DecodeRow
                label='Validación'
                value='Evidencia observada · timers no evaluados · no certifica conformidad'
              />
            )}
            <DecodeRow
              label='Procedimiento'
              value={event.procedure ?? 'No clasificado'}
            />
            <DecodeRow label='Estado' value={event.status ?? 'info'} />
            <DecodeRow
              label='Frame'
              value={String(
                event.frame_number ?? event.packet_number ?? 'No asociado'
              )}
              mono
            />
          </div>
          <div className='p-4'>
            <div className='mb-3 flex items-center justify-between gap-3'>
              <p className='text-xs font-semibold tracking-wide uppercase'>
                Información decodificada
              </p>
              <Badge variant='secondary'>
                {identifiers.length} identificadores
              </Badge>
            </div>
            {identifiers.length ? (
              <div className='mb-4 grid gap-2 sm:grid-cols-2'>
                {identifiers.map((identifier, index) => (
                  <div
                    key={`${identifier.kind ?? identifier.type}:${index}`}
                    className='rounded-md border bg-background px-3 py-2'
                  >
                    <p className='text-[9px] font-semibold tracking-wide text-muted-foreground uppercase'>
                      {identifier.label ??
                        identifierLabel(identifier.kind ?? identifier.type)}
                    </p>
                    <p className='mt-1 font-mono text-xs'>
                      {maskIdentifier(
                        identifier.masked_value ?? identifier.value,
                        identifier.kind ?? identifier.type
                      )}
                    </p>
                  </div>
                ))}
              </div>
            ) : (
              <p className='mb-4 text-xs text-muted-foreground'>
                Este mensaje no contiene identificadores correlacionables
                expuestos por el decodificador.
              </p>
            )}
            <details
              className='group rounded-md border bg-slate-950 text-slate-200'
              open
            >
              <summary className='cursor-pointer border-b border-slate-800 px-3 py-2 font-mono text-[10px] font-semibold text-sky-300 select-none'>
                Evidencia de decodificación
              </summary>
              <pre className='max-h-52 overflow-auto p-3 font-mono text-[10px] leading-relaxed break-words whitespace-pre-wrap'>
                {event.evidence ??
                  buildEventEvidence(event, source.endpoint, target.endpoint)}
              </pre>
            </details>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function DecodeRow({
  label,
  value,
  mono = false,
}: {
  label: string
  value: string
  mono?: boolean
}) {
  return (
    <div className='grid grid-cols-[7.5rem_1fr] border-b last:border-b-0'>
      <div className='bg-muted/35 px-3 py-2 text-[10px] font-semibold text-muted-foreground'>
        {label}
      </div>
      <div
        className={cn(
          'min-w-0 px-3 py-2 text-xs break-all',
          mono && 'font-mono'
        )}
      >
        {value}
      </div>
    </div>
  )
}

function eventEndpointDisplay(
  endpoint: TraceEvent['source'],
  fallback?: string
) {
  if (typeof endpoint === 'object') {
    const label =
      endpoint.label ?? endpoint.nf ?? endpoint.id ?? fallback ?? 'NF'
    const address = endpoint.address
      ? `${endpoint.address}${endpoint.port ? `:${endpoint.port}` : ''}`
      : label
    return { label, endpoint: address }
  }
  const label = endpoint ?? fallback ?? 'Desconocido'
  return { label, endpoint: label }
}

function eventTimestamp(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return `${date.toLocaleString([], { hour12: false })}.${String(date.getMilliseconds()).padStart(3, '0')}`
}

function buildEventEvidence(event: TraceEvent, source: string, target: string) {
  return [
    `Message: ${event.message}`,
    `Protocol: ${event.protocol ?? 'unknown'}`,
    `3GPP interface: ${event.interface_3gpp ?? event.interface ?? 'unknown'}`,
    `Source: ${source}`,
    `Destination: ${target}`,
    `Procedure: ${event.procedure ?? 'unclassified'}`,
    `Evidence type: ${event.evidence_type ?? 'unknown'}`,
  ].join('\n')
}

function MetadataRow({ label, value }: { label: string; value: string }) {
  return (
    <div className='flex items-start justify-between gap-4'>
      <span className='text-muted-foreground'>{label}</span>
      <span className='text-right font-medium'>{value}</span>
    </div>
  )
}

function buildIdentifierChain(task?: TraceTask, declared?: TraceIdentifier[]) {
  const items = [...(declared ?? [])]
  if (
    !items.length &&
    task?.identifier_type &&
    (task.identifier_masked || task.identifier)
  ) {
    items.push({
      kind: task.identifier_type,
      value: maskIdentifier(
        task.identifier_masked ?? task.identifier,
        task.identifier_type
      ),
      evidence_type: 'runtime',
      confidence: 'direct',
    })
  }
  const order = [
    'imsi',
    'supi',
    'suci',
    'ran_ue_ngap_id',
    'ran-ue-ngap-id',
    'amf_ue_ngap_id',
    'amf-ue-ngap-id',
    'pdu_session_id',
    'pdu-session-id',
    'pfcp_seid',
    'seid',
    'gtpu_teid',
    'teid',
    'ue_ip',
    'ue-ip',
  ]
  return items.sort((a, b) => {
    const aKey = (a.kind ?? a.type ?? '').toLowerCase()
    const bKey = (b.kind ?? b.type ?? '').toLowerCase()
    const aIndex = order.indexOf(aKey)
    const bIndex = order.indexOf(bKey)
    return (
      (aIndex === -1 ? order.length : aIndex) -
      (bIndex === -1 ? order.length : bIndex)
    )
  })
}

function identifierLabel(kind?: string) {
  const labels: Record<string, string> = {
    imsi: 'IMSI',
    supi: 'SUPI',
    suci: 'SUCI',
    ran_ue_ngap_id: 'RAN UE NGAP ID',
    'ran-ue-ngap-id': 'RAN UE NGAP ID',
    amf_ue_ngap_id: 'AMF UE NGAP ID',
    'amf-ue-ngap-id': 'AMF UE NGAP ID',
    pdu_session_id: 'PDU Session ID',
    'pdu-session-id': 'PDU Session ID',
    pfcp_seid: 'PFCP SEID',
    seid: 'PFCP SEID',
    gtpu_teid: 'GTP-U TEID',
    teid: 'GTP-U TEID',
    ue_ip: 'UE IP',
    'ue-ip': 'UE IP',
  }
  return (
    labels[(kind ?? '').toLowerCase()] ??
    (kind ?? 'Identificador').replace(/_/g, ' ').toUpperCase()
  )
}

function confidenceLabel(value: string) {
  const labels: Record<string, string> = {
    direct: 'Evidencia directa',
    transaction: 'Misma transacción',
    inferred: 'Relación inferida',
  }
  return labels[value] ?? value
}

function correlationLabel(value?: string) {
  const labels: Record<string, string> = {
    complete: 'Completa',
    partial: 'Parcial',
    insufficient: 'Insuficiente',
  }
  return value ? (labels[value] ?? value) : 'Pendiente'
}

function maskIdentifier(value?: string, kind?: string) {
  if (!value) return 'No identificado'
  if (value.includes('*') || value.includes('•')) return value
  const normalizedKind = (kind ?? '').toLowerCase()
  if (!['imsi', 'supi'].includes(normalizedKind) || value.length < 9)
    return value
  const prefix = value.toLowerCase().startsWith('imsi-') ? 'imsi-' : ''
  const digits = prefix ? value.slice(prefix.length) : value
  return `${prefix}${digits.slice(0, 5)}${'•'.repeat(Math.max(digits.length - 9, 3))}${digits.slice(-4)}`
}

function shortId(id: string) {
  return id.length > 20 ? `${id.slice(0, 12)}…${id.slice(-6)}` : id
}

function DetailSkeleton() {
  return (
    <div className='animate-pulse space-y-4'>
      <div className='h-10 rounded-lg bg-muted' />
      <div className='h-10 rounded-lg bg-muted' />
      <div className='h-96 rounded-xl bg-muted' />
    </div>
  )
}

export type TraceTaskDetailViewProps = {
  task?: TraceTask
  analysis?: TraceAnalysis
  isLoading: boolean
  analysisError?: Error | null
  role?: Role
  currentUsername?: string
  isStopping?: boolean
  isDeleting?: boolean
  onBack: () => void
  onStop: (task: TraceTask) => void
  onDelete: (task: TraceTask) => void
  onDownload: (
    task: TraceTask,
    artifact: 'original' | 'filtered' | 'evidence'
  ) => void
}

export function TraceTaskDetailView({
  task,
  analysis,
  isLoading,
  analysisError,
  role,
  currentUsername,
  isStopping,
  isDeleting,
  onBack,
  onStop,
  onDelete,
  onDownload,
}: TraceTaskDetailViewProps) {
  const [selectedEvent, setSelectedEvent] = useState<TraceEvent | undefined>()
  const [deleteOpen, setDeleteOpen] = useState(false)
  const canControl =
    !!task &&
    (role === 'admin' ||
      role === 'teacher' ||
      (!!task.owner && task.owner === currentUsername))
  const isTroubleshooting =
    (!analysis?.events || analysis.events.length === 0) &&
    Boolean(analysis?.troubleshooting_events?.length)

  const events = isTroubleshooting
    ? (analysis?.troubleshooting_events ?? [])
    : (analysis?.events ?? [])

  const participants = isTroubleshooting
    ? (analysis?.troubleshooting_participants ?? analysis?.participants)
    : analysis?.participants

  return (
    <div className='flex min-h-0 flex-col gap-3'>
      {/* Barra Superior con Navegación y Acciones */}
      <div className='flex min-h-14 items-center justify-between gap-3 rounded-lg border bg-card px-3 py-2 shadow-xs sm:px-4'>
        <div className='flex min-w-0 items-center gap-3'>
          <Button
            variant='ghost'
            size='icon'
            onClick={onBack}
            className='size-8 shrink-0'
            title='Volver a la lista de tareas'
            aria-label='Volver a Trazas'
          >
            <ArrowLeft className='h-4 w-4' />
          </Button>
          <div className='h-6 w-px shrink-0 bg-border' />
          <h2 className='truncate text-base font-semibold tracking-tight text-foreground sm:text-lg'>
            {task?.name ?? 'Detalle de tarea'}
          </h2>
          {task && (
            <TraceOutcomeBadge
              outcome={analysis?.outcome ?? task.outcome ?? task.result}
            />
          )}
        </div>

        {task && (
          <div className='flex flex-wrap items-center gap-2'>
            {isActiveTrace(task) && canControl && (
              <Button
                variant='destructive'
                size='sm'
                disabled={isStopping}
                onClick={() => onStop(task)}
              >
                <Square /> {isStopping ? 'Deteniendo…' : 'Detener'}
              </Button>
            )}
            {!isActiveTrace(task) && (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant='outline'
                    size='sm'
                    className='gap-1.5 font-semibold shadow-xs'
                  >
                    <Download className='h-4 w-4' /> Descargar
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align='end'>
                  <DropdownMenuItem
                    onSelect={() => onDownload(task, 'original')}
                  >
                    <Download /> PCAP original
                  </DropdownMenuItem>
                  {traceKind(task) === 'subscriber' && (
                    <>
                      <DropdownMenuItem
                        onSelect={() => onDownload(task, 'filtered')}
                      >
                        <Download /> PCAP filtrado
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        onSelect={() => onDownload(task, 'evidence')}
                      >
                        <FileJson /> Evidencia JSON
                      </DropdownMenuItem>
                    </>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
            )}
            {!isActiveTrace(task) && canControl && (
              <Button
                variant='ghost'
                size='icon'
                aria-label='Eliminar tarea'
                onClick={() => setDeleteOpen(true)}
              >
                <Trash2 className='text-destructive' />
              </Button>
            )}
          </div>
        )}
      </div>

      {/* Contenido Principal sin modales */}
      {isLoading || !task ? (
        <DetailSkeleton />
      ) : (
        <div className='space-y-3'>
          {analysisError && (
            <Alert variant='destructive'>
              <ShieldAlert />
              <AlertTitle>El análisis no está disponible</AlertTitle>
              <AlertDescription>
                {analysisError.message}. El PCAP original y los metadatos de
                captura siguen disponibles.
              </AlertDescription>
            </Alert>
          )}

          <div className='space-y-3'>
            {isTroubleshooting && (
              <div className='flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-amber-900 dark:text-amber-200'>
                <Wrench className='mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400' />
                <div className='min-w-0 flex-1 space-y-1 text-xs sm:text-sm'>
                  <p className='font-semibold text-amber-800 dark:text-amber-300'>
                    Modo Diagnóstico (Troubleshooting Activo)
                  </p>
                  <p className='text-xs text-amber-700/90 dark:text-amber-200/90'>
                    No se observó tráfico de señalización NAS/RRC vinculado al suscriptor en esta captura (posible fallo de conexión o UE inactivo). Se muestran los {events.length} paquetes de interfaces capturados (PFCP, SBI, SCTP) para permitir el diagnóstico de la red.
                  </p>
                </div>
              </div>
            )}
            <SequenceDiagram
              events={events}
              participants={participants}
              selectedEventId={selectedEvent?.id}
              onSelectEvent={setSelectedEvent}
            />
            {selectedEvent && (
              <SelectedEventCard
                event={selectedEvent}
                onOpenTimeline={() => undefined}
              />
            )}
          </div>

          <ConfirmDialog
            open={deleteOpen}
            onOpenChange={setDeleteOpen}
            title='Eliminar tarea y artefactos'
            desc={`¿Deseas eliminar "${task.name ?? task.id}"? Se eliminarán los PCAP, eventos correlacionados y archivos de evidencia asociados. Esta acción no puede deshacerse.`}
            confirmText='Eliminar definitivamente'
            destructive
            isLoading={isDeleting}
            handleConfirm={() => {
              onDelete(task)
              setDeleteOpen(false)
            }}
          />
        </div>
      )}
    </div>
  )
}
