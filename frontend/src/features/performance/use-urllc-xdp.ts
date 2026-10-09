import {
  useIsMutating,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { useAuthStore } from '@/stores/auth-store'
import { api, canOperate } from '@/lib/api'

export type NetworkMode = 'kernel' | 'xdp'
export type UrllcStatus = {
  available: boolean
  effective_mode: NetworkMode | 'unknown'
  slice: 'urllc'
  namespace: string
  interfaces: string[]
  driver_mode: string | null
  confirmed?: boolean
  ue?: string
  session_generation?: string
  lease_seconds?: number
  reason?: string | null
  counters?: Record<string, { packets: number; bytes: number }>
}
export const urllcKey = ['upf-xdp', 'urllc'] as const
export const vehicleProbeKey = ['vehicle-mec-probe'] as const
export type VehicleObservation = {
  connected: boolean
  session: { address: string; interface: string } | null
  xdp?: UrllcStatus
}
export function measuredMode(
  before: VehicleObservation | undefined,
  after: VehicleObservation | undefined,
  measurement: { source_ip: string; target: string }
): NetworkMode | undefined {
  const mode = before?.xdp?.effective_mode
  if (
    !before?.connected ||
    !after?.connected ||
    !before.session ||
    !after.session ||
    !mode ||
    mode === 'unknown' ||
    mode !== after.xdp?.effective_mode ||
    isTransition(before.xdp) ||
    isTransition(after.xdp) ||
    before.session.address !== measurement.source_ip ||
    after.session.address !== measurement.source_ip ||
    before.session.interface !== after.session.interface ||
    measurement.target !== '172.31.48.2' ||
    before.xdp?.session_generation !== after.xdp?.session_generation
  )
    return undefined
  // Kernel can be measured before the native publisher is installed. XDP
  // always requires the authorized generation and the correlated UE address.
  if (
    mode === 'xdp' &&
    (!before.xdp?.session_generation ||
      before.xdp.ue !== measurement.source_ip ||
      after.xdp?.ue !== measurement.source_ip)
  )
    return undefined
  return mode
}
export const readUrllcStatus = async (signal?: AbortSignal) =>
  (
    await api.get<UrllcStatus>('/upf-xdp/status', {
      params: { slice: 'urllc' },
      signal,
    })
  ).data
export function isTransition(data?: UrllcStatus) {
  return data?.effective_mode === 'xdp' && data.confirmed !== true
}
export function useUrllcXdp() {
  const client = useQueryClient()
  const allowed = canOperate(useAuthStore((s) => s.auth.user?.role))
  const pending = useIsMutating({ mutationKey: urllcKey }) > 0
  const measuring = useIsMutating({ mutationKey: vehicleProbeKey }) > 0
  const state = useQuery({
    queryKey: urllcKey,
    queryFn: ({ signal }) => readUrllcStatus(signal),
    refetchInterval: 2000,
    retry: false,
    enabled: allowed,
  })
  const change = useMutation({
    mutationKey: urllcKey,
    mutationFn: async (mode: NetworkMode) =>
      (await api.post<UrllcStatus>('/upf-xdp/mode', { mode, slice: 'urllc' }))
        .data,
    onMutate: () => client.cancelQueries({ queryKey: urllcKey }),
    onSuccess: async (data) => {
      await client.cancelQueries({ queryKey: urllcKey })
      client.setQueryData(urllcKey, data)
    },
    // Reconcile even after rollback or HTTP timeout; never assume success.
    onSettled: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: urllcKey }),
        client.invalidateQueries({ queryKey: ['terminal-device'] }),
        client.invalidateQueries({ queryKey: ['performance-result'] }),
      ])
    },
  })
  const transitioning = pending || (!state.isError && isTransition(state.data))
  return { state, change, allowed, transitioning, measuring }
}
