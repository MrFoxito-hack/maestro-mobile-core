import { expect, it } from 'vitest'
import { windowQuality, type CorrelatedSample } from './ab-observations'

const sample: CorrelatedSample = {
  timestamp: 10000,
  actionId: 'action-7',
  version: 33,
  authorityMode: 'MANUAL',
  phase: 'idle',
  generation: '42',
  networkMode: 'xdp',
  confirmed: true,
  service: 'urllc',
  fresh: true,
}
it('accepts only successive correlated stable observations', () => {
  expect(windowQuality(sample, { ...sample, timestamp: 15000 })).toBe('Estable')
  expect(windowQuality(undefined, sample)).toBe('Primera muestra')
})
it.each([
  'actionId',
  'authorityMode',
  'generation',
  'networkMode',
  'version',
] as const)('excludes missing %s', (key) => {
  expect(windowQuality(sample, { ...sample, [key]: undefined })).toBe(
    'Sin correlación'
  )
})
it.each([
  { generation: '43' },
  { actionId: 'action-8' },
  { version: 34 },
  { authorityMode: 'AUTONOMOUS' },
  { networkMode: 'kernel' },
  { confirmed: false },
  { phase: 'mutating' },
  { timestamp: 30000 },
])('marks transitions and gaps', (changes) => {
  expect(
    windowQuality(sample, { ...sample, timestamp: 15000, ...changes })
  ).toBe('Transición')
})
it('excludes stale and failed readings', () => {
  expect(windowQuality(sample, { ...sample, fresh: false })).toBe(
    'Sin correlación'
  )
  expect(
    windowQuality({ ...sample, fresh: false }, { ...sample, timestamp: 15000 })
  ).toBe('Transición')
})
