import { describe, expect, it } from 'vitest'
import { measuredMode, type VehicleObservation } from './use-urllc-xdp'

const observed: VehicleObservation = {
  connected: true,
  session: { address: '10.47.0.3', interface: 'uesimtun7' },
  xdp: {
    available: false,
    effective_mode: 'kernel',
    slice: 'urllc',
    namespace: 'maestro-urllc',
    interfaces: ['murllc-n3', 'murllc-mec'],
    driver_mode: null,
  },
}
const measurement = { source_ip: '10.47.0.3', target: '172.31.48.2' }
describe('MEC measurement attribution', () => {
  it('allows an observed kernel baseline before native XDP installation', () => {
    expect(measuredMode(observed, observed, measurement)).toBe('kernel')
  })
  it('rejects changed session, mode, target and missing observations', () => {
    expect(
      measuredMode(
        observed,
        {
          ...observed,
          session: { address: '10.47.0.4', interface: 'uesimtun8' },
        },
        measurement
      )
    ).toBeUndefined()
    expect(
      measuredMode(
        observed,
        {
          ...observed,
          xdp: { ...observed.xdp!, effective_mode: 'xdp', confirmed: true },
        },
        measurement
      )
    ).toBeUndefined()
    expect(
      measuredMode(observed, observed, { ...measurement, target: '10.47.0.1' })
    ).toBeUndefined()
    expect(measuredMode(observed, undefined, measurement)).toBeUndefined()
  })
  it('requires confirmed XDP and the same PFCP generation', () => {
    const xdp: VehicleObservation = {
      ...observed,
      xdp: {
        ...observed.xdp!,
        effective_mode: 'xdp',
        confirmed: true,
        ue: measurement.source_ip,
        session_generation: '123',
      },
    }
    expect(measuredMode(xdp, xdp, measurement)).toBe('xdp')
    expect(
      measuredMode(
        xdp,
        { ...xdp, xdp: { ...xdp.xdp!, confirmed: false } },
        measurement
      )
    ).toBeUndefined()
    expect(
      measuredMode(
        xdp,
        { ...xdp, xdp: { ...xdp.xdp!, session_generation: '124' } },
        measurement
      )
    ).toBeUndefined()
  })
})
