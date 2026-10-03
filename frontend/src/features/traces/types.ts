export type TraceKind = 'interface' | 'subscriber'

export type TraceTaskStatus =
  | 'queued'
  | 'preparing'
  | 'starting'
  | 'capturing'
  | 'running'
  | 'stopping'
  | 'processing'
  | 'completed'
  | 'partial'
  | 'failed'
  | 'stopped'
  | 'cancelled'
  | 'interrupted'

export type TraceOutcome =
  | 'success'
  | 'procedure_failure'
  | 'partial'
  | 'inconclusive'
  | 'no_traffic'
  | 'unknown'

export type TraceEvidenceType = 'pcap' | 'log' | 'correlated' | 'runtime'

export type TraceCaptureAgent = {
  id: string
  label?: string
  hostname?: string
  status?: string
  source?: string
  node_ids?: string[]
}

export type TraceNetworkFunction = {
  id: string
  component_id?: string
  label?: string
  node_id: string
  kind?: string
  interfaces?: string[]
}

export type TraceCaptureTarget = {
  id: string
  label: string
  capture_agent_id?: string
  node_id?: string
  component_id?: string
  component_ids?: string[]
  nf_ids?: string[]
  interface_3gpp?: string
  interface?: string
  device?: string
  device_label?: string
  protocol?: string
  protocols?: string[]
  procedures?: string[]
}

export type TraceOption = {
  id: string
  label: string
  description?: string
}

export type TraceSubscriberCapabilities = {
  enabled?: boolean
  identifier_types?: Array<TraceOption | string>
  procedures?: Array<TraceOption | string>
  default_procedures?: string[]
  supports_sbi?: boolean
  supports_user_plane?: boolean
  supports_auto_trigger?: boolean
  participating_components?: string[]
  component_ids?: string[]
  interfaces?: string[]
}

export type TraceCapabilities = {
  scenario_id: string
  technology?: string
  testbed_id?: string
  capture_agents: TraceCaptureAgent[]
  network_functions: TraceNetworkFunction[]
  capture_targets: TraceCaptureTarget[]
  subscriber?: TraceSubscriberCapabilities
  limits?: {
    min_duration_seconds?: number
    max_duration_seconds?: number
    max_megabytes?: number
    max_concurrent_tasks?: number
  }
  quota?: {
    max_active?: number
    max_duration_seconds?: number
    max_megabytes?: number
    auto_trigger_allowed?: boolean
  }
}

export type TraceProtocolCount = {
  protocol: string
  packets: number
}

export type TraceArtifact = {
  id?: string
  kind?: 'original' | 'filtered' | 'evidence' | string
  label?: string
  file?: string
  size_bytes?: number
  sha256?: string
  available?: boolean
}

export type TraceTask = {
  id: string
  name?: string
  trace_type?: TraceKind
  type?: TraceKind
  scenario_id?: string
  testbed_id?: string
  capture_agent_id?: string
  capture_agent_label?: string
  node_id?: string
  component_id?: string
  component_label?: string
  capture_point?: string
  capture_point_label?: string
  interface?: string
  interface_3gpp?: string
  protocol?: string
  protocols?: TraceProtocolCount[]
  procedures?: string[]
  identifier_type?: string
  identifier?: string
  identifier_masked?: string
  selector_kind?: string
  selector_masked?: string
  target?: {
    kind?: string
    masked?: string
    matched?: boolean
  }
  include_user_plane?: boolean
  include_sbi?: boolean
  auto_trigger?: boolean
  status: TraceTaskStatus | string
  result?: TraceOutcome | string
  outcome?: TraceOutcome | string
  phase?: string
  progress_percent?: number
  source?: string
  owner?: string
  created_at: string
  started_at?: string
  completed_at?: string
  duration_seconds?: number
  max_megabytes?: number
  size_bytes?: number
  packet_count?: number
  event_count?: number
  correlated_event_count?: number
  file?: string
  artifacts?: TraceArtifact[]
  error?: string
}

export type TraceIdentifier = {
  id?: string
  kind?: string
  type?: string
  label?: string
  value?: string
  masked_value?: string
  source?: string
  evidence_type?: TraceEvidenceType | string
  confidence?: 'direct' | 'transaction' | 'inferred' | string
  frame_number?: number
}

export type TraceParticipant = {
  id: string
  label?: string
  kind?: string
  node_id?: string
}

export type TraceEventEndpoint = {
  id?: string
  nf?: string
  label?: string
  node_id?: string
  address?: string
  port?: number
}

export type TraceEvent = {
  charging?: {
    operation: string
    http_method?: string | null
    http_status?: string | null
    charging_data_ref?: string | null
    payload_decoded: boolean
    payload_frames: string[]
    units: Array<{ kind: string; ratingGroup?: number; totalVolume?: number; uplinkVolume?: number; downlinkVolume?: number; time?: number; localSequenceNumber?: number }>
  }
  interpretation_policy?: string
  source_ip?: string
  target_ip?: string
  standard_references?: Array<{
    spec: string
    version: string
    clause: string
    source: string
  }>
  id: string
  ordinal?: number
  timestamp: string
  relative_ms?: number
  source_nf?: string
  target_nf?: string
  source?: TraceEventEndpoint | string
  target?: TraceEventEndpoint | string
  interface_3gpp?: string
  interface?: string
  protocol?: string
  plane?: 'control' | 'user' | string
  message: string
  procedure?: string
  status?: 'success' | 'failure' | 'warning' | 'pending' | string
  packet_number?: number
  length_bytes?: number | null
  frame_number?: number
  evidence_type?: TraceEvidenceType | string
  evidence?: string
  identifiers?: TraceIdentifier[]
  cause_code?: string | number
  cause?: string
}

export type TraceDecodeNode = {
  name: string
  label: string
  value?: string
  children?: TraceDecodeNode[]
}

export type TraceFrameDecode = {
  frame_number: number
  protocol: string
  message_type: string
  timestamp?: string | null
  tree: TraceDecodeNode[]
  truncated?: boolean
}

export type NodeTraceCatalogItem = {
  id: string
  label: string
  description: string
  interfaces: Array<{ id: string; label: string; protocols: string[] }>
}

export type TraceProcedure = {
  id?: string
  type?: string
  name?: string
  label?: string
  status?: 'success' | 'failure' | 'partial' | 'inconclusive' | string
  started_at?: string
  completed_at?: string
  duration_ms?: number
  event_count?: number
  evidence?: string[]
  log_confirmation?: boolean
  last_successful_step?: string
  affected_nf?: string
  interface?: string
  failure_cause?: {
    code?: string | number
    domain?: string
    text?: string
  }
}

export type TraceDiagnostic = {
  id?: string
  severity?: 'critical' | 'major' | 'minor' | 'warning' | 'info' | string
  title: string
  description?: string
  detail?: string
  evidence?: string
  recommendation?: string
  affected_nf?: string
  interface?: string
}

export type TraceAnalysis = {
  analysis_policy?: string
  task_id: string
  generated_at?: string
  analysis_version?: string
  outcome?: TraceOutcome | string
  correlation_status?: 'complete' | 'partial' | 'insufficient' | string
  summary?: string
  target?: {
    kind?: string
    masked?: string
    matched?: boolean
  }
  identifiers?: TraceIdentifier[]
  relations?: TraceIdentifierRelation[]
  procedures?: TraceProcedure[]
  participants?: Array<TraceParticipant | string>
  events?: TraceEvent[]
  troubleshooting_active?: boolean
  troubleshooting_events?: TraceEvent[]
  troubleshooting_participants?: Array<TraceParticipant | string>
  diagnostics?: TraceDiagnostic[]
  artifacts?: TraceArtifact[]
}

export type TraceIdentifierRelation = {
  from: Pick<TraceIdentifier, 'kind' | 'value'>
  to: Pick<TraceIdentifier, 'kind' | 'value'>
  confidence?: 'direct' | 'transaction' | 'inferred' | string
  evidence?: string[]
}

export type InterfaceTraceDraft = {
  name: string
  scenario_id: string
  testbed_id: string
  capture_agent_id: string
  node_id: string
  component_id: string
  capture_point: string
  duration_seconds: number
  max_megabytes: number
  identifier_type?: 'imsi' | 'supi'
  identifier?: string
}

export type SubscriberTraceDraft = {
  name: string
  scenario_id: '5g-sa'
  testbed_id: string
  capture_agent_id: string
  identifier_type: 'imsi' | 'supi' | 'ue-ip'
  identifier: string
  procedures: string[]
  include_user_plane: boolean
  include_sbi: boolean
  auto_trigger: boolean
  duration_seconds: number
  max_megabytes: number
}

export const ACTIVE_TRACE_STATUSES = new Set([
  'queued',
  'preparing',
  'starting',
  'capturing',
  'running',
  'stopping',
  'processing',
])

export function traceKind(task: TraceTask): TraceKind {
  return (
    task.trace_type ??
    task.type ??
    (task.identifier_type ? 'subscriber' : 'interface')
  )
}

export function isActiveTrace(task: TraceTask) {
  return ACTIVE_TRACE_STATUSES.has(task.status)
}

export function eventEndpointId(
  endpoint: TraceEventEndpoint | string | undefined,
  fallback: string | undefined
) {
  if (typeof endpoint === 'string') return endpoint
  return endpoint?.nf ?? endpoint?.id ?? fallback ?? 'unknown'
}

export function optionFrom(value: TraceOption | string): TraceOption {
  if (typeof value === 'string') {
    return {
      id: value,
      label: value
        .replace(/[_-]/g, ' ')
        .replace(/\b\w/g, (letter: string) => letter.toUpperCase()),
    }
  }
  return value
}
