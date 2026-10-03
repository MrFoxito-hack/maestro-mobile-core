import {
  type LucideIcon,
  Activity,
  Cpu,
  Layers,
  Network,
  Radio,
  Server,
  Shield,
  Zap,
} from 'lucide-react'
import type { Aggregation, KpiCounter, KpiObject } from './types'

export interface PerformanceTemplate {
  id: string
  title: string
  subtitle: string
  nf:
    | 'upf'
    | 'amf'
    | 'smf'
    | 'pcf'
    | 'ran'
    | 'host'
    | 'mme'
    | 'sgw'
    | 'procedures'
  nfName: string
  icon: LucideIcon
  scenario: '5g-sa' | '4g-epc' | 'both'
  matchObjects: (obj: KpiObject) => boolean
  defaultObjectIds: string[]
  matchCounters: (counter: KpiCounter) => boolean
  defaultCounterIds: string[]
  defaultAggregation?: Aggregation
  defaultRangeKey?: '15m' | '1h' | '6h' | '24h'
}

export interface NfGroup {
  key: PerformanceTemplate['nf']
  name: string
  code: string
  icon: LucideIcon
  color: string
}

export const NF_GROUPS: Record<string, NfGroup> = {
  procedures: {
    key: 'procedures',
    name: 'Procedimientos globales',
    code: 'PROCEDIMIENTOS',
    icon: Activity,
    color: 'text-muted-foreground',
  },
  upf: {
    key: 'upf',
    name: 'UPF (User Plane / UPF)',
    code: 'UPF',
    icon: Network,
    color: 'text-amber-500',
  },
  amf: {
    key: 'amf',
    name: 'AMF (Control Plane / AMF)',
    code: 'AMF',
    icon: Shield,
    color: 'text-blue-500',
  },
  smf: {
    key: 'smf',
    name: 'SMF (Session Management)',
    code: 'SMF',
    icon: Layers,
    color: 'text-emerald-500',
  },
  pcf: {
    key: 'pcf',
    name: 'SBI (Service Based Core)',
    code: 'SBI',
    icon: Zap,
    color: 'text-purple-500',
  },
  ran: {
    key: 'ran',
    name: 'RAN (gNodeB / Acceso Radio)',
    code: 'RAN',
    icon: Radio,
    color: 'text-rose-500',
  },
  host: {
    key: 'host',
    name: 'HOST (Infraestructura & Testbed)',
    code: 'HOST',
    icon: Server,
    color: 'text-slate-400',
  },
  mme: {
    key: 'mme',
    name: 'MME (Mobility Management 4G)',
    code: 'MME',
    icon: Shield,
    color: 'text-blue-500',
  },
  sgw: {
    key: 'sgw',
    name: 'GW (Gateways 4G)',
    code: 'GW',
    icon: Network,
    color: 'text-amber-500',
  },
}

export const PERFORMANCE_TEMPLATES: PerformanceTemplate[] = [
  {
    id: 'procedure-registration',
    title: 'Registration global',
    subtitle:
      'Métricas globales derivadas de logs; no atribuibles a una instancia AMF',
    nf: 'procedures',
    nfName: 'Procedimientos globales',
    icon: Activity,
    scenario: '5g-sa',
    matchObjects: (o) => o.id === 'procedure:registration',
    defaultObjectIds: ['procedure:registration'],
    matchCounters: (c) =>
      c.id.startsWith('5g.registration') || c.id === '5g.ue.registered',
    defaultCounterIds: ['5g.ue.registered'],
    defaultAggregation: 'last',
  },
  {
    id: 'procedure-pdu',
    title: 'Sesiones PDU globales',
    subtitle:
      'Métricas globales derivadas de logs; no atribuibles a una instancia SMF',
    nf: 'procedures',
    nfName: 'Procedimientos globales',
    icon: Activity,
    scenario: '5g-sa',
    matchObjects: (o) => o.id === 'procedure:pdu-session',
    defaultObjectIds: ['procedure:pdu-session'],
    matchCounters: (c) => c.id.startsWith('5g.pdu'),
    defaultCounterIds: ['5g.pdu.active'],
    defaultAggregation: 'last',
  },
  // ================= UPF (UPF-01 y UPF-02) =================
  {
    id: 'upf-throughput',
    title: 'Tráfico por interfaz UPF',
    subtitle:
      'Rendimiento de transmisión (Tx) y recepción (Rx) en UPF-01 y UPF-02',
    nf: 'upf',
    nfName: 'UPF',
    icon: Network,
    scenario: '5g-sa',
    matchObjects: (obj) =>
      [
        'interface:ogstun',
        'interface:ogstun_corp',
        'interface:ogstun2',
      ].includes(obj.id),
    defaultObjectIds: ['interface:ogstun'],
    matchCounters: (c) => c.id.startsWith('interface.'),
    defaultCounterIds: ['interface.rx.kbps', 'interface.tx.kbps'],
    defaultAggregation: 'avg',
    defaultRangeKey: '1h',
  },
  {
    id: 'upf-sessions',
    title: 'Sesiones PDU y Flujos QoS',
    subtitle:
      'Sesiones activas, flujos por DNN y paquetes GTP-U N3 en el Gateway',
    nf: 'upf',
    nfName: 'UPF',
    icon: Activity,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:upf' || obj.id === 'nf:upf2',
    defaultObjectIds: ['nf:upf', 'nf:upf2'],
    matchCounters: (c) =>
      c.id.includes('upf') ||
      c.id.includes('gtp') ||
      c.id === 'core.nf.availability',
    defaultCounterIds: ['upf_sessionnbr', 'upf_qosflows'],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'upf-health',
    title: 'Salud y Recursos del Gateway',
    subtitle: 'Consumo CPU, memoria residente (RAM) y disponibilidad de UPF',
    nf: 'upf',
    nfName: 'UPF',
    icon: Cpu,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:upf' || obj.id === 'nf:upf2',
    defaultObjectIds: ['nf:upf', 'nf:upf2'],
    matchCounters: (c) =>
      c.id === 'core.nf.availability' || c.id.includes('process.upf'),
    defaultCounterIds: ['core.nf.availability', 'nf.process.upf.cpu_percent'],
    defaultAggregation: 'avg',
    defaultRangeKey: '1h',
  },

  // ================= AMF (AMF) =================
  {
    id: 'amf-mobility',
    title: 'Registro y Movilidad 5G',
    subtitle:
      'Tasa de éxito de Registration, intentos, rechazos y latencia NAS',
    nf: 'amf',
    nfName: 'AMF',
    icon: Shield,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:amf',
    defaultObjectIds: ['nf:amf'],
    matchCounters: (c) => c.id.startsWith('rm_reg'),
    defaultCounterIds: [
      '5g.registration.success_rate',
      '5g.registration.attempts',
      '5g.registration.successes',
    ],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'amf-auth',
    title: 'Autenticación 5G-AKA & Seguridad',
    subtitle:
      'Solicitudes y causas de rechazo criptográfico en plano de control',
    nf: 'amf',
    nfName: 'AMF',
    icon: Zap,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:amf',
    defaultObjectIds: ['nf:amf'],
    matchCounters: (c) => c.id.startsWith('amf_auth'),
    defaultCounterIds: ['amf_authreq', 'amf_authreject', 'amf_authfail'],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'amf-signaling',
    title: 'Paging 5G y Contextos Radio',
    subtitle:
      'gNodeB conectados (N2), contextos RAN UE y solicitudes de Paging',
    nf: 'amf',
    nfName: 'AMF',
    icon: Radio,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:amf',
    defaultObjectIds: ['nf:amf'],
    matchCounters: (c) =>
      c.id === 'ran_ue' ||
      c.id === 'gnb' ||
      c.id === 'amf_session' ||
      c.id.startsWith('mm_paging'),
    defaultCounterIds: ['gnb', 'ran_ue', 'amf_session'],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'amf-health',
    title: 'Disponibilidad y Recursos AMF',
    subtitle:
      'Estado operativo del servicio y métricas de CPU/RAM del proceso AMF',
    nf: 'amf',
    nfName: 'AMF',
    icon: Cpu,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:amf',
    defaultObjectIds: ['nf:amf'],
    matchCounters: (c) =>
      c.id === 'core.nf.availability' || c.id.includes('process.amf'),
    defaultCounterIds: ['core.nf.availability', 'nf.process.amf.cpu_percent'],
    defaultAggregation: 'avg',
    defaultRangeKey: '1h',
  },

  // ================= SMF (Sesiones) =================
  {
    id: 'smf-sessions',
    title: 'Sesiones PDU 5G & Accesibilidad',
    subtitle: 'Tasa de éxito de PDU Session, activaciones, rechazos y latencia',
    nf: 'smf',
    nfName: 'SMF',
    icon: Layers,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:smf',
    defaultObjectIds: ['nf:smf'],
    matchCounters: (c) => c.id === 'sm_sessionnbr' || c.id.startsWith('sm_pdu'),
    defaultCounterIds: [
      '5g.pdu.success_rate',
      '5g.pdu.attempts',
      '5g.pdu.successes',
    ],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'smf-qos',
    title: 'Flujos QoS y Tráfico SMF',
    subtitle:
      'Monitoreo de flujos por perfil 5QI y suscriptores activos en SMF',
    nf: 'smf',
    nfName: 'SMF',
    icon: Activity,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:smf',
    defaultObjectIds: ['nf:smf'],
    matchCounters: (c) =>
      c.id === 'sm_qos_flow_nbr' ||
      c.id === 'ues_active' ||
      c.id.startsWith('sm_pdusessioncreation'),
    defaultCounterIds: ['sm_qos_flow_nbr', 'ues_active'],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'smf-n4',
    title: 'Señalización N4 / PFCP (SMF ↔ UPF)',
    subtitle:
      'Mensajes de establecimiento, reportes N4 y sesiones PFCP activas',
    nf: 'smf',
    nfName: 'SMF',
    icon: Zap,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:smf',
    defaultObjectIds: ['nf:smf'],
    matchCounters: (c) => c.id.startsWith('sm_n4') || c.id.startsWith('pfcp_'),
    defaultCounterIds: [
      'sm_n4sessionestabreq',
      'sm_n4sessionestabfail',
      'pfcp_sessions_active',
    ],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'smf-health',
    title: 'Disponibilidad y Recursos SMF',
    subtitle:
      'Estado operativo del servicio y métricas de CPU/RAM del proceso SMF',
    nf: 'smf',
    nfName: 'SMF',
    icon: Cpu,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:smf',
    defaultObjectIds: ['nf:smf'],
    matchCounters: (c) =>
      c.id === 'core.nf.availability' || c.id.includes('process.smf'),
    defaultCounterIds: ['core.nf.availability', 'nf.process.smf.cpu_percent'],
    defaultAggregation: 'avg',
    defaultRangeKey: '1h',
  },

  // ================= SBI (PCF / NRF / SCP / UDM / UDR) =================
  {
    id: 'pcf-policies',
    title: 'Políticas de Red PCF (Npcf)',
    subtitle: 'Asociaciones de políticas AM y SM y sesiones activas en PCF',
    nf: 'pcf',
    nfName: 'SBI',
    icon: Zap,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:pcf',
    defaultObjectIds: ['nf:pcf'],
    matchCounters: (c) =>
      c.id.startsWith('pa_') || c.id === 'core.nf.availability',
    defaultCounterIds: [
      'pa_sessionnbr',
      'pa_policyamassoreq',
      'pa_policysmassoreq',
    ],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'sbi-health',
    title: 'Disponibilidad SBI (NRF / SCP / PCF)',
    subtitle:
      'Supervisión de salud del plano de control Service Based Architecture',
    nf: 'pcf',
    nfName: 'SBI',
    icon: Shield,
    scenario: '5g-sa',
    matchObjects: (obj) =>
      obj.id === 'nf:nrf' ||
      obj.id === 'nf:scp' ||
      obj.id === 'nf:pcf' ||
      obj.id === 'nf:udm' ||
      obj.id === 'nf:udr' ||
      obj.id === 'nf:ausf',
    defaultObjectIds: ['nf:nrf', 'nf:scp', 'nf:pcf'],
    matchCounters: (c) =>
      c.id === 'core.nf.availability' || c.id.startsWith('nf.process.'),
    defaultCounterIds: ['core.nf.availability'],
    defaultAggregation: 'avg',
    defaultRangeKey: '1h',
  },

  // ================= RAN (gNodeB / UERANSIM) =================
  {
    id: 'ran-gnb',
    title: 'Conectividad gNodeB & Radio',
    subtitle: 'Supervisión del enlace NG-RAN N2/N3 y estado de conexión radio',
    nf: 'ran',
    nfName: 'RAN',
    icon: Radio,
    scenario: '5g-sa',
    matchObjects: (obj) => obj.id === 'nf:gnb' || obj.id === 'nf:ue',
    defaultObjectIds: ['nf:gnb'],
    matchCounters: (c) =>
      c.id === 'core.nf.availability' ||
      c.id === 'gnb' ||
      c.id.includes('ueransim'),
    defaultCounterIds: ['core.nf.availability', 'gnb'],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },

  // ================= HOST / INFRAESTRUCTURA =================
  {
    id: 'host-resources',
    title: 'Recursos del Servidor VM',
    subtitle: 'Uso de CPU del testbed, memoria RAM y funciones de red activas',
    nf: 'host',
    nfName: 'HOST',
    icon: Server,
    scenario: 'both',
    matchObjects: (obj) => obj.type === 'testbed',
    defaultObjectIds: ['testbed:local'],
    matchCounters: (c) =>
      c.id.startsWith('host.') ||
      c.id === 'core.nf.active' ||
      c.id.startsWith('alarms.'),
    defaultCounterIds: [
      'host.cpu.percent',
      'host.memory.percent',
      'core.nf.active',
    ],
    defaultAggregation: 'avg',
    defaultRangeKey: '1h',
  },

  // ================= 4G EPC (MME y Gateways) =================
  {
    id: 'mme-attach',
    title: 'Attach y Movilidad 4G',
    subtitle: 'Procedimiento inicial LTE NAS, tasa de éxito y latencia',
    nf: 'mme',
    nfName: 'MME',
    icon: Shield,
    scenario: '4g-epc',
    matchObjects: (obj) => obj.id === 'nf:mme',
    defaultObjectIds: ['nf:mme'],
    matchCounters: (c) =>
      c.id.startsWith('4g.attach') || c.id === '4g.ue.attached',
    defaultCounterIds: [
      '4g.attach.success_rate',
      '4g.attach.attempts',
      '4g.attach.successes',
    ],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'mme-signaling',
    title: 'Señalización eNB & S1-MME',
    subtitle: 'Conexión eNodeB, contextos de usuario y disponibilidad MME',
    nf: 'mme',
    nfName: 'MME',
    icon: Radio,
    scenario: '4g-epc',
    matchObjects: (obj) => obj.id === 'nf:mme',
    defaultObjectIds: ['nf:mme'],
    matchCounters: (c) =>
      c.id === 'enb' || c.id === 'enb_ue' || c.id === 'mme_session',
    defaultCounterIds: ['enb', 'enb_ue', 'mme_session'],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'sgw-bearers',
    title: 'Bearers EPS 4G',
    subtitle: 'Sesiones de portador de datos LTE, intentos y latencia',
    nf: 'sgw',
    nfName: 'GW',
    icon: Layers,
    scenario: '4g-epc',
    matchObjects: (obj) => obj.id === 'nf:smf',
    defaultObjectIds: ['nf:smf'],
    matchCounters: (c) => c.id.startsWith('4g.eps'),
    defaultCounterIds: [
      '4g.eps.success_rate',
      '4g.eps.attempts',
      '4g.eps.successes',
    ],
    defaultAggregation: 'last',
    defaultRangeKey: '1h',
  },
  {
    id: 'sgw-throughput',
    title: 'Tráfico SGi / S5',
    subtitle: 'Throughput transmitido y recibido por la interfaz del PGW',
    nf: 'sgw',
    nfName: 'GW',
    icon: Network,
    scenario: '4g-epc',
    matchObjects: (obj) =>
      obj.id === 'nf:upf' ||
      (obj.type === 'interface' && obj.id === 'interface:ogstun'),
    defaultObjectIds: ['nf:upf'],
    matchCounters: (c) => c.id.startsWith('interface.'),
    defaultCounterIds: ['interface.rx.kbps', 'interface.tx.kbps'],
    defaultAggregation: 'avg',
    defaultRangeKey: '1h',
  },
]

export function getObjectPresentation(
  objectId: string,
  catalogObjects?: KpiObject[]
): { label: string; badge?: string; roleBadge?: string } {
  if (objectId === 'interface:ogstun')
    return { label: 'ogstun', badge: 'Interfaz de usuario' }
  if (objectId === 'nf:upf') {
    return { label: 'UPF-01 (Internet)', badge: 'UDG 1', roleBadge: 'UPF' }
  }
  if (
    objectId === 'nf:upf2' ||
    objectId === 'interface:ogstun_corp' ||
    objectId === 'interface:ogstun2'
  ) {
    return { label: 'UPF-02 (Corporate)', badge: 'UDG 2', roleBadge: 'UPF' }
  }
  if (objectId === 'procedure:registration')
    return { label: 'Procedimiento Registration', badge: 'Global · logs' }
  if (objectId === 'procedure:pdu-session')
    return { label: 'Procedimiento PDU Session', badge: 'Global · logs' }
  if (objectId === 'nf:amf') {
    return { label: 'AMF-01', badge: 'UNC 1', roleBadge: 'AMF' }
  }
  if (objectId === 'nf:smf') {
    return { label: 'SMF-01', badge: 'SMF 1', roleBadge: 'SMF' }
  }
  if (objectId === 'nf:pcf') {
    return { label: 'PCF-01', badge: 'Policy', roleBadge: 'SBI' }
  }
  if (objectId === 'nf:nrf') {
    return { label: 'NRF-01', badge: 'Repository', roleBadge: 'SBI' }
  }
  if (objectId === 'nf:scp') {
    return { label: 'SCP-01', badge: 'Proxy', roleBadge: 'SBI' }
  }
  if (objectId === 'nf:udm') {
    return { label: 'UDM-01', badge: 'Data Mgmt', roleBadge: 'SBI' }
  }
  if (objectId === 'nf:udr') {
    return { label: 'UDR-01', badge: 'Data Repo', roleBadge: 'SBI' }
  }
  if (objectId === 'nf:ausf') {
    return { label: 'AUSF-01', badge: 'Auth Server', roleBadge: 'SBI' }
  }
  if (objectId === 'nf:bsf') {
    return { label: 'BSF-01', badge: 'Binding', roleBadge: 'SBI' }
  }
  if (objectId === 'nf:nssf') {
    return { label: 'NSSF-01', badge: 'Slice Selection', roleBadge: 'SBI' }
  }
  if (objectId === 'nf:chf') {
    return { label: 'CHF-01', badge: 'Charging', roleBadge: 'SBI' }
  }
  if (objectId === 'nf:gnb') {
    return { label: 'gNodeB-01', badge: 'gNodeB', roleBadge: 'RAN' }
  }
  if (objectId === 'nf:ue') {
    return { label: 'UE Simulado', badge: 'UERANSIM', roleBadge: 'RAN' }
  }
  if (objectId === 'testbed:local') {
    return { label: 'Testbed Host', badge: 'Servidor VM', roleBadge: 'HOST' }
  }
  if (objectId === 'nf:mme' || objectId === 'procedure:attach') {
    return { label: 'MME-01', badge: 'Mobility', roleBadge: 'MME' }
  }
  if (objectId === 'procedure:eps-bearer') {
    return { label: 'SGW/PGW-01', badge: 'Gateway', roleBadge: '4G_SGW' }
  }

  const found = catalogObjects?.find((o) => o.id === objectId)
  if (found) {
    return { label: found.label, badge: found.group }
  }
  return { label: objectId.split(':').pop() || objectId }
}
