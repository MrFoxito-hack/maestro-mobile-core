export type CorrelatedSample = {
  timestamp: number
  actionId?: string
  version?: number
  authorityMode?: string
  phase?: string
  generation?: string
  networkMode?: string
  confirmed: boolean
  service: string
  fresh: boolean
  ulPps?: number
  dlPps?: number
}

// Compare observed endpoints only. Unknown identity, gaps and transitions must
// never enter a stable A/B window. This does not infer unobserved history.
export function windowQuality(
  before: CorrelatedSample | undefined,
  after: CorrelatedSample
) {
  if (
    !after.fresh ||
    !after.actionId ||
    after.version === undefined ||
    !after.authorityMode ||
    !after.networkMode ||
    (after.service === 'urllc' && !after.generation)
  )
    return 'Sin correlación'
  if (!after.confirmed || after.phase !== 'idle') return 'Transición'
  if (!before) return 'Primera muestra'
  if (
    after.timestamp <= before.timestamp ||
    after.timestamp - before.timestamp > 15000 ||
    !before.fresh ||
    !before.confirmed ||
    before.phase !== 'idle' ||
    [
      'service',
      'actionId',
      'version',
      'authorityMode',
      'generation',
      'networkMode',
    ].some(
      (key) =>
        before[key as keyof CorrelatedSample] !==
        after[key as keyof CorrelatedSample]
    )
  )
    return 'Transición'
  return 'Estable'
}
