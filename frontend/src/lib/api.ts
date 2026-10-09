import axios from 'axios'
import { useAuthStore } from '@/stores/auth-store'

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL ?? '/api/v1',
  timeout: 45_000,
})

api.interceptors.request.use((config) => {
  const token = useAuthStore.getState().auth.accessToken
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

export type Role = 'admin' | 'teacher' | 'student'
export type User = { username: string; role: Role; testbed: string | null }
export type ScenarioState =
  | 'stopped'
  | 'starting'
  | 'running'
  | 'degraded'
  | 'stopping'
  | 'failed'
export type ComponentStatus = {
  id: string
  label: string
  kind: string
  status: string
  node_id: string
  unit: string
  interfaces: string[]
  expected_endpoints: {
    protocol: string
    address: string
    port: number
    interface: string
  }[]
  config_paths: string[]
  procedures: string[]
  depends_on: string[]
}
export type ScenarioStatus = {
  scenario_id: string
  state: ScenarioState
  components: ComponentStatus[]
  updated_at: string
  message?: string
}
export type Metrics = {
  timestamp: string
  source?: string
  cpu_percent: number
  memory_percent: number
  interfaces: Record<
    string,
    {
      rx_bytes?: number
      tx_bytes?: number
      rx_mbps: number
      tx_mbps: number
      rx_kbps?: number
      tx_kbps?: number
    }
  >
  telco?: {
    active_nfs: number
    total_nfs: number
    ue_registered: number
    pdu_sessions: number
    slices_active?: number
    slices_detail?: string[]
  }
  history?: {
    time: string
    ogstun_kbps: number
    lo_kbps: number
    cpu: number
    mem: number
  }[]
  rrc?: { available: boolean; reason?: string }
}
export type HostSnapshot = {
  id: string
  hostname: string
  ip?: string
  role?: string
  port?: number
  interfaces: {
    name: string
    state: string
    mtu: number | null
    addresses: {
      family: string
      address: string
      prefix_length: number
      scope: string
    }[]
  }[]
  listening_ports: { protocol: string; address: string; port: number }[]
}

export type RuntimeSnapshot = {
  source: 'mock' | 'local' | 'remote'
  hostname: string
  hosts?: HostSnapshot[]
  interfaces: {
    name: string
    state: string
    mtu: number | null
    addresses: {
      family: string
      address: string
      prefix_length: number
      scope: string
    }[]
  }[]
  listening_ports: { protocol: string; address: string; port: number }[]
}
export type Alarm = {
  id: string
  component: string
  network_function: string
  node_id: string
  severity:
    | 'critical'
    | 'major'
    | 'minor'
    | 'warning'
    | 'indeterminate'
    | 'cleared'
  message: string
  state: 'active' | 'acknowledged' | 'cleared'
  probable_cause: string
  interfaces: string[]
  procedures: string[]
  evidence: string
  recommendation: string
  observed_at: string
}

export const canOperate = (role?: Role) =>
  role === 'admin' || role === 'teacher'

// Trace tasks are part of the student workflow. The backend still enforces
// testbed assignment, ownership, quotas and role-specific operations.
export const canTrace = (role?: Role): role is Role =>
  role === 'admin' || role === 'teacher' || role === 'student'

export function apiErrorMessage(error: unknown, fallback: string) {
  if (!axios.isAxiosError(error)) {
    return error instanceof Error && error.message ? error.message : fallback
  }
  const detail = error.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) =>
        typeof item?.msg === 'string'
          ? item.msg
          : typeof item === 'string'
            ? item
            : null
      )
      .filter(Boolean)
    if (messages.length) return messages.join(' · ')
  }
  return error.message && error.code !== 'ERR_BAD_RESPONSE'
    ? error.message
    : fallback
}

export type ConfigFile = {
  component_id: string
  component: string
  path: string
}

export type DeclaredConfig = {
  scenario_id: string
  component_id: string
  path: string
  content: string
  redacted_fields: number
  sha256: string
  read_only: boolean
}

export type ConfigCheck = {
  id: string
  category: string
  status: 'pass' | 'warning' | 'error'
  title: string
  evidence: string
  components: string[]
}

export type ConfigValidationResult = {
  scenario_id: string
  generated_at: string
  summary: {
    pass: number
    warning: number
    error: number
  }
  checks: ConfigCheck[]
  baseline_diff: string
}

export type Experiment = {
  id: string
  scenario_id: string
  title: string
  category: string
  severity: 'critical' | 'major' | 'minor' | 'warning'
  target_nf: string
  interfaces: string[]
  procedures: string[]
  description: string
  expected_detection: string
  recovery_action: string
  state: {
    status: 'nominal' | 'injected'
    injected_at?: string
    recovered_at?: string
    elapsed_seconds?: number
    message?: string
  }
}
