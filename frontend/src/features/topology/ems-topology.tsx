import { Fragment, useEffect, useRef } from 'react'
import {
  Background,
  BaseEdge,
  Controls,
  EdgeLabelRenderer,
  getSmoothStepPath,
  getStraightPath,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeMouseHandler,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Brain, Coins, Database, Globe, Radio, Router, Server, Smartphone, Tv } from 'lucide-react'
import type { ComponentStatus, RuntimeSnapshot } from '@/lib/api'
import { alarmBelongsToComponent } from './topology-alarm'

export type TopologyView = 'physical' | 'telco' | 'models' | 'interfaces'
export type TopologySelection =
  | { type: 'host'; id: string }
  | { type: 'component'; id: string }

export type NodeAlarmItem = {
  component?: string
  node_id?: string
  severity?: string
}

// Arquitectura 3GPP Rel-16 Canónica con SBA Service Bus (3GPP TS 23.501 Figura 4.2.3-2)
const telcoPositions5G: Record<string, { x: number; y: number }> = {
  // Fila 1: Repositorios, Datos y Productores SBA (Superior, Y = 40)
  nssf: { x: 40, y: 40 },
  nrf: { x: 240, y: 40 },
  ausf: { x: 440, y: 40 },
  udm: { x: 640, y: 40 },
  udr: { x: 840, y: 40 },
  mongodb: { x: 840, y: -55 },
  pcf: { x: 1040, y: 40 },
  chf: { x: 1240, y: 40 },
  nwdaf: { x: 1440, y: 40 },
  af: { x: 1640, y: 40 },

  // Fila 2: Orquestadores Core y Proxy (Bajo el Bus SBA, Y = 285)
  amf: { x: 340, y: 285 },
  smf: { x: 740, y: 285 },
  scp: { x: 1140, y: 285 },
  bsf: { x: 1340, y: 285 },

  // Fila 3: Pipeline de Acceso Radio y Plano de Usuario (Y = 470)
  ue: { x: 60, y: 470 },
  gnb: { x: 340, y: 470 },
  upf: { x: 740, y: 470 },
  dn: { x: 1140, y: 470 },
}

// Arquitectura Core SBA pura: Modelo canónico 3GPP (Sin RAN, UPF ni DB)
const sbaModelsPositions5G: Record<string, { x: number; y: number }> = {
  // Izquierda: Funciones Consumidoras
  amf: { x: 80, y: 150 },
  smf: { x: 340, y: 330 },

  // Centro: Hub SBA (Repositorio y Service Mesh)
  nrf: { x: 620, y: 60 },
  scp: { x: 620, y: 260 },

  // Columna Derecha: Funciones Productoras de Servicios SBA (UDM -> BSF -> PCF -> NWDAF -> CHF)
  udm: { x: 1160, y: 40 },
  bsf: { x: 1160, y: 150 },
  pcf: { x: 1160, y: 260 },
  nwdaf: { x: 1160, y: 370 },
  chf: { x: 1160, y: 480 },

  // Extremo Derecho: Aplicación Externa (Modelo A / Directo con PCF)
  af: { x: 1440, y: 260 },
}

const telcoPositions4G: Record<string, { x: number; y: number }> = {
  mongodb: { x: 60, y: 50 },
  hss: { x: 260, y: 50 },
  pcrf: { x: 580, y: 50 },

  mme: { x: 260, y: 230 },
  sgwc: { x: 580, y: 230 },
  smf: { x: 910, y: 230 },

  ue: { x: 60, y: 410 },
  enb: { x: 260, y: 410 },
  sgwu: { x: 580, y: 410 },
  upf: { x: 910, y: 330 },
  upf2: { x: 910, y: 480 },
}

type TelcoEdgeMeta = {
  label: string
  stroke: 'solid'
  color: string
  sourceHandle?: string
  targetHandle?: string
  type?: string
  labelNearTarget?: boolean
  bidirectional?: boolean
  noMarker?: boolean
}

// 1. Diccionario de Modelos SBA (Paleta Telco Moderna y Profesional · Pizarra y Ámbar sobrio)
const sbaModelsEdges: Record<string, TelcoEdgeMeta> = {
  // SBA Modelo A: Productor-Consumidor Directo sin NRF (AF ──── PCF) · 3GPP TS 23.501 Cl. 7.1.4
  'af-pcf': {
    label: 'Modelo A',
    stroke: 'solid',
    color: '#2563eb',
    sourceHandle: 'source-left',
    targetHandle: 'target-right',
  },
  'pcf-af': {
    label: 'Modelo A',
    stroke: 'solid',
    color: '#2563eb',
    sourceHandle: 'source-right',
    targetHandle: 'target-left',
  },

  // SBA Modelo B: Productor-Consumidor Directo (AMF ──── SMF)
  'amf-smf': {
    label: 'Modelo B',
    stroke: 'solid',
    color: '#d97706',
    sourceHandle: 'source-bottom',
    targetHandle: 'target-left',
  },

  // Central Hub NRF - SCP: Enlace de comunicación proxy-repositorio (Bidireccional)
  'nrf-scp': {
    label: '',
    stroke: 'solid',
    color: '#64748b',
    sourceHandle: 'source-bottom',
    targetHandle: 'target-top',
    bidirectional: true,
  },

  // SBA Modelo D: Señalización Indirecta Delegada vía SCP Mesh (Pizarra institucional)
  'amf-scp': {
    label: 'Modelo D',
    stroke: 'solid',
    color: '#475569',
    sourceHandle: 'source-right',
    targetHandle: 'target-left',
  },
  'smf-scp': {
    label: 'Modelo D',
    stroke: 'solid',
    color: '#475569',
    sourceHandle: 'source-right',
    targetHandle: 'target-bottom',
  },
  'scp-udm': {
    label: 'AMF & SMF',
    stroke: 'solid',
    color: '#475569',
    sourceHandle: 'source-right-top',
    targetHandle: 'target-left',
    labelNearTarget: true,
  },
  'scp-bsf': {
    label: 'PCF',
    stroke: 'solid',
    color: '#475569',
    sourceHandle: 'source-right-top',
    targetHandle: 'target-left',
    labelNearTarget: true,
  },
  'scp-pcf': {
    label: 'SMF',
    stroke: 'solid',
    color: '#475569',
    sourceHandle: 'source-right-top',
    targetHandle: 'target-left-top',
    labelNearTarget: true,
  },
  'pcf-scp': {
    label: 'Modelo D',
    stroke: 'solid',
    color: '#475569',
    sourceHandle: 'source-left-bottom',
    targetHandle: 'target-right-bottom',
    type: 'telcoEdge',
  },

  // SBA Modelo A: Productor-Consumidor Directo sin NRF (NWDAF ──── PCF) · 3GPP TS 23.288 / TS 23.501
  'nwdaf-pcf': {
    label: 'Modelo A',
    stroke: 'solid',
    color: '#2563eb',
    sourceHandle: 'source-top',
    targetHandle: 'target-bottom',
  },
  'pcf-nwdaf': {
    label: 'Modelo A',
    stroke: 'solid',
    color: '#2563eb',
    sourceHandle: 'source-bottom',
    targetHandle: 'target-top',
  },

  // SBA Modelo A: Vinculación directa de Sesión y Políticas (PCF ──── BSF) · 3GPP TS 29.521 / TS 23.501
  'pcf-bsf': {
    label: 'Modelo A',
    stroke: 'solid',
    color: '#2563eb',
    sourceHandle: 'source-top',
    targetHandle: 'target-bottom',
  },
  'bsf-pcf': {
    label: 'Modelo A',
    stroke: 'solid',
    color: '#2563eb',
    sourceHandle: 'source-bottom',
    targetHandle: 'target-top',
  },

  // SBA Modelo A: Cobro Convergente Directo sin NRF (SMF ──── CHF) · 3GPP TS 32.290 / TS 32.291
  'smf-chf': {
    label: 'Modelo A',
    stroke: 'solid',
    color: '#2563eb',
    sourceHandle: 'source-bottom',
    targetHandle: 'target-left-bottom',
  },
  'chf-smf': {
    label: 'Modelo A',
    stroke: 'solid',
    color: '#2563eb',
    sourceHandle: 'source-left-bottom',
    targetHandle: 'target-bottom',
  },
}

// 2. Diccionario de Interfaces Canónicas 3GPP Rel-16 (SBA Service Bus + User Plane Pipeline)
const sbaInterfacesEdges: Record<string, TelcoEdgeMeta> = {
  // Derivaciones verticales al SBA Service Bus (Fila 1 -> Bus, HTTP/2 REST)
  'nssf-sba-bus': {
    label: 'Nnssf',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'source-bottom',
    targetHandle: 'bus-top-nssf',
    noMarker: true,
  },
  'nrf-sba-bus': {
    label: 'Nnrf',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'source-bottom',
    targetHandle: 'bus-top-nrf',
    noMarker: true,
  },
  'ausf-sba-bus': {
    label: 'Nausf',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'source-bottom',
    targetHandle: 'bus-top-ausf',
    noMarker: true,
  },
  'udm-sba-bus': {
    label: 'Nudm',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'source-bottom',
    targetHandle: 'bus-top-udm',
    noMarker: true,
  },
  'udr-sba-bus': {
    label: 'Nudr',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'source-bottom',
    targetHandle: 'bus-top-udr',
    noMarker: true,
  },
  'pcf-sba-bus': {
    label: 'Npcf',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'source-bottom',
    targetHandle: 'bus-top-pcf',
    noMarker: true,
  },
  'chf-sba-bus': {
    label: 'Nchf',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'source-bottom',
    targetHandle: 'bus-top-chf',
    noMarker: true,
  },
  'nwdaf-sba-bus': {
    label: 'Nnwdaf',
    stroke: 'solid',
    color: '#9333ea',
    sourceHandle: 'source-bottom',
    targetHandle: 'bus-top-nwdaf',
    noMarker: true,
  },
  'af-sba-bus': {
    label: 'Naf',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'source-bottom',
    targetHandle: 'bus-top-af',
    noMarker: true,
  },

  // Derivaciones verticales del SBA Service Bus (Bus -> Fila 2, HTTP/2 REST)
  'sba-bus-amf': {
    label: 'Namf',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'bus-bottom-amf',
    targetHandle: 'target-top',
    noMarker: true,
  },
  'sba-bus-smf': {
    label: 'Nsmf',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'bus-bottom-smf',
    targetHandle: 'target-top',
    noMarker: true,
  },
  'sba-bus-scp': {
    label: 'Nscp (Mesh)',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'bus-bottom-scp',
    targetHandle: 'target-top',
    noMarker: true,
  },
  'sba-bus-bsf': {
    label: 'Nbsf',
    stroke: 'solid',
    color: '#0284c7',
    sourceHandle: 'bus-bottom-bsf',
    targetHandle: 'target-top',
    noMarker: true,
  },

  // Base de Datos BSON (MongoDB <-> UDR)
  'mongodb-udr': {
    label: 'BSON',
    stroke: 'solid',
    color: '#64748b',
    sourceHandle: 'source-bottom',
    targetHandle: 'target-top',
  },
  'udr-mongodb': {
    label: 'BSON',
    stroke: 'solid',
    color: '#64748b',
    sourceHandle: 'source-top',
    targetHandle: 'target-bottom',
  },

  // Control Plano a RAN y UPF (Alineados verticalmente en X = 340 y X = 740)
  'gnb-amf': {
    label: 'N2',
    stroke: 'solid',
    color: '#475569',
    sourceHandle: 'source-top',
    targetHandle: 'target-bottom',
  },
  'smf-upf': {
    label: 'N4',
    stroke: 'solid',
    color: '#475569',
    sourceHandle: 'source-bottom',
    targetHandle: 'target-top',
  },

  // Pipeline Plano de Usuario y Acceso (Fila 3, 100% horizontal de izquierda a derecha)
  'ue-gnb': {
    label: 'NR-Uu',
    stroke: 'solid',
    color: '#059669',
    sourceHandle: 'source-right',
    targetHandle: 'target-left',
  },
  'gnb-upf': {
    label: 'N3',
    stroke: 'solid',
    color: '#059669',
    sourceHandle: 'source-right',
    targetHandle: 'target-left',
  },
  'upf-dn': {
    label: 'N6',
    stroke: 'solid',
    color: '#059669',
    sourceHandle: 'source-right',
    targetHandle: 'target-left',
  },

  // Señalización N1 (NAS) UE -> AMF
  'ue-amf': {
    label: 'N1 (NAS)',
    stroke: 'solid',
    color: '#d97706',
    sourceHandle: 'source-top',
    targetHandle: 'target-left',
  },

  // 4G Fallbacks
  'ue-enb': { label: 'LTE-Uu', stroke: 'solid', color: '#059669' },
  'enb-sgwu': { label: 'S1-U', stroke: 'solid', color: '#059669' },
  'sgwu-upf': { label: 'S5/S8-U', stroke: 'solid', color: '#059669' },
  'enb-mme': { label: 'S1-MME', stroke: 'solid', color: '#475569' },
  'hss-mme': { label: 'S6a', stroke: 'solid', color: '#475569' },
  'mme-sgwc': { label: 'S11', stroke: 'solid', color: '#475569' },
  'sgwc-sgwu': { label: 'S5/S8 Control', stroke: 'solid', color: '#475569' },
  'sgwc-smf': { label: 'S5/S8-C', stroke: 'solid', color: '#475569' },
  'pcrf-smf': { label: 'Gx', stroke: 'solid', color: '#475569' },
  'mongodb-hss': { label: 'BSON', stroke: 'solid', color: '#64748b' },
}

function getComponentIcon(id: string) {
  if (id === 'ue') {
    return <Smartphone className='h-4 w-4 text-sky-500' />
  }
  if (id === 'dn') {
    return <Globe className='h-4 w-4 text-emerald-500' />
  }
  if (id === 'gnb' || id === 'enb') {
    return <Radio className='h-4 w-4 text-indigo-500' />
  }
  if (id === 'upf' || id === 'upf2' || id === 'sgwu') {
    return <Router className='h-4 w-4 text-emerald-500' />
  }
  if (id === 'mongodb') {
    return <Database className='h-4 w-4 text-amber-500' />
  }
  if (id === 'chf') {
    return <Coins className='h-4 w-4 text-amber-500' />
  }
  if (id === 'af') {
    return <Tv className='h-4 w-4 text-blue-500' />
  }
  if (id === 'nwdaf') {
    return <Brain className='h-4 w-4 text-purple-400' />
  }
  return <Server className='h-4 w-4 text-indigo-400' />
}

// Componente de nodo estilizado, sobrio, limpio y profesional (sin handles visibles)
function TelcoNode({ data }: NodeProps) {
  const isRunning = data.status === 'running'
  const id = String(data.id ?? data.label).toLowerCase()
  const icon = getComponentIcon(id)
  const alarmSeverity = data.alarmSeverity as string | undefined
  const alarmCount = Number(data.alarmCount ?? 0)

  let containerBorder = 'border-border/80 hover:border-primary/60 hover:shadow-md'
  let ledColor = 'bg-emerald-500 shadow-[0_0_6px_#10b981]'

  if (!isRunning) {
    containerBorder = 'border-destructive/80 bg-destructive/5'
    ledColor = 'bg-red-500 shadow-[0_0_6px_#ef4444]'
  } else if (alarmSeverity === 'critical') {
    containerBorder = 'border-red-500 ring-1 ring-red-500/40 bg-destructive/5'
    ledColor = 'bg-red-500 shadow-[0_0_6px_#ef4444]'
  } else if (alarmSeverity === 'major') {
    containerBorder = 'border-amber-500 ring-1 ring-amber-500/30 bg-amber-500/5'
    ledColor = 'bg-amber-500 shadow-[0_0_6px_#f59e0b]'
  } else if (alarmSeverity === 'minor' || alarmSeverity === 'warning') {
    containerBorder = 'border-yellow-500/70'
    ledColor = 'bg-yellow-500 shadow-[0_0_6px_#eab308]'
  }

  return (
    <div
      className={`relative flex items-center justify-between rounded-xl border bg-card/95 px-3.5 text-card-foreground shadow-xs transition-all duration-200 select-none ${containerBorder}`}
      style={{ width: 156, height: 50 }}
    >
      {/* Handles para anclaje de conexiones 100% INVISIBLES (sin puntos exteriores antiestéticos) */}
      {[Position.Top, Position.Bottom, Position.Left, Position.Right].flatMap(
        (position) =>
          (['source', 'target'] as const).map((type) => (
            <Handle
              key={`${type}-${position}`}
              id={`${type}-${String(position).toLowerCase()}`}
              type={type}
              position={position}
              className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
              style={{
                opacity: 0,
                width: 0,
                height: 0,
                minWidth: 0,
                minHeight: 0,
                border: 'none',
              }}
            />
          ))
      )}

      {/* Handles desplazados para enrutamiento múltiple limpio sin solapamiento */}
      {(['source', 'target'] as const).flatMap((type) => [
        <Handle
          key={`${type}-right-top`}
          id={`${type}-right-top`}
          type={type}
          position={Position.Right}
          className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
          style={{
            top: '25%',
            opacity: 0,
            width: 0,
            height: 0,
            minWidth: 0,
            minHeight: 0,
            border: 'none',
          }}
        />,
        <Handle
          key={`${type}-right-mid-top`}
          id={`${type}-right-mid-top`}
          type={type}
          position={Position.Right}
          className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
          style={{
            top: '40%',
            opacity: 0,
            width: 0,
            height: 0,
            minWidth: 0,
            minHeight: 0,
            border: 'none',
          }}
        />,
        <Handle
          key={`${type}-right-mid-bottom`}
          id={`${type}-right-mid-bottom`}
          type={type}
          position={Position.Right}
          className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
          style={{
            top: '60%',
            opacity: 0,
            width: 0,
            height: 0,
            minWidth: 0,
            minHeight: 0,
            border: 'none',
          }}
        />,
        <Handle
          key={`${type}-right-bottom`}
          id={`${type}-right-bottom`}
          type={type}
          position={Position.Right}
          className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
          style={{
            top: '75%',
            opacity: 0,
            width: 0,
            height: 0,
            minWidth: 0,
            minHeight: 0,
            border: 'none',
          }}
        />,
        <Handle
          key={`${type}-left-top`}
          id={`${type}-left-top`}
          type={type}
          position={Position.Left}
          className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
          style={{
            top: '25%',
            opacity: 0,
            width: 0,
            height: 0,
            minWidth: 0,
            minHeight: 0,
            border: 'none',
          }}
        />,
        <Handle
          key={`${type}-left-bottom`}
          id={`${type}-left-bottom`}
          type={type}
          position={Position.Left}
          className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
          style={{
            top: '75%',
            opacity: 0,
            width: 0,
            height: 0,
            minWidth: 0,
            minHeight: 0,
            border: 'none',
          }}
        />,
      ])}

      {/* Centro: Icono destacado y Sigla grande y legible */}
      <div className='flex min-w-0 items-center gap-2.5'>
        <div className='shrink-0 [&>svg]:h-5 [&>svg]:w-5'>{icon}</div>
        <span className='truncate font-mono text-base font-black tracking-tight text-foreground'>
          {String(data.label)}
        </span>
      </div>

      {/* Estado LED y Alarmas */}
      <div className='flex shrink-0 items-center gap-1.5'>
        {alarmSeverity && alarmCount > 0 ? (
          <span className='rounded bg-red-600 px-1 font-mono text-[8px] font-bold text-white uppercase'>
            {alarmSeverity === 'critical' ? 'CRIT' : 'ALARM'}
          </span>
        ) : null}
        <span className={`size-2.5 rounded-full ${ledColor}`} />
      </div>
    </div>
  )
}

function getHostIcon(id: string) {
  if (id === 'ue-vm') return <Smartphone className='h-4 w-4 text-sky-500' />
  if (id === 'gnb-vm') return <Radio className='h-4 w-4 text-indigo-500' />
  if (id === 'upf-vm') return <Router className='h-4 w-4 text-emerald-500' />
  if (id === 'upf-vm2') return <Router className='h-4 w-4 text-cyan-500' />
  return <Server className='h-4 w-4 text-violet-500' />
}

function HostNode({ data }: NodeProps) {
  const isHealthy = data.healthy !== false
  const ledStyle = isHealthy
    ? 'bg-emerald-500 shadow-[0_0_6px_#10b981]'
    : 'bg-red-500 shadow-[0_0_6px_#ef4444]'

  const id = String(data.id || '')
  const rawLabel = String(data.label || '')
  let shortTitle = rawLabel
  if (id === 'ue-vm' || rawLabel.includes('ue-01'))
    shortTitle = 'EMS-UE-01'
  else if (id === 'gnb-vm' || rawLabel.includes('gnb-01'))
    shortTitle = 'EMS-GNB-01'
  else if (id === 'core' || rawLabel.includes('testbed'))
    shortTitle = 'EMS-CORE'
  else if (id === 'upf-vm' || rawLabel.includes('upf-01'))
    shortTitle = 'EMS-UPF-01'
  else if (id === 'upf-vm2' || rawLabel.includes('upf-02'))
    shortTitle = 'EMS-UPF-02'

  let shortRole = String(data.role || 'Host VM')
  if (id === 'ue-vm') shortRole = 'UE (Dual PDU)'
  else if (id === 'gnb-vm') shortRole = 'gNodeB (RAN)'
  else if (id === 'core') shortRole = '5G Core (CP)'
  else if (id === 'upf-vm') shortRole = 'UPF (Internet)'
  else if (id === 'upf-vm2') shortRole = 'UPF (Corporate)'

  const icon = getHostIcon(id)

  return (
    <div
      className='relative flex cursor-pointer flex-col justify-between rounded-xl border border-border/80 bg-card/95 p-2.5 text-card-foreground shadow-xs transition-all duration-200 select-none hover:border-primary/60 hover:shadow-md'
      style={{ width: 190, height: 60 }}
    >
      {[Position.Top, Position.Bottom, Position.Left, Position.Right].flatMap(
        (position) =>
          (['source', 'target'] as const).map((type) => (
            <Handle
              key={`${type}-${position}`}
              id={`${type}-${String(position).toLowerCase()}`}
              type={type}
              position={position}
              className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
              style={{
                opacity: 0,
                width: 0,
                height: 0,
                minWidth: 0,
                minHeight: 0,
                border: 'none',
              }}
            />
          ))
      )}

      {/* Header: Icono + Hostname + Status LED */}
      <div className='flex items-center justify-between gap-1.5'>
        <div className='flex min-w-0 items-center gap-2'>
          <div className='shrink-0'>{icon}</div>
          <span className='truncate font-mono text-xs font-bold tracking-tight text-foreground'>
            {shortTitle}
          </span>
        </div>
        <span className={`size-2 rounded-full ${ledStyle} shrink-0`} />
      </div>

      {/* Footer: IP + Rol */}
      <div className='flex items-center justify-between gap-1 font-mono text-[10px]'>
        <span className='truncate text-muted-foreground/80'>
          {String(data.ip || 'Sin IP')}
        </span>
        <span className='shrink-0 rounded bg-muted/70 px-1.5 py-0.5 font-sans text-[9.5px] font-medium text-foreground/80'>
          {shortRole}
        </span>
      </div>
    </div>
  )
}

function CustomTelcoEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  data,
  label,
  markerEnd,
  markerStart,
}: EdgeProps) {
  const isHorizontal = Math.abs(targetY - sourceY) < 2
  const isVertical = Math.abs(targetX - sourceX) < 2

  let edgePath = ''
  let labelX = 0
  let labelY = 0

  if (isHorizontal || isVertical) {
    const [path, lx, ly] = getStraightPath({
      sourceX,
      sourceY,
      targetX,
      targetY,
    })
    edgePath = path
    labelX = lx
    labelY = ly
  } else {
    const [path, lx, ly] = getSmoothStepPath({
      sourceX,
      sourceY,
      sourcePosition,
      targetX,
      targetY,
      targetPosition,
    })
    edgePath = path
    labelX = lx
    labelY = ly
  }

  // Si data?.labelNearTarget es true, colocamos el badge en el tramo horizontal previo al nodo destino
  const lx = data?.labelNearTarget ? targetX - 70 : labelX
  const ly = data?.labelNearTarget ? targetY : labelY

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={style}
        markerEnd={markerEnd}
        markerStart={markerStart}
      />
      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${lx}px,${ly}px)`,
              pointerEvents: 'all',
            }}
            className='nodrag nopan rounded-md border border-border/80 bg-card/95 px-2 py-0.5 font-sans text-[10px] font-semibold text-foreground shadow-xs select-none backdrop-blur-xs'
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  )
}

function SbaBusNode() {
  const topTaps = [
    { id: 'nssf', x: 118 - 20 },
    { id: 'nrf', x: 318 - 20 },
    { id: 'ausf', x: 518 - 20 },
    { id: 'udm', x: 718 - 20 },
    { id: 'udr', x: 918 - 20 },
    { id: 'pcf', x: 1118 - 20 },
    { id: 'chf', x: 1318 - 20 },
    { id: 'nwdaf', x: 1518 - 20 },
    { id: 'af', x: 1718 - 20 },
  ]

  const bottomTaps = [
    { id: 'amf', x: 418 - 20 },
    { id: 'smf', x: 818 - 20 },
    { id: 'scp', x: 1218 - 20 },
    { id: 'bsf', x: 1418 - 20 },
  ]

  return (
    <div
      className='relative flex items-center justify-center rounded-xl border border-primary/30 bg-card/90 px-6 py-1.5 shadow-sm backdrop-blur-md select-none'
      style={{ width: 1780, height: 32 }}
    >
      {topTaps.map((tap) => (
        <Fragment key={`bus-top-${tap.id}`}>
          <Handle
            id={`bus-top-${tap.id}`}
            type='target'
            position={Position.Top}
            className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
            style={{
              left: `${tap.x}px`,
              opacity: 0,
              width: 0,
              height: 0,
              minWidth: 0,
              minHeight: 0,
              border: 'none',
            }}
          />
          <Handle
            id={`bus-top-src-${tap.id}`}
            type='source'
            position={Position.Top}
            className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
            style={{
              left: `${tap.x}px`,
              opacity: 0,
              width: 0,
              height: 0,
              minWidth: 0,
              minHeight: 0,
              border: 'none',
            }}
          />
        </Fragment>
      ))}

      {bottomTaps.map((tap) => (
        <Fragment key={`bus-bottom-${tap.id}`}>
          <Handle
            id={`bus-bottom-${tap.id}`}
            type='source'
            position={Position.Bottom}
            className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
            style={{
              left: `${tap.x}px`,
              opacity: 0,
              width: 0,
              height: 0,
              minWidth: 0,
              minHeight: 0,
              border: 'none',
            }}
          />
          <Handle
            id={`bus-bottom-tgt-${tap.id}`}
            type='target'
            position={Position.Bottom}
            className='!opacity-0 !w-0 !h-0 !border-0 pointer-events-none'
            style={{
              left: `${tap.x}px`,
              opacity: 0,
              width: 0,
              height: 0,
              minWidth: 0,
              minHeight: 0,
              border: 'none',
            }}
          />
        </Fragment>
      ))}

      <div className='flex items-center gap-3 font-mono text-[11px] font-bold tracking-widest text-primary/85 uppercase'>
        <span className='size-1.5 rounded-full bg-primary/70 animate-pulse' />
        <span>Service-Based Architecture Bus (3GPP TS 23.501 SBI · HTTP/2 REST)</span>
        <span className='size-1.5 rounded-full bg-primary/70 animate-pulse' />
      </div>
    </div>
  )
}

const nodeTypes = {
  telcoNode: TelcoNode,
  hostNode: HostNode,
  sbaBusNode: SbaBusNode,
}

const edgeTypes = {
  telcoEdge: CustomTelcoEdge,
}

function telcoElements(
  components: ComponentStatus[],
  alarms?: NodeAlarmItem[],
  view: TopologyView = 'models'
) {
  const is5g = components.some((c) => c.id === 'amf' || c.id === 'gnb')
  const isInterfaces = view === 'interfaces'
  const posMap = isInterfaces
    ? (is5g ? telcoPositions5G : telcoPositions4G)
    : sbaModelsPositions5G
  const edgeDict = isInterfaces ? sbaInterfacesEdges : sbaModelsEdges

  // En la vista de Modelos SBA, mostramos las funciones core de la arquitectura SBA
  // (AMF, SMF, NRF, SCP, UDM, BSF, PCF, CHF, AF y NWDAF)
  const sbaCoreNfs = new Set([
    'amf',
    'smf',
    'nrf',
    'scp',
    'udm',
    'bsf',
    'pcf',
    'chf',
    'af',
    'nwdaf',
  ])

  const hasAf = components.some((c) => c.id === 'af')
  const hasNwdaf = components.some((c) => c.id === 'nwdaf')
  const extras: ComponentStatus[] = []

  if (is5g && !hasAf) {
    extras.push({
      id: 'af',
      label: 'AF',
      kind: 'application',
      node_id: 'core',
      unit: 'maestro-video-af',
      interfaces: ['N5', 'N6'],
      status: 'running',
      procedures: [
        'Npcf_PolicyAuthorization (N5 / HTTP/2 REST)',
        'Modelo A: Comunicación directa sin NRF (3GPP TS 23.501 Cl. 7.1.4)',
        'Servidor de Video Streaming HLS / fMP4 (N6 / Plano de Usuario)',
        'Control Dinámico de Sesión y QoS Boost (5QI=2, GBR 10M / MBR 20M)',
      ],
      expected_endpoints: [
        {
          interface: 'N5',
          protocol: 'http2',
          address: '10.210.50.1',
          port: 7777,
        },
        {
          interface: 'N6',
          protocol: 'http',
          address: '10.210.50.1',
          port: 18090,
        },
      ],
      config_paths: [],
      depends_on: ['pcf'],
    } as ComponentStatus)
  }

  if (is5g && !hasNwdaf) {
    extras.push({
      id: 'nwdaf',
      label: 'NWDAF',
      kind: 'analytics',
      node_id: 'core',
      unit: 'maestro-nwdaf',
      interfaces: ['Nnwdaf', 'N23'],
      status: 'running',
      procedures: [
        'Nnwdaf_AnalyticsInfo (3GPP TS 29.520 Cl. 5.2 / HTTP/2 REST)',
        'Nnwdaf_EventsSubscription (3GPP TS 29.520 Cl. 5.3 / HTTP/2 REST)',
        'Control en Bucle Cerrado Autónomo (Closed-Loop) con PCF (3GPP TS 23.288)',
        'Inferencia de IA/ML: SLICE_LOAD_LEVEL, ABNORMAL_BEHAVIOUR, SERVICE_EXPERIENCE',
      ],
      expected_endpoints: [
        {
          interface: 'Nnwdaf',
          protocol: 'http2',
          address: '10.210.50.1',
          port: 9095,
        },
        {
          interface: 'N23',
          protocol: 'http2',
          address: '10.210.50.1',
          port: 9095,
        },
      ],
      config_paths: ['/etc/open5gs/nwdaf.yaml'],
      depends_on: ['pcf', 'mongodb', 'chf'],
    } as ComponentStatus)
  }

  const hasDn = components.some((c) => c.id === 'dn')
  if (is5g && isInterfaces && !hasDn) {
    extras.push({
      id: 'dn',
      label: 'DN',
      kind: 'data-network',
      node_id: 'core',
      unit: 'data-network',
      interfaces: ['N6'],
      status: 'running',
      procedures: [
        'Red de Datos Externa (Data Network / Internet & Corporativo)',
        'Conexión N6 sobre subredes 10.45.0.0/16 y 10.46.0.0/16',
        'Enrutamiento hacia Servicios IP, Servidor de Video AF y Tráfico Empresarial',
      ],
      expected_endpoints: [
        {
          interface: 'N6',
          protocol: 'ip',
          address: '10.210.50.1',
          port: 0,
        },
      ],
      config_paths: [],
      depends_on: ['upf'],
    } as ComponentStatus)
  }

  const baseComponents = extras.length > 0 ? [...components, ...extras] : components

  const sourceComponents = isInterfaces
    ? baseComponents
    : baseComponents.filter((c) => sbaCoreNfs.has(c.id))

  // En la vista lógica 3GPP, colapsamos instancias del plano de usuario (ej. 2 UPFs)
  // y del Session Management (ej. 2 SMFs) en un único nodo arquitectural para mantener un diagrama canónico.
  const upfComps = sourceComponents.filter(
    (c) => c.id === 'upf' || c.id === 'upf2' || c.kind === 'user-plane'
  )
  const hasMultipleUpfs = is5g && upfComps.length > 1

  const smfComps = sourceComponents.filter(
    (c) => c.id === 'smf' || c.id === 'smf2'
  )
  const hasMultipleSmfs = is5g && smfComps.length > 1

  let displayComponents: ComponentStatus[] = sourceComponents
  if (hasMultipleUpfs) {
    const nonUpfs = displayComponents.filter(
      (c) => !(c.id === 'upf' || c.id === 'upf2' || c.kind === 'user-plane')
    )
    const allRunning = upfComps.every((c) => c.status === 'running')
    const aggregatedUpf: ComponentStatus = {
      id: 'upf',
      label: 'UPF',
      kind: 'user-plane',
      node_id: 'upf-cluster',
      unit: 'open5gs-upfd',
      interfaces: ['N3', 'N4', 'N6'],
      status: allRunning ? 'running' : 'stopped',
      procedures: [
        'Plano de Usuario Desagregado (CUPS)',
        'Soporte Multi-Slice: Internet (eMBB) + Corporativo (MEC)',
      ],
      expected_endpoints: upfComps.flatMap((c) => c.expected_endpoints || []),
      config_paths: ['/etc/open5gs/upf.yaml'],
      depends_on: ['smf'],
    }
    displayComponents = [...nonUpfs, aggregatedUpf]
  }

  if (hasMultipleSmfs) {
    const nonSmfs = displayComponents.filter(
      (c) => !(c.id === 'smf' || c.id === 'smf2')
    )
    const allRunning = smfComps.every((c) => c.status === 'running')
    const aggregatedSmf: ComponentStatus = {
      id: 'smf',
      label: 'SMF',
      kind: 'core',
      node_id: 'core',
      unit: 'open5gs-smfd',
      interfaces: ['N4', 'SBI'],
      status: allRunning ? 'running' : 'stopped',
      procedures: [
        'Aislamiento en Plano de Control (3GPP Rel-16 Dual-SMF)',
        'Soporte Multi-Slice: SMF-01 (Internet eMBB) + SMF-02 (Corporate MEC)',
      ],
      expected_endpoints: smfComps.flatMap((c) => c.expected_endpoints || []),
      config_paths: ['/etc/open5gs/smf.yaml'],
      depends_on: ['nrf', 'pcf'],
    }
    displayComponents = [...nonSmfs, aggregatedSmf]
  }

  const nodes: Node[] = displayComponents.map((component, idx) => {
    const defaultPos = { x: 50 + (idx % 4) * 200, y: Math.floor(idx / 4) * 120 }
    const pos = posMap[component.id] ?? defaultPos

    const compAlarms = (alarms ?? []).filter((a) => {
      if (component.id === 'upf' && hasMultipleUpfs) {
        return upfComps.some(
          (u) =>
            a.component === u.id ||
            a.component?.toLowerCase() === u.id.toLowerCase() ||
            (a.node_id &&
              (a.node_id === u.node_id ||
                a.node_id === 'upf-vm' ||
                a.node_id === 'upf-vm2'))
        )
      }
      if (component.id === 'smf' && hasMultipleSmfs) {
        return smfComps.some(
          (s) =>
            a.component === s.id ||
            a.component?.toLowerCase() === s.id.toLowerCase()
        )
      }
      return alarmBelongsToComponent(a, component)
    })
    const hasCritical = compAlarms.some((a) => a.severity === 'critical')
    const hasMajor = compAlarms.some((a) => a.severity === 'major')
    const hasMinor = compAlarms.some((a) => a.severity === 'minor')
    const hasWarning = compAlarms.some((a) => a.severity === 'warning')
    const highestSeverity = hasCritical
      ? 'critical'
      : hasMajor
        ? 'major'
        : hasMinor
          ? 'minor'
          : hasWarning
            ? 'warning'
            : null

    const subtitle =
      component.id === 'upf' && hasMultipleUpfs
        ? `${upfComps.length} Instancias (Internet + Corp)`
        : component.id === 'smf' && hasMultipleSmfs
          ? `${smfComps.length} Instancias (Dual-SMF)`
          : undefined

    return {
      id: component.id,
      type: 'telcoNode',
      position: pos,
      data: {
        selectionType: 'component',
        id: component.id,
        label: component.label,
        kind: component.kind,
        status: component.status,
        subtitle,
        alarmSeverity: highestSeverity,
        alarmCount: compAlarms.length,
      },
    }
  })

  // En la vista Canónica de Interfaces 3GPP Rel-16, insertamos el SBA Service Bus
  if (isInterfaces && is5g) {
    nodes.push({
      id: 'sba-bus',
      type: 'sbaBusNode',
      position: { x: 20, y: 175 },
      data: {
        id: 'sba-bus',
        label: 'SBA Service Bus',
        selectionType: 'bus',
      },
      selectable: false,
      draggable: false,
    })
  }

  const edges: Edge[] = []
  const directTelcoLinks = !is5g
    ? [
        { from: 'ue', to: 'enb' },
        { from: 'enb', to: 'sgwu' },
        { from: 'enb', to: 'mme' },
        { from: 'hss', to: 'mme' },
        { from: 'mme', to: 'sgwc' },
        { from: 'sgwc', to: 'sgwu' },
        { from: 'sgwc', to: 'smf' },
        { from: 'sgwu', to: 'upf' },
        { from: 'pcrf', to: 'smf' },
        { from: 'mongodb', to: 'hss' },
      ]
    : isInterfaces
      ? [
          // Pipeline Plano de Usuario y Acceso (Fila 3, 100% horizontal de izquierda a derecha)
          { from: 'ue', to: 'gnb' },
          { from: 'gnb', to: 'upf' },
          { from: 'upf', to: 'dn' },

          // Control Plano a RAN y UPF (Alineados verticalmente en X = 340 y X = 740)
          { from: 'gnb', to: 'amf' },
          { from: 'smf', to: 'upf' },

          // Señalización N1 NAS (UE -> AMF)
          { from: 'ue', to: 'amf' },

          // Base de Datos BSON (MongoDB -> UDR)
          { from: 'mongodb', to: 'udr' },

          // Derivaciones verticales al SBA Service Bus (Fila 1 -> Bus, HTTP/2 REST)
          { from: 'nssf', to: 'sba-bus' },
          { from: 'nrf', to: 'sba-bus' },
          { from: 'ausf', to: 'sba-bus' },
          { from: 'udm', to: 'sba-bus' },
          { from: 'udr', to: 'sba-bus' },
          { from: 'pcf', to: 'sba-bus' },
          { from: 'chf', to: 'sba-bus' },
          { from: 'nwdaf', to: 'sba-bus' },
          { from: 'af', to: 'sba-bus' },

          // Derivaciones verticales del SBA Service Bus (Bus -> Fila 2, HTTP/2 REST)
          { from: 'sba-bus', to: 'amf' },
          { from: 'sba-bus', to: 'smf' },
          { from: 'sba-bus', to: 'scp' },
          { from: 'sba-bus', to: 'bsf' },
        ]
      : [
          // 1. Modelo A directo sin NRF (AF ──── PCF) · 3GPP TS 23.501 Cl. 7.1.4
          { from: 'af', to: 'pcf' },

          // 2. Modelo A directo Closed-Loop IA (PCF ──── NWDAF) · 3GPP TS 23.288
          { from: 'pcf', to: 'nwdaf' },

          // 3. Modelo A directo Cobro Convergente por Slice (SMF ──── CHF) · 3GPP TS 32.290
          { from: 'smf', to: 'chf' },

          // 4. Modelo B directo Productor-Consumidor (AMF ──── SMF)
          { from: 'amf', to: 'smf' },

          // 5. Hub Central NRF ──── SCP (Sincronización de Perfiles y Enrutamiento)
          { from: 'nrf', to: 'scp' },

          // 6. Modelo D Señalización Indirecta Delegada vía SCP Mesh
          { from: 'amf', to: 'scp' },
          { from: 'smf', to: 'scp' },
          { from: 'scp', to: 'udm' },
          { from: 'scp', to: 'bsf' },
          { from: 'scp', to: 'pcf' },
          { from: 'pcf', to: 'scp' },
        ]

  for (const link of directTelcoLinks) {
    const hasSource =
      link.from === 'sba-bus' || displayComponents.some((c) => c.id === link.from)
    const hasTarget =
      link.to === 'sba-bus' || displayComponents.some((c) => c.id === link.to)
    if (!hasSource || !hasTarget) continue

    const sourceNode = nodes.find((node) => node.id === link.from)
    const targetNode = nodes.find((node) => node.id === link.to)
    if (!sourceNode || !targetNode) continue

    const forwardKey = `${link.from}-${link.to}`
    const reverseKey = `${link.to}-${link.from}`
    const info =
      edgeDict[forwardKey] ??
      edgeDict[reverseKey] ?? {
        label: 'Control',
        stroke: 'solid',
        color: '#475569',
      }

    const sourcePosition = sourceNode.position
    const targetPosition = targetNode.position
    const isPureHorizontal = Math.abs(targetPosition.y - sourcePosition.y) < 2
    const isPureVertical = Math.abs(targetPosition.x - sourcePosition.x) < 2

    let sourceSide: Position
    let targetSide: Position
    let edgeType: string = 'straight'

    if (isPureHorizontal) {
      sourceSide =
        targetPosition.x >= sourcePosition.x ? Position.Right : Position.Left
      targetSide =
        targetPosition.x >= sourcePosition.x ? Position.Left : Position.Right
    } else if (isPureVertical) {
      sourceSide =
        targetPosition.y >= sourcePosition.y ? Position.Bottom : Position.Top
      targetSide =
        targetPosition.y >= sourcePosition.y ? Position.Top : Position.Bottom
    } else {
      sourceSide =
        targetPosition.x >= sourcePosition.x ? Position.Right : Position.Left
      targetSide =
        targetPosition.x >= sourcePosition.x ? Position.Left : Position.Right
    }

    if (info.labelNearTarget) {
      edgeType = 'telcoEdge'
    } else if (info.type) {
      edgeType = info.type
    } else if (isPureHorizontal || isPureVertical) {
      edgeType = 'straight'
    } else {
      edgeType = 'smoothstep'
    }

    const sourceHandle =
      info.sourceHandle ?? `source-${String(sourceSide).toLowerCase()}`
    const targetHandle =
      info.targetHandle ?? `target-${String(targetSide).toLowerCase()}`

    const color = info.color || '#475569'

    edges.push({
      id: forwardKey,
      source: link.from,
      target: link.to,
      sourceHandle,
      targetHandle,
      type: edgeType,
      data: {
        labelNearTarget: Boolean(info.labelNearTarget),
      },
      animated: false,
      markerEnd: info.noMarker
        ? undefined
        : {
            type: MarkerType.ArrowClosed,
            width: 14,
            height: 14,
            color,
          },
      markerStart: info.bidirectional
        ? {
            type: MarkerType.ArrowClosed,
            width: 14,
            height: 14,
            color,
          }
        : undefined,
      label: info.label ? info.label : undefined,
      labelStyle: info.label
        ? {
            fontSize: 10,
            fontWeight: 600,
            fill: 'var(--foreground)',
            fontFamily: 'ui-sans-serif, system-ui, sans-serif',
          }
        : undefined,
      labelBgPadding: info.label ? [6, 3] : undefined,
      labelBgBorderRadius: info.label ? 6 : undefined,
      labelBgStyle: info.label
        ? {
            fill: 'var(--card)',
            stroke: 'var(--border)',
            strokeWidth: 1.2,
          }
        : undefined,
      style: {
        stroke: color,
        strokeWidth: 2,
      },
    })
  }

  return { nodes, edges }
}

function physicalElements(
  components: ComponentStatus[],
  runtime?: RuntimeSnapshot
) {
  if (!runtime) return { nodes: [], edges: [] }

  const rawHosts =
    runtime.hosts && runtime.hosts.length > 1 ? runtime.hosts : null

  if (rawHosts) {
    const nodes: Node[] = rawHosts.map((host, idx) => {
      const hostComps = components.filter((c) => {
        if (host.id === 'upf-vm')
          return c.node_id === 'upf-vm' || c.id === 'upf'
        if (host.id === 'upf-vm2')
          return c.node_id === 'upf-vm2' || c.id === 'upf2'
        if (host.id === 'gnb-vm')
          return c.node_id === 'gnb-vm' || c.id === 'gnb'
        if (host.id === 'ue-vm') return c.node_id === 'ue-vm' || c.id === 'ue'
        return (
          c.node_id === 'core' ||
          (!['upf-vm', 'upf-vm2', 'gnb-vm', 'ue-vm'].includes(c.node_id) &&
            !['upf', 'upf2', 'gnb', 'ue'].includes(c.id))
        )
      })
      const active = hostComps.filter((c) => c.status === 'running').length
      const healthy = active === hostComps.length && hostComps.length > 0

      // Layout amplio y proporcional sin solapamiento
      let pos = { x: 80 + idx * 300, y: 190 }
      if (rawHosts.length === 5) {
        if (host.id === 'ue-vm') pos = { x: 60, y: 200 }
        else if (host.id === 'gnb-vm') pos = { x: 370, y: 200 }
        else if (host.id === 'core') pos = { x: 680, y: 200 }
        else if (host.id === 'upf-vm') pos = { x: 990, y: 110 }
        else if (host.id === 'upf-vm2') pos = { x: 990, y: 290 }
      } else if (rawHosts.length === 3) {
        if (idx === 0) pos = { x: 100, y: 200 }
        else if (idx === 1) pos = { x: 500, y: 110 }
        else if (idx === 2) pos = { x: 500, y: 290 }
      }

      const defaultRole =
        host.id === 'ue-vm'
          ? 'UE (Dual PDU)'
          : host.id === 'gnb-vm'
            ? 'gNodeB (RAN)'
            : host.id === 'upf-vm2'
              ? 'UPF (Corporate)'
              : host.id === 'upf-vm'
                ? 'UPF (Internet)'
                : '5G Core (CP)'

      const defaultIp =
        host.id === 'ue-vm'
          ? '10.210.50.11'
          : host.id === 'gnb-vm'
            ? '10.210.50.10'
            : host.id === 'upf-vm2'
              ? '10.210.50.9'
              : host.id === 'upf-vm'
                ? '10.210.50.8'
                : '10.210.50.1'

      return {
        id: host.id,
        type: 'hostNode',
        position: pos,
        data: {
          selectionType: 'host',
          id: host.id,
          label: host.hostname,
          role: defaultRole,
          ip: host.ip || defaultIp,
          nfSummary: `${active}/${hostComps.length} NFs activas`,
          healthy,
        },
      }
    })

    const edges: Edge[] = []
    if (rawHosts.length === 5) {
      const links = [
        {
          id: 'phys-ue-gnb',
          source: 'ue-vm',
          target: 'gnb-vm',
          sourceHandle: 'source-right',
          targetHandle: 'target-left',
          label: 'Radio Sim',
          stroke: '#0284c7',
          type: 'straight',
        },
        {
          id: 'phys-gnb-core',
          source: 'gnb-vm',
          target: 'core',
          sourceHandle: 'source-right',
          targetHandle: 'target-left',
          label: 'N2 (NGAP)',
          stroke: '#475569',
          type: 'straight',
        },
        {
          id: 'phys-core-upf1',
          source: 'core',
          target: 'upf-vm',
          sourceHandle: 'source-right',
          targetHandle: 'target-left',
          label: 'N4 (Internet)',
          stroke: '#059669',
          type: 'smoothstep',
        },
        {
          id: 'phys-core-upf2',
          source: 'core',
          target: 'upf-vm2',
          sourceHandle: 'source-right',
          targetHandle: 'target-left',
          label: 'N4 (Corporate)',
          stroke: '#0891b2',
          type: 'smoothstep',
        },
      ]

      for (const link of links) {
        edges.push({
          id: link.id,
          source: link.source,
          target: link.target,
          sourceHandle: link.sourceHandle,
          targetHandle: link.targetHandle,
          type: link.type,
          animated: false,
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 12,
            height: 12,
            color: link.stroke,
          },
          label: link.label,
          labelStyle: {
            fontSize: 10,
            fontWeight: 600,
            fill: 'var(--foreground)',
            fontFamily: 'ui-sans-serif, system-ui, sans-serif',
          },
          labelBgPadding: [6, 2.5],
          labelBgBorderRadius: 6,
          labelBgStyle: {
            fill: 'var(--card)',
            stroke: 'var(--border)',
            strokeWidth: 1.2,
          },
          style: {
            stroke: link.stroke,
            strokeWidth: 2,
          },
        })
      }
    } else {
      const coreHost = rawHosts[0]
      for (let i = 1; i < rawHosts.length; i++) {
        const targetHost = rawHosts[i]
        const isUpf2 =
          targetHost.id === 'upf-vm2' || targetHost.ip === '10.210.50.9'
        const color = isUpf2 ? '#0891b2' : '#059669'
        edges.push({
          id: `host-link-${targetHost.id}`,
          source: coreHost.id,
          target: targetHost.id,
          sourceHandle: 'source-right',
          targetHandle: 'target-left',
          type: 'smoothstep',
          animated: false,
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 12,
            height: 12,
            color,
          },
          label: isUpf2 ? 'N4 (Corporate)' : 'N4 (Internet)',
          labelStyle: {
            fontSize: 10,
            fontWeight: 600,
            fill: 'var(--foreground)',
            fontFamily: 'ui-sans-serif, system-ui, sans-serif',
          },
          labelBgPadding: [6, 2.5],
          labelBgBorderRadius: 6,
          labelBgStyle: {
            fill: 'var(--card)',
            stroke: 'var(--border)',
            strokeWidth: 1.2,
          },
          style: {
            stroke: color,
            strokeWidth: 2,
          },
        })
      }
    }

    return { nodes, edges }
  }

  const active = components.filter(
    (component) => component.status === 'running'
  ).length
  const address = runtime.interfaces
    .flatMap((item) => item.addresses)
    .find((item) => item.family === 'inet' && !item.address.startsWith('127.'))

  const node: Node = {
    id: runtime.hostname,
    type: 'hostNode',
    position: { x: 320, y: 180 },
    data: {
      selectionType: 'host',
      id: runtime.hostname,
      label: runtime.hostname,
      role: 'Host Testbed',
      ip: address?.address ?? 'Sin IP',
      nfSummary: `${active}/${components.length} NFs activas`,
      healthy: active === components.length,
    },
  }
  return { nodes: [node], edges: [] }
}

export function EmsTopology({
  components,
  runtime,
  alarms,
  view = 'telco',
  onSelect,
}: {
  components: ComponentStatus[]
  runtime?: RuntimeSnapshot
  alarms?: NodeAlarmItem[]
  view?: TopologyView
  onSelect?: (selection: TopologySelection) => void
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([])

  const prevViewRef = useRef(view)
  useEffect(() => {
    const isViewChange = prevViewRef.current !== view
    prevViewRef.current = view

    const next =
      view === 'physical'
        ? physicalElements(components, runtime)
        : telcoElements(components, alarms, view)
    setNodes((current) => {
      if (isViewChange) {
        return next.nodes
      }
      const positions = new Map(current.map((node) => [node.id, node.position]))
      return next.nodes.map((node) => ({
        ...node,
        position: positions.get(node.id) ?? node.position,
      }))
    })
    setEdges(next.edges)
  }, [components, runtime, alarms, setEdges, setNodes, view])

  const handleNodeClick: NodeMouseHandler = (_, node) => {
    if (node.id === 'sba-bus') return
    const type = node.data.selectionType === 'host' ? 'host' : 'component'
    onSelect?.({ type, id: node.id })
  }

  return (
    <ReactFlow
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={handleNodeClick}
      nodesDraggable
      nodesConnectable={false}
      proOptions={{ hideAttribution: true }}
      fitView
      fitViewOptions={{ padding: 0.08, duration: 0 }}
      minZoom={0.2}
      maxZoom={1.8}
    >
      <Background color='var(--border)' gap={20} size={1} />
      <Controls
        showInteractive={false}
        className='overflow-hidden !rounded-lg !border !border-border !bg-card !shadow-md [&>button]:!border-border [&>button]:!bg-card [&>button]:!fill-foreground [&>button:hover]:!bg-muted'
      />
      <TopologyViewportController view={view} nodesCount={nodes.length} />
    </ReactFlow>
  )
}

function TopologyViewportController({
  view,
  nodesCount,
}: {
  view?: TopologyView
  nodesCount: number
}) {
  const { fitView } = useReactFlow()
  const lastFittedViewRef = useRef<TopologyView | undefined | null>(null)

  useEffect(() => {
    if (nodesCount > 0 && lastFittedViewRef.current !== view) {
      const raf = requestAnimationFrame(() => {
        fitView({ padding: 0.08, duration: 0 })
        lastFittedViewRef.current = view
      })
      return () => cancelAnimationFrame(raf)
    }
  }, [view, nodesCount, fitView])

  useEffect(() => {
    const handleResize = () => {
      fitView({ padding: 0.08, duration: 0 })
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [fitView])

  return null
}
