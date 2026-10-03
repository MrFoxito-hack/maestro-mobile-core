export type KpiObject = {
  id: string
  label: string
  type: 'testbed' | 'nf' | 'interface' | 'procedure'
  group: string
  status?: string
  counter_count?: number
  capabilities?: {
    metrics_status: string
    process_status?: string
    checked_at?: string
  }
}

export type KpiCounter = {
  id: string
  label: string
  category: string
  unit: string
  kind: 'gauge' | 'counter'
  source: string
  objects: KpiObject['type'][]
  object_ids?: string[]
  description?: string
  native_name?: string
  dimensions?: Record<string, string>
  last_seen?: string
  min_granularity_seconds?: number
}

export type PerformanceCatalog = {
  scenario_id: string
  testbed_id: string
  objects: KpiObject[]
  counters: KpiCounter[]
  collector: {
    interval_seconds: number
    retention_days: number
    stored_samples: number
    last_run?: { status: string; completed_at?: string; sample_count: number }
  }
}

export type KpiFolder = {
  id: string
  name: string
  owner: string
  testbed_id: string
  scope: 'personal' | 'testbed'
  created_at: string
}

export type SavedKpiQuery = {
  id: string
  name: string
  folder_id?: string
  owner: string
  scope: 'personal' | 'testbed'
  scenario_id: string
  object_ids: string[]
  counter_ids: string[]
  range_key: RangeKey
  granularity_seconds: number
  aggregation: Aggregation
}

export type RangeKey = '15m' | '1h' | '6h' | '24h' | '7d'
export type Aggregation = 'avg' | 'min' | 'max' | 'sum' | 'last'

export type KpiQueryDraft = {
  scenario_id: '5g-sa' | '4g-epc'
  object_ids: string[]
  counter_ids: string[]
  range_key: RangeKey
  granularity_seconds: number
  aggregation: Aggregation
}

export type KpiSeries = {
  id: string
  object_id: string
  counter_id: string
  label: string
  unit: string
  source: string
  quality: string
  kind?: 'gauge' | 'counter'
  aggregation?: Aggregation
  points: { timestamp: string; epoch: number; value: number }[]
}

export type KpiQueryResult = {
  testbed_id: string
  scenario_id: string
  start: string
  end: string
  sample_count: number
  granularity_seconds: number
  aggregation: Aggregation
  series: KpiSeries[]
  missing_series?: { object_id: string; counter_id: string }[]
  notes?: string[]
}

export function supportsObject(counter: KpiCounter, objectId: string) {
  return counter.object_ids?.length
    ? counter.object_ids.includes(objectId)
    : counter.objects.includes(objectId.split(':')[0] as KpiObject['type'])
}

// NF and procedure catalog IDs identify distinct measurement domains.
// Interfaces may be compared together, but never mixed with host or NF data.
export function measurementDomain(objectId: string) {
  const type = objectId.split(':')[0]
  return type === 'nf' || type === 'procedure' ? objectId : type
}

export function selectMeasurementObject(current: string[], id: string) {
  if (current.includes(id)) return current.filter((item) => item !== id)
  return current.every(
    (item) => measurementDomain(item) === measurementDomain(id)
  )
    ? [...current, id]
    : [id]
}

export function isOperationalCounter(counter: KpiCounter) {
  return (
    counter.id === 'core.nf.availability' ||
    counter.id.startsWith('nf.process.') ||
    counter.source === 'systemd /proc' ||
    /^(process_|go_|python_|promhttp_)/.test(counter.native_name ?? '')
  )
}

export function recommendedCounters(counters: KpiCounter[], objectId: string) {
  const compatible = counters.filter((counter) =>
    supportsObject(counter, objectId)
  )
  const native = compatible.filter(
    (c) =>
      c.source === 'Open5GS /metrics' &&
      c.kind === 'gauge' &&
      /session|ues_active|registeredsubnbr|^gnb$|^enb$/.test(
        c.native_name ?? ''
      )
  )
  if (native.length) return native.slice(0, 3).map((c) => c.id)
  const memory = compatible.find((c) => c.id.endsWith('.rss_mib'))
  if (memory) return [memory.id]
  return compatible.slice(0, 2).map((c) => c.id)
}
