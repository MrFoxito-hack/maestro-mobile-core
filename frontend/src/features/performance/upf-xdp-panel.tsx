import type { ReactNode } from 'react'
import { RefreshCw, Zap } from 'lucide-react'
import { apiErrorMessage } from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useUrllcXdp } from './use-urllc-xdp'

export function UpfXdpPanel({
  title,
  subtitle,
  className,
}: {
  title?: ReactNode
  subtitle?: ReactNode
  className?: string
} = {}) {
  const { state, change, allowed, transitioning, measuring } = useUrllcXdp()
  if (!allowed) return null
  const data = state.isError ? undefined : state.data
  const mode = data?.effective_mode ?? 'unknown'

  return (
    <div
      className={
        className ??
        'flex flex-wrap items-center justify-between gap-2.5 rounded-lg border bg-card/60 p-3 text-xs shadow-2xs'
      }
    >
      <div className='flex items-center gap-2 flex-wrap'>
        {title ?? (
          <>
            <Zap className='size-3.5 text-amber-500' />
            <strong>Acelerador URLLC</strong>
          </>
        )}
        {subtitle}
        <Badge
          variant={mode === 'xdp' && !transitioning ? 'default' : 'secondary'}
          role='status'
          className='text-[10px] uppercase font-mono'
        >
          {transitioning
            ? 'Conmutando…'
            : mode === 'unknown'
              ? 'Estado no verificado'
              : `Modo efectivo: ${mode}`}
        </Badge>
      </div>
      <div
        className='flex items-center gap-1.5'
        role='group'
        aria-label='Modo de red URLLC'
      >
        <Button
          size='sm'
          className='h-7 px-2.5 text-xs'
          variant={mode === 'kernel' ? 'secondary' : 'ghost'}
          aria-pressed={mode === 'kernel' && !transitioning}
          disabled={transitioning || measuring}
          onClick={() => change.mutate('kernel')}
        >
          A · Kernel
        </Button>
        <Button
          size='sm'
          className='h-7 px-2.5 text-xs'
          variant={mode === 'xdp' ? 'default' : 'ghost'}
          aria-pressed={mode === 'xdp' && !transitioning}
          disabled={
            !data?.available || transitioning || measuring || state.isPending
          }
          onClick={() => change.mutate('xdp')}
        >
          B · XDP
        </Button>
        <Button
          size='icon'
          className='size-7'
          variant='ghost'
          aria-label='Actualizar estado URLLC'
          onClick={() => void state.refetch()}
        >
          <RefreshCw
            className={`size-3.5 ${state.isFetching ? 'animate-spin' : ''}`}
          />
        </Button>
      </div>
      {(state.error || change.error) && (
        <p role='alert' className='w-full text-destructive text-[11px]'>
          {apiErrorMessage(
            state.error || change.error,
            'No se pudo verificar el agente URLLC'
          )}
        </p>
      )}
    </div>
  )
}
