import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ChevronRight, Copy, LoaderCircle } from 'lucide-react'
import { toast } from 'sonner'
import { api, apiErrorMessage } from '@/lib/api'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { TraceDecodeNode, TraceEvent, TraceFrameDecode } from '../types'

export function MessageCodeStreamsModal({
  taskId,
  events,
  index,
  onIndexChange,
  onClose,
}: {
  taskId: string
  events: TraceEvent[]
  index: number | null
  onIndexChange: (index: number) => void
  onClose: () => void
}) {
  const event = index === null ? undefined : events[index]
  const frame = event?.packet_number ?? event?.frame_number
  const decode = useQuery({
    queryKey: ['trace-frame-decode', taskId, frame],
    queryFn: async () =>
      (await api.get<TraceFrameDecode>(`/traces/${taskId}/frames/${frame}/decode`)).data,
    enabled: Boolean(frame && index !== null),
    retry: false,
  })
  const transcript = useMemo(
    () => (decode.data ? treeTranscript(decode.data.tree) : ''),
    [decode.data]
  )

  return (
    <Dialog open={index !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className='flex max-h-[84vh] w-[min(1380px,calc(100vw-3rem))] max-w-none flex-col gap-0 overflow-hidden p-0'>
        <DialogHeader className='border-b px-6 py-4'>
          <DialogTitle>Message Code Streams — {event?.message ?? 'Mensaje 3GPP'}</DialogTitle>
          <DialogDescription>
            Frame {frame ?? '—'} · {event?.protocol ?? '—'} · {event?.interface_3gpp ?? '—'}
          </DialogDescription>
        </DialogHeader>
        <div className='min-h-0 flex-1 overflow-auto bg-muted/15 px-5 py-4'>
          {decode.isLoading ? (
            <div className='flex h-64 items-center justify-center gap-2 text-sm text-muted-foreground'>
              <LoaderCircle className='size-4 animate-spin' /> Decodificando trama…
            </div>
          ) : decode.error ? (
            <div className='rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive'>
              {apiErrorMessage(decode.error, 'No se pudo decodificar la trama')}
            </div>
          ) : (
            <div className='grid items-start gap-3 font-mono text-xs lg:grid-cols-2'>
              {decode.data?.tree.map((node, nodeIndex) => (
                <div key={`${node.name}:${nodeIndex}`} className='min-w-0 rounded-lg border bg-background p-2'>
                  <TreeNode node={node} depth={0} defaultOpen />
                </div>
              ))}
              {decode.data?.truncated && (
                <div className='px-3 py-2 text-amber-600'>Árbol limitado por seguridad de visualización.</div>
              )}
            </div>
          )}
        </div>
        <DialogFooter className='border-t px-5 py-3 sm:justify-between'>
          <div className='flex gap-2'>
            <Button variant='outline' disabled={index === null || index <= 0} onClick={() => index !== null && onIndexChange(index - 1)}>
              Previous
            </Button>
            <Button variant='outline' disabled={index === null || index >= events.length - 1} onClick={() => index !== null && onIndexChange(index + 1)}>
              Next
            </Button>
          </div>
          <div className='flex gap-2'>
            <Button
              variant='outline'
              disabled={!transcript}
              onClick={async () => {
                await navigator.clipboard.writeText(transcript)
                toast.success('Transcripción copiada')
              }}
            >
              <Copy /> Copy
            </Button>
            <Button onClick={onClose}>Close</Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function TreeNode({ node, depth, defaultOpen = false }: { node: TraceDecodeNode; depth: number; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen || depth < 2)
  const branch = Boolean(node.children?.length)
  return (
    <div>
      <button
        type='button'
        className='flex min-h-7 w-full items-start gap-1 rounded px-2 py-1 text-left hover:bg-muted'
        style={{ paddingLeft: `${8 + depth * 18}px` }}
        onClick={() => branch && setOpen((value) => !value)}
      >
        <ChevronRight className={`mt-0.5 size-3.5 shrink-0 transition-transform ${branch && open ? 'rotate-90' : ''} ${branch ? '' : 'opacity-0'}`} />
        <span className={branch ? 'font-semibold text-foreground' : 'text-muted-foreground'}>{node.label}</span>
      </button>
      {branch && open && node.children?.map((child, index) => (
        <TreeNode key={`${child.name}:${index}`} node={child} depth={depth + 1} />
      ))}
    </div>
  )
}

function treeTranscript(nodes: TraceDecodeNode[], depth = 0): string {
  return nodes.map((node) => {
    const line = `${'  '.repeat(depth)}${node.label}`
    return node.children?.length ? `${line}\n${treeTranscript(node.children, depth + 1)}` : line
  }).join('\n')
}
