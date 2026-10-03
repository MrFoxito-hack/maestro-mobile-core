import { useMemo, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowUp,
  RotateCcw,
  Search,
  ZoomIn,
  ZoomOut,
} from 'lucide-react'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  eventEndpointId,
  type TraceEvent,
  type TraceParticipant,
} from '../types'
import { EmptyState } from './trace-status'

const PARTICIPANT_WIDTH = 176
const TIME_GUTTER_WIDTH = 184
const HEADER_HEIGHT = 48
const CANONICAL_ORDER = [
  'ue',
  'gnb',
  'enb',
  'amf',
  'mme',
  'ausf',
  'udm',
  'udr',
  'nrf',
  'scp',
  'nssf',
  'af',
  'pcf',
  'bsf',
  'smf',
  'chf',
  'sgwc',
  'upf',
  'sgwu',
  'dn',
]
type Density = 'normal' | 'compact'

export function SequenceDiagram({
  events,
  participants,
  selectedEventId,
  onSelectEvent,
}: {
  events: TraceEvent[]
  participants?: Array<TraceParticipant | string>
  selectedEventId?: string
  onSelectEvent?: (event: TraceEvent) => void
}) {
  const [searchTerm, setSearchTerm] = useState('')
  const [density, setDensity] = useState<Density>('normal')
  const [procedureFilter, setProcedureFilter] = useState('all')
  const [zoomLevel, setZoomLevel] = useState(1)
  const scrollRef = useRef<HTMLDivElement>(null)
  const participantList = useMemo(
    () => buildParticipants(events, participants),
    [events, participants]
  )
  const filteredEvents = useMemo(() => {
    let result = [...events].sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0))
    if (procedureFilter !== 'all')
      result = result.filter((event) =>
        matchesProcedure(event, procedureFilter)
      )
    const query = searchTerm.trim().toLowerCase()
    if (query) {
      result = result.filter((event) =>
        [
          event.message,
          event.protocol,
          event.procedure,
          event.interface_3gpp,
          event.interface,
          event.source_nf,
          event.target_nf,
        ].some((value) => value?.toLowerCase().includes(query))
      )
    }
    return result.slice(0, 500)
  }, [events, procedureFilter, searchTerm])
  const protocolLegend = useMemo(
    () =>
      Array.from(
        new Set(
          filteredEvents
            .map((event) => event.protocol)
            .filter((protocol): protocol is string => Boolean(protocol))
        )
      ),
    [filteredEvents]
  )

  if (!events.length) {
    return (
      <EmptyState
        title='Captura sin paquetes de señalización'
        description='No se registraron paquetes decodificables en el periodo seleccionado o la interfaz estuvo inactiva.'
      />
    )
  }

  if (participantList.length < 2) {
    return (
      <EmptyState
        title='Señalización unilateral o insuficiente'
        description='Se detectaron eventos, pero no fue posible identificar al menos dos funciones de red para diagramar la interacción.'
      />
    )
  }

  const baseRowHeight = density === 'normal' ? 44 : 34
  const rowHeight = Math.max(28, Math.round(baseRowHeight * zoomLevel))
  const diagramWidth = Math.max(
    participantList.length * PARTICIPANT_WIDTH * zoomLevel,
    780
  )
  const bodyHeight = Math.max(filteredEvents.length * rowHeight, 250)
  const totalWidth = TIME_GUTTER_WIDTH + diagramWidth
  const xFor = (id: string) => {
    const index = participantList.findIndex(
      (item) => item.id === normalizeParticipant(id)
    )
    return (Math.max(index, 0) + 0.5) * PARTICIPANT_WIDTH * zoomLevel
  }

  return (
    <div className='flex min-h-0 flex-col gap-2'>
      <div className='flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card/60 p-2.5 text-xs'>
        <div className='flex flex-wrap items-center gap-2'>
          <div className='hidden items-center gap-1 xl:flex'>
            {(
              [
                ['all', 'Todo'],
                ['registration', 'Registro'],
                ['authentication', '5G-AKA'],
                ['pdu-session', 'Sesión PDU'],
                ['user-plane', 'Datos'],
              ] as const
            ).map(([id, label]) => (
              <Button
                key={id}
                variant={procedureFilter === id ? 'secondary' : 'ghost'}
                size='sm'
                className='h-7 px-2 text-[11px]'
                onClick={() => setProcedureFilter(id)}
              >
                {label}
              </Button>
            ))}
          </div>
          <div className='relative w-52 sm:w-64'>
            <Search className='absolute top-1/2 left-2.5 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground' />
            <Input
              value={searchTerm}
              onChange={(event) => setSearchTerm(event.target.value)}
              placeholder='Mensaje, protocolo, interfaz…'
              className='h-7 pl-8 text-xs'
            />
          </div>
        </div>
        <div className='flex items-center gap-1.5'>
          <Badge variant='outline' className='font-mono text-[10px]'>
            {filteredEvents.length} eventos
          </Badge>
          <Button
            variant='outline'
            size='icon'
            className='h-7 w-7'
            title='Primer evento'
            onClick={() =>
              scrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' })
            }
          >
            <ArrowUp className='h-3 w-3' />
          </Button>
          <Button
            variant='outline'
            size='icon'
            className='h-7 w-7'
            title='Último evento'
            onClick={() =>
              scrollRef.current?.scrollTo({
                top: scrollRef.current.scrollHeight,
                behavior: 'smooth',
              })
            }
          >
            <ArrowDown className='h-3 w-3' />
          </Button>
          <Button
            variant='outline'
            size='icon'
            className='h-7 w-7'
            disabled={zoomLevel <= 0.7}
            title='Reducir zoom'
            onClick={() => setZoomLevel((value) => Math.max(0.7, value - 0.15))}
          >
            <ZoomOut className='h-3 w-3' />
          </Button>
          <Button
            variant='outline'
            size='icon'
            className='h-7 w-7'
            disabled={zoomLevel >= 1.5}
            title='Aumentar zoom'
            onClick={() => setZoomLevel((value) => Math.min(1.5, value + 0.15))}
          >
            <ZoomIn className='h-3 w-3' />
          </Button>
          <Button
            variant={density === 'compact' ? 'secondary' : 'outline'}
            size='sm'
            className='h-7 px-2 text-[11px]'
            onClick={() =>
              setDensity(density === 'normal' ? 'compact' : 'normal')
            }
          >
            {density === 'normal' ? 'Compacto' : 'Normal'}
          </Button>
          <Button
            variant='ghost'
            size='icon'
            className='h-7 w-7'
            title='Restablecer'
            onClick={() => {
              setZoomLevel(1)
              setDensity('normal')
              setSearchTerm('')
              setProcedureFilter('all')
            }}
          >
            <RotateCcw className='h-3 w-3' />
          </Button>
        </div>
      </div>

      <div className='min-h-0 overflow-hidden rounded-lg border bg-card shadow-inner'>
        <div
          ref={scrollRef}
          className='relative h-[calc(100dvh-10.75rem)] min-h-[22rem] overflow-auto'
          style={{ scrollbarWidth: 'thin' }}
        >
          <div
            className='relative'
            style={{ width: totalWidth, minHeight: HEADER_HEIGHT + bodyHeight }}
          >
            <div
              className='sticky top-0 z-40 flex border-b bg-card/95 shadow-sm backdrop-blur'
              style={{ width: totalWidth, height: HEADER_HEIGHT }}
            >
              <div
                className='sticky left-0 z-50 flex shrink-0 items-center justify-between border-r bg-card px-3 text-[10px] font-semibold tracking-wider text-muted-foreground uppercase'
                style={{ width: TIME_GUTTER_WIDTH }}
              >
                <span>Timestamp</span>
                <span>Δ</span>
              </div>
              <div
                className='relative shrink-0'
                style={{ width: diagramWidth }}
              >
                {participantList.map((participant, index) => {
                  const x = (index + 0.5) * PARTICIPANT_WIDTH * zoomLevel
                  return (
                    <div
                      key={participant.id}
                      className='absolute top-2 flex -translate-x-1/2 items-center gap-1.5 rounded-md border border-slate-500/50 bg-background px-3 py-1.5 shadow-sm'
                      style={{ left: x, maxWidth: PARTICIPANT_WIDTH - 14 }}
                      title={participant.label}
                    >
                      <span className='size-1.5 shrink-0 rounded-full bg-sky-500' />
                      <span className='truncate font-mono text-xs font-bold tracking-wide'>
                        {participant.label}
                      </span>
                    </div>
                  )
                })}
              </div>
            </div>

            <div className='relative flex' style={{ height: bodyHeight }}>
              {!filteredEvents.length && (
                <div
                  className='absolute top-20 z-20 flex justify-center px-6'
                  style={{ left: TIME_GUTTER_WIDTH, width: diagramWidth }}
                >
                  <div className='max-w-md rounded-lg border border-amber-500/30 bg-card/95 px-4 py-3 text-center shadow-sm'>
                    <p className='text-sm font-semibold'>
                      No se observaron eventos de{' '}
                      {procedureLabel(procedureFilter)}
                    </p>
                    <p className='mt-1 text-xs text-muted-foreground'>
                      Esto no representa un resultado exitoso: el procedimiento
                      no fue ejecutado o no estuvo visible en esta captura.
                    </p>
                  </div>
                </div>
              )}
              <div
                className='sticky left-0 z-30 shrink-0 border-r bg-card'
                style={{ width: TIME_GUTTER_WIDTH, height: bodyHeight }}
              >
                {filteredEvents.map((event, index) => (
                  <button
                    key={`time-${event.id}`}
                    type='button'
                    onClick={() => onSelectEvent?.(event)}
                    className={cn(
                      'absolute left-0 grid w-full grid-cols-[1fr_auto] items-center gap-2 border-b border-dotted px-3 text-left font-mono transition-colors hover:bg-muted/70',
                      selectedEventId === event.id &&
                        'bg-primary/10 text-primary'
                    )}
                    style={{ top: index * rowHeight, height: rowHeight }}
                  >
                    <span className='truncate text-[10px]'>
                      {absoluteTime(event.timestamp)}
                    </span>
                    <span className='text-[9px] text-muted-foreground'>
                      {relativeTime(event)}
                    </span>
                  </button>
                ))}
              </div>

              <svg
                role='img'
                aria-label='Flujo de señalización extremo a extremo'
                width={diagramWidth}
                height={bodyHeight}
                viewBox={`0 0 ${diagramWidth} ${bodyHeight}`}
                className='block shrink-0'
              >
                {filteredEvents.map((event, index) => (
                  <rect
                    key={`row-${event.id}`}
                    pointerEvents='none'
                    x={0}
                    y={index * rowHeight}
                    width={diagramWidth}
                    height={rowHeight}
                    fill={
                      selectedEventId === event.id
                        ? 'color-mix(in oklab, var(--primary) 10%, transparent)'
                        : index % 2
                          ? 'color-mix(in oklab, var(--muted) 12%, transparent)'
                          : 'transparent'
                    }
                    stroke='color-mix(in oklab, var(--border) 70%, transparent)'
                    strokeDasharray='2 4'
                    strokeWidth={0.7}
                  />
                ))}
                {participantList.map((participant, index) => {
                  const x = (index + 0.5) * PARTICIPANT_WIDTH * zoomLevel
                  return (
                    <line
                      key={`lifeline-${participant.id}`}
                      pointerEvents='none'
                      x1={x}
                      y1={0}
                      x2={x}
                      y2={bodyHeight}
                      stroke='color-mix(in oklab, var(--muted-foreground) 42%, transparent)'
                      strokeWidth={2}
                    />
                  )
                })}
                {filteredEvents.map((event, index) => {
                  const sourceX = xFor(
                    eventEndpointId(event.source, event.source_nf)
                  )
                  const targetX = xFor(
                    eventEndpointId(event.target, event.target_nf)
                  )
                  const y = index * rowHeight + rowHeight / 2 + 5
                  const selected = selectedEventId === event.id
                  const inferred = event.evidence_type === 'correlated'
                  const color = eventColor(event)
                  const sameParticipant = Math.abs(sourceX - targetX) < 1
                  const direction = targetX >= sourceX ? 1 : -1
                  const endX = targetX - direction * 9
                  const pillText = formatPillMessage(
                    event.message,
                    Math.max(50, event.message.length),
                    true
                  )
                  const labelWidth = Math.min(
                    Math.max(pillText.length * 6.5 + 20, 116),
                    Math.max(Math.abs(targetX - sourceX) - 12, 290)
                  )
                  const labelX = sameParticipant
                    ? sourceX + 42
                    : (sourceX + targetX) / 2
                  return (
                    <g
                      key={event.id}
                      role='button'
                      tabIndex={0}
                      className='cursor-pointer outline-none'
                      onClick={() => onSelectEvent?.(event)}
                      onKeyDown={(keyEvent) => {
                        if (keyEvent.key === 'Enter' || keyEvent.key === ' ')
                          onSelectEvent?.(event)
                      }}
                    >
                      {sameParticipant ? (
                        <path
                          d={`M ${sourceX} ${y} C ${sourceX + 55} ${y}, ${sourceX + 55} ${y + 20}, ${sourceX} ${y + 20}`}
                          fill='none'
                          stroke={color}
                          strokeWidth={selected ? 3 : 2}
                          strokeDasharray={inferred ? '6 4' : undefined}
                        />
                      ) : (
                        <line
                          x1={sourceX + direction * 7}
                          y1={y}
                          x2={endX}
                          y2={y}
                          stroke={color}
                          strokeWidth={selected ? 3 : 2}
                          strokeDasharray={inferred ? '6 4' : undefined}
                        />
                      )}
                      <polygon
                        points={arrowPoints(
                          sameParticipant ? sourceX : targetX,
                          sameParticipant ? y + 20 : y,
                          sameParticipant ? -1 : direction
                        )}
                        fill={color}
                      />
                      <rect
                        x={labelX - labelWidth / 2}
                        y={index * rowHeight + 4}
                        width={labelWidth}
                        height={17}
                        rx={3}
                        fill={selected ? color : 'var(--card)'}
                        stroke={color}
                        strokeWidth={selected ? 1.5 : 0.8}
                      />
                      <text
                        x={labelX}
                        y={index * rowHeight + 16}
                        textAnchor='middle'
                        fill={selected ? '#ffffff' : color}
                        className='font-mono text-[10px] font-semibold'
                      >
                        {pillText}
                        <title>{event.message}</title>
                      </text>
                      <text
                        x={Math.min(sourceX, targetX) + 5}
                        y={index * rowHeight + rowHeight - 5}
                        fill='var(--muted-foreground)'
                        className='font-mono text-[8px]'
                      >
                        {(() => {
                          const proto = event.protocol?.toUpperCase()
                          const iface = event.interface_3gpp ?? event.interface
                          const parts = [proto, iface].filter(
                            Boolean
                          ) as string[]
                          // Avoid duplicating when protocol already contains the interface name
                          if (
                            parts.length === 2 &&
                            proto &&
                            iface &&
                            proto.toLowerCase().includes(iface.toLowerCase())
                          )
                            return proto
                          return parts.join(' · ')
                        })()}
                      </text>
                    </g>
                  )
                })}
              </svg>
            </div>
          </div>
        </div>
      </div>

      <div className='flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border bg-muted/15 px-3 py-2 text-[10px] text-muted-foreground'>
        <span className='font-semibold tracking-wide uppercase'>
          Protocolos
        </span>
        {protocolLegend.map((protocol) => (
          <span key={protocol} className='flex items-center gap-1.5'>
            <span
              className='h-0.5 w-5 rounded-full'
              style={{ backgroundColor: protocolColor(protocol) }}
            />
            {protocol}
          </span>
        ))}
        <span className='ml-auto'>
          Línea discontinua = evento correlacionado
        </span>
      </div>
    </div>
  )
}

function matchesProcedure(event: TraceEvent, procedure: string) {
  if (event.interpretation_policy) {
    if (procedure === 'registration') {
      return (
        event.procedure === 'registration' ||
        event.procedure === 'authentication' ||
        event.procedure === 'nas-security'
      )
    }
    return event.procedure === procedure
  }
  const message = event.message.toLowerCase()
  if (event.procedure === procedure) return true
  if (procedure === 'registration')
    return (
      message.includes('registration') ||
      message.includes('attach') ||
      message.includes('authenticat') ||
      message.includes('security')
    )
  if (procedure === 'authentication')
    return message.includes('authenticat') || message.includes('security')
  if (procedure === 'pdu-session')
    return (
      message.includes('pdu') ||
      message.includes('pfcp') ||
      message.includes('session')
    )
  if (procedure === 'user-plane')
    return (
      message.includes('ping') ||
      message.includes('icmp') ||
      message.includes('gtp-u')
    )
  return true
}

function procedureLabel(value: string) {
  return (
    {
      all: 'la selección actual',
      registration: 'Registro',
      authentication: '5G-AKA',
      'pdu-session': 'Sesión PDU',
      'user-plane': 'Datos',
    }[value] ?? value
  )
}

function eventColor(event: TraceEvent) {
  return event.status === 'failure'
    ? '#ef4444'
    : protocolColor(event.protocol ?? event.interface_3gpp ?? '')
}
function protocolColor(value: string) {
  const protocol = value.toLowerCase().replace(/[^a-z0-9]/g, '')
  if (protocol.includes('nas')) return '#0ea5e9'
  if (protocol.includes('ngap') || protocol.includes('s1ap')) return '#14b8a6'
  if (protocol.includes('http') || protocol.includes('sbi')) return '#8b5cf6'
  if (protocol.includes('pfcp') || protocol.includes('n4')) return '#f59e0b'
  if (protocol.includes('gtpu') || protocol.includes('n3')) return '#22c55e'
  if (protocol.includes('gtpc')) return '#eab308'
  if (protocol.includes('icmp')) return '#06b6d4'
  if (protocol.includes('sctp')) return '#64748b'
  if (protocol.includes('nruu') || protocol.includes('radio')) return '#ec4899'
  return '#7c3aed'
}
function arrowPoints(x: number, y: number, direction: number) {
  return direction > 0
    ? `${x},${y} ${x - 10},${y - 5} ${x - 10},${y + 5}`
    : `${x},${y} ${x + 10},${y - 5} ${x + 10},${y + 5}`
}

function buildParticipants(
  events: TraceEvent[],
  declared?: Array<TraceParticipant | string>
) {
  const labels = new Map<string, string>()
  for (const item of declared ?? []) {
    if (typeof item === 'string')
      labels.set(normalizeParticipant(item), displayParticipant(item))
    else
      labels.set(
        normalizeParticipant(item.id ?? item.label ?? 'NF'),
        item.label ?? displayParticipant(item.id)
      )
  }
  for (const event of events) {
    const source = eventEndpointId(event.source, event.source_nf)
    const target = eventEndpointId(event.target, event.target_nf)
    labels.set(
      normalizeParticipant(source),
      endpointLabel(event.source, event.source_nf)
    )
    labels.set(
      normalizeParticipant(target),
      endpointLabel(event.target, event.target_nf)
    )
  }
  labels.delete('unknown')
  if (events.length > 0 && labels.size === 1) {
    labels.set('network', 'Red')
  }
  return [...labels.entries()]
    .map(([id, label]) => ({ id, label }))
    .sort(
      (a, b) =>
        participantRank(a.id) - participantRank(b.id) ||
        a.id.localeCompare(b.id)
    )
}
function normalizeParticipant(value: string) {
  return value
    .toLowerCase()
    .replace(/gnodeb/g, 'gnb')
    .replace(/enodeb/g, 'enb')
    .replace(/[^a-z0-9-]/g, '')
}
function endpointLabel(endpoint: TraceEvent['source'], fallback?: string) {
  return typeof endpoint === 'object'
    ? (endpoint.label ??
        displayParticipant(endpoint.nf ?? endpoint.id ?? fallback ?? 'NF'))
    : displayParticipant(endpoint ?? fallback ?? 'NF')
}
function displayParticipant(value: string) {
  const normalized = normalizeParticipant(value)
  return (
    (
      { ue: 'UE', gnb: 'gNodeB', enb: 'eNodeB', dn: 'Data Network' } as Record<
        string,
        string
      >
    )[normalized] ?? value.toUpperCase()
  )
}
function participantRank(id: string) {
  const index = CANONICAL_ORDER.indexOf(id)
  return index === -1 ? CANONICAL_ORDER.length : index
}
function formatPillMessage(value: string, max: number, _literal = true) {
  return truncate(value, max)
}

function truncate(value: string, max: number) {
  return value.length <= max
    ? value
    : `${value.slice(0, Math.max(max - 1, 1))}…`
}
function relativeTime(event: TraceEvent) {
  if (event.relative_ms == null) return `#${event.ordinal ?? '—'}`
  return event.relative_ms < 1000
    ? `+${event.relative_ms.toFixed(0)}ms`
    : `+${(event.relative_ms / 1000).toFixed(3)}s`
}
function absoluteTime(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  const base = date.toLocaleTimeString([], { hour12: false })
  return `${base}.${String(date.getMilliseconds()).padStart(3, '0')}`
}
