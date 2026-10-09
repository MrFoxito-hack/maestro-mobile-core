import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Check,
  Copy,
  Edit,
  Eye,
  Plus,
  RefreshCw,
  Trash2,
  UserPlus,
  Wifi,
} from 'lucide-react'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import { api, apiErrorMessage, canOperate } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { EmsPage } from '@/features/ems-page'

type PduSession = {
  session_id?: string
  state?: string
  type?: string
  apn?: string
  address?: string
  ambr?: string
}

type SubscriberLiveStatus = {
  registered: boolean | null
  cm_state: string | null
  rm_state: string | null
  mm_state: string | null
  terminal_available?: boolean | null
  observation_status?: string
  observed_at?: string
  cell_id?: string | null
  tac?: string | null
  guti?: string | null
  pdu_sessions?: PduSession[]
  active_ip?: string | null
}

type Subscriber = {
  imsi: string
  terminal_label?: string
  security: { k: string; opc: string; amf?: string }
  slice: {
    sst: number
    sd?: string
    session?: { name: string; type?: number }[]
  }[]
  live_status?: SubscriberLiveStatus
}

const emptySubscribers: Subscriber[] = []

function resolveSubscriberSlice(sub: Subscriber) {
  const activeApn = sub.live_status?.pdu_sessions?.[0]?.apn
  const slice =
    (activeApn &&
      sub.slice?.find((s) => s.session?.some((sess) => sess.name === activeApn))) ||
    sub.slice?.[0]
  const dnn = activeApn || slice?.session?.[0]?.name || 'internet'
  const sst = slice?.sst ?? 1
  const sd = slice?.sd
  return { slice, dnn, sst, sd }
}

const defaultCreate = {
  imsi: '999700000000002',
  key: '465B5CE8B199B49FAA5F0A2EE238A6BC',
  opc: 'E8ED289DEBA952E4283B54E88E6183CA',
  amf: '8000',
  apn_dnn: 'internet',
  sst: 1,
  sd: '000001',
}

export function SubscribersPage() {
  const queryClient = useQueryClient()
  const role = useAuthStore((s) => s.auth.user?.role)
  const isOperator = canOperate(role)

  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<
    'all' | 'terminals' | 'registered' | 'offline' | 'unknown'
  >('all')

  // Modals state
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm, setCreateForm] = useState(defaultCreate)

  const [detailImsi, setDetailImsi] = useState<string | null>(null)

  const [editSub, setEditSub] = useState<Subscriber | null>(null)
  const [editForm, setEditForm] = useState({
    key: '',
    opc: '',
    amf: '8000',
    apn_dnn: 'internet',
    sst: 1,
    sd: '000001',
  })

  const [deleteTarget, setDeleteTarget] = useState<string | null>(null)
  const [copiedImsi, setCopiedImsi] = useState<string | null>(null)

  const query = useQuery({
    queryKey: ['subscribers'],
    queryFn: async () => (await api.get<Subscriber[]>('/subscribers')).data,
    refetchInterval: 5000,
  })

  const createMutation = useMutation({
    mutationFn: (payload: typeof defaultCreate) =>
      api.post('/subscribers', payload),
    onSuccess: () => {
      toast.success('Suscriptor aprovisionado exitosamente en Open5GS')
      setCreateOpen(false)
      setCreateForm(defaultCreate)
      void queryClient.invalidateQueries({ queryKey: ['subscribers'] })
    },
    onError: (error) =>
      toast.error('No se pudo aprovisionar el suscriptor', {
        description: apiErrorMessage(
          error,
          'El IMSI ya existe o formato incorrecto.'
        ),
      }),
  })

  const updateMutation = useMutation({
    mutationFn: ({
      imsi,
      payload,
    }: {
      imsi: string
      payload: Record<string, unknown>
    }) => api.put(`/subscribers/${imsi}`, payload),
    onSuccess: () => {
      toast.success('Perfil de suscriptor actualizado')
      setEditSub(null)
      void queryClient.invalidateQueries({ queryKey: ['subscribers'] })
    },
    onError: (error) =>
      toast.error('No se pudo actualizar el suscriptor', {
        description: apiErrorMessage(
          error,
          'Verifique los parámetros ingresados.'
        ),
      }),
  })

  const deleteMutation = useMutation({
    mutationFn: (imsi: string) => api.delete(`/subscribers/${imsi}`),
    onSuccess: () => {
      toast.success('Suscriptor eliminado de la base de datos')
      setDeleteTarget(null)
      void queryClient.invalidateQueries({ queryKey: ['subscribers'] })
    },
    onError: (error) =>
      toast.error('No se pudo eliminar el suscriptor', {
        description: apiErrorMessage(
          error,
          'Error al contactar con la base de datos.'
        ),
      }),
  })

  const subscribers = query.data ?? emptySubscribers
  const detailSub = subscribers.find((sub) => sub.imsi === detailImsi) ?? null
  const setDetailSub = (sub: Subscriber | null) =>
    setDetailImsi(sub?.imsi ?? null)
  const terminalCount = subscribers.filter(
    (s) => s.live_status?.terminal_available === true
  ).length
  const offlineCount = subscribers.filter(
    (s) => s.live_status?.registered === false
  ).length
  const unknownCount = subscribers.filter(
    (s) => s.live_status?.registered == null
  ).length

  const registeredCount = useMemo(
    () => subscribers.filter((s) => s.live_status?.registered).length,
    [subscribers]
  )

  const filteredSubscribers = useMemo(() => {
    return subscribers.filter((sub) => {
      const q = search.trim().toLowerCase()
      const { dnn } = resolveSubscriberSlice(sub)
      const allDnns =
        sub.slice?.flatMap((s) => s.session?.map((sess) => sess.name) ?? []) ??
        []
      const matchesSearch =
        !q ||
        sub.imsi.includes(q) ||
        dnn.toLowerCase().includes(q) ||
        allDnns.some((d) => d.toLowerCase().includes(q))

      if (!matchesSearch) return false

      if (statusFilter === 'registered') {
        return Boolean(sub.live_status?.registered)
      }
      if (statusFilter === 'offline') {
        return sub.live_status?.registered === false
      }
      if (statusFilter === 'unknown') {
        return sub.live_status?.registered == null
      }
      if (statusFilter === 'terminals') {
        return sub.live_status?.terminal_available === true
      }
      return true
    })
  }, [subscribers, search, statusFilter])

  const copyToClipboard = (text: string) => {
    void navigator.clipboard.writeText(text).then(() => {
      setCopiedImsi(text)
      setTimeout(() => setCopiedImsi(null), 2000)
      toast.success(`IMSI ${text} copiado`)
    })
  }

  const openEdit = (sub: Subscriber) => {
    const { sst, sd, dnn } = resolveSubscriberSlice(sub)
    setEditSub(sub)
    setEditForm({
      key: '',
      opc: '',
      amf: sub.security?.amf ?? '8000',
      apn_dnn: dnn,
      sst,
      sd: sd ?? '000001',
    })
  }

  return (
    <EmsPage
      title='Suscriptores y Perfiles SIM'
      description='Gestión de identidades 5G/4G sobre la base de datos UDM/UDR con telemetría de registro NAS y sesiones PDU en vivo.'
    >
      {/* FILTER AND TABLE CARD */}
      <Card className='gap-0 overflow-hidden py-0 shadow-none'>
        <CardHeader className='px-4 py-3'>
          <div className='flex flex-wrap items-center justify-between gap-3'>
            <div className='flex flex-wrap items-center gap-2'>
              <Input
                placeholder='Buscar por IMSI o APN…'
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className='h-9 w-full sm:w-56'
                aria-label='Buscar por IMSI o APN'
              />
              <div className='flex flex-wrap rounded-md border bg-muted/30 p-0.5 text-xs'>
                <button
                  type='button'
                  onClick={() => setStatusFilter('all')}
                  className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                    statusFilter === 'all'
                      ? 'bg-background font-semibold text-foreground shadow-xs'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  Perfiles SIM ({subscribers.length})
                </button>
                <button
                  type='button'
                  onClick={() => setStatusFilter('terminals')}
                  aria-pressed={statusFilter === 'terminals'}
                  className={`rounded px-2.5 py-1 text-xs font-medium ${statusFilter === 'terminals' ? 'bg-background text-foreground shadow-xs' : 'text-muted-foreground'}`}
                >
                  Terminales detectados ({terminalCount})
                </button>
                <button
                  type='button'
                  onClick={() => setStatusFilter('registered')}
                  className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                    statusFilter === 'registered'
                      ? 'bg-background font-semibold text-emerald-600 shadow-xs'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  Registrados ({registeredCount})
                </button>
                <button
                  type='button'
                  onClick={() => setStatusFilter('offline')}
                  className={`rounded px-2.5 py-1 text-xs font-medium transition-colors ${
                    statusFilter === 'offline'
                      ? 'bg-background font-semibold text-foreground shadow-xs'
                      : 'text-muted-foreground hover:text-foreground'
                  }`}
                >
                  No registrados ({offlineCount})
                </button>
                <button
                  type='button'
                  onClick={() => setStatusFilter('unknown')}
                  aria-pressed={statusFilter === 'unknown'}
                  className={`rounded px-2.5 py-1 text-xs font-medium ${statusFilter === 'unknown' ? 'bg-background text-foreground shadow-xs' : 'text-muted-foreground'}`}
                >
                  Sin observación ({unknownCount})
                </button>
              </div>
            </div>
            <div className='flex items-center gap-2'>
              {isOperator && (
                <Button variant='outline' size='sm' asChild>
                  <Link to='/charging'>Tarificación 5G</Link>
                </Button>
              )}
              <Button
                variant='outline'
                size='sm'
                onClick={() => void query.refetch()}
                disabled={query.isFetching}
                className='gap-1.5'
              >
                <RefreshCw
                  className={`size-3.5 ${query.isFetching ? 'animate-spin' : ''}`}
                />
                Actualizar
              </Button>
              {isOperator && (
                <Button
                  size='sm'
                  onClick={() => setCreateOpen(true)}
                  className='gap-1.5'
                >
                  <Plus className='size-4' />
                  Nuevo suscriptor
                </Button>
              )}
            </div>
          </div>
          <p className='mt-2 text-xs text-muted-foreground'>
            Un perfil SIM aprovisionado no implica un equipo encendido. El
            teléfono muestra los terminales detectados en UERANSIM.
          </p>
        </CardHeader>
        <CardContent className='p-0'>
          <div className='overflow-x-auto border-t'>
            <Table>
              <TableHeader>
                <TableRow className='bg-muted/40 text-[11px] tracking-wider uppercase'>
                  <TableHead className='font-semibold'>IMSI</TableHead>
                  <TableHead className='font-semibold'>Estado NAS</TableHead>
                  <TableHead className='font-semibold'>
                    Sesión PDU / IP
                  </TableHead>
                  <TableHead className='font-semibold'>
                    Slice (S-NSSAI)
                  </TableHead>
                  <TableHead className='font-semibold'>APN / DNN</TableHead>
                  <TableHead className='text-right font-semibold'>
                    Acciones
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {query.isLoading ? (
                  <TableRow>
                    <TableCell
                      colSpan={6}
                      className='h-32 text-center text-xs text-muted-foreground'
                    >
                      Cargando suscriptores y consultando estado en vivo…
                    </TableCell>
                  </TableRow>
                ) : query.isError ? (
                  <TableRow>
                    <TableCell
                      colSpan={6}
                      className='h-24 text-center text-sm text-destructive'
                    >
                      No se pudieron cargar los suscriptores. Actualiza para
                      reintentar.
                    </TableCell>
                  </TableRow>
                ) : filteredSubscribers.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={6}
                      className='h-32 text-center text-xs text-muted-foreground'
                    >
                      {search || statusFilter !== 'all'
                        ? 'Sin coincidencias para los filtros.'
                        : 'No hay suscriptores aprovisionados.'}
                    </TableCell>
                  </TableRow>
                ) : (
                  filteredSubscribers.map((sub) => {
                    const live = sub.live_status
                    const { dnn, sst, sd } = resolveSubscriberSlice(sub)

                    return (
                      <TableRow
                        key={sub.imsi}
                        className='text-xs hover:bg-muted/30'
                      >
                        <TableCell className='font-mono font-bold whitespace-nowrap'>
                          <div className='flex items-center gap-1.5'>
                            <span>{sub.imsi}</span>
                            <Button
                              variant='ghost'
                              size='icon'
                              className='size-6 text-muted-foreground hover:text-foreground'
                              onClick={() => copyToClipboard(sub.imsi)}
                              title='Copiar IMSI'
                            >
                              {copiedImsi === sub.imsi ? (
                                <Check className='size-3 text-emerald-500' />
                              ) : (
                                <Copy className='size-3' />
                              )}
                            </Button>
                          </div>
                          <span className='text-[10px] font-normal text-muted-foreground'>
                            {sub.terminal_label && sub.terminal_label !== 'UE'
                              ? `${sub.terminal_label} · `
                              : ''}
                            {live?.terminal_available === true
                              ? 'Terminal detectado'
                              : live?.terminal_available === false
                                ? 'Solo perfil SIM'
                                : 'Terminal sin confirmar'}
                          </span>
                        </TableCell>

                        <TableCell className='whitespace-nowrap'>
                          {live?.registered ? (
                            <div className='flex flex-col gap-0.5'>
                              <Badge className='w-fit gap-1 bg-emerald-600 font-mono text-[10px] text-white'>
                                <span className='size-1.5 rounded-full bg-white' />
                                RM-REGISTERED
                              </Badge>
                              <span className='font-mono text-[10px] text-muted-foreground'>
                                {live.cm_state}{' '}
                                {live.cell_id ? `· Cell ${live.cell_id}` : ''}
                              </span>
                            </div>
                          ) : (
                            <Badge
                              variant='outline'
                              className='font-mono text-[10px] text-muted-foreground'
                            >
                              {live?.registered === false
                                ? 'DEREGISTERED'
                                : 'SIN OBSERVACIÓN'}
                            </Badge>
                          )}
                        </TableCell>

                        <TableCell className='font-mono whitespace-nowrap'>
                          {live?.pdu_sessions?.length ? (
                            <div className='flex flex-col gap-0.5'>
                              <Badge
                                variant='secondary'
                                className='w-fit gap-1 font-mono text-[10px] text-sky-600 dark:text-sky-400'
                              >
                                <Wifi className='size-3' />
                                {live.active_ip ??
                                  live.pdu_sessions[0].state ??
                                  'Sin dato'}
                              </Badge>
                              <span className='text-[10px] text-muted-foreground'>
                                {live.pdu_sessions[0].session_id} ·{' '}
                                {live.pdu_sessions[0].apn}
                              </span>
                            </div>
                          ) : (
                            <span className='text-[11px] text-muted-foreground'>
                              Sin sesión PDU observada
                            </span>
                          )}
                        </TableCell>

                        <TableCell className='font-mono text-xs whitespace-nowrap'>
                          <span className='font-semibold'>
                            SST {sst}
                          </span>
                          {sd && (
                            <span className='ml-1 text-muted-foreground'>
                              / SD {sd}
                            </span>
                          )}
                        </TableCell>

                        <TableCell className='font-mono text-xs whitespace-nowrap'>
                          <span className='rounded bg-muted px-1.5 py-0.5 text-foreground'>
                            {dnn}
                          </span>
                        </TableCell>

                        <TableCell className='text-right whitespace-nowrap'>
                          <div className='flex items-center justify-end gap-1'>
                            <Button
                              variant='ghost'
                              size='sm'
                              className='h-7 gap-1 text-xs'
                              onClick={() => setDetailSub(sub)}
                              title='Ver detalles 3GPP y telemetría de sesión'
                            >
                              <Eye className='size-3.5' />
                              Detalle
                            </Button>
                            {isOperator && (
                              <>
                                <Button
                                  variant='ghost'
                                  size='icon'
                                  className='size-7'
                                  onClick={() => openEdit(sub)}
                                  title='Editar suscriptor'
                                >
                                  <Edit className='size-3.5' />
                                </Button>
                                <Button
                                  variant='ghost'
                                  size='icon'
                                  className='size-7 text-destructive hover:bg-destructive/10'
                                  onClick={() => setDeleteTarget(sub.imsi)}
                                  title='Eliminar suscriptor'
                                >
                                  <Trash2 className='size-3.5' />
                                </Button>
                              </>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>
          </div>
          <div className='border-t px-4 py-2 text-xs text-muted-foreground'>
            {filteredSubscribers.length} de {subscribers.length} suscriptores
          </div>
        </CardContent>
      </Card>

      {/* MODAL DETALLES 3GPP (Minimalista estilo Topology) */}
      <Dialog
        open={Boolean(detailSub)}
        onOpenChange={(o) => !o && setDetailSub(null)}
      >
        <DialogContent className='sm:max-w-md p-5' showCloseButton={false}>
          {detailSub && (() => {
            const isRegistered = detailSub.live_status?.registered === true
            const isOffline = detailSub.live_status?.registered === false

            const alias =
              detailSub.terminal_label && detailSub.terminal_label !== 'UE'
                ? detailSub.terminal_label
                : null

            const activeIp =
              detailSub.live_status?.active_ip ||
              detailSub.live_status?.pdu_sessions?.[0]?.address ||
              'Sin sesión PDU'

            const { dnn, sst, sd } = resolveSubscriberSlice(detailSub)

            return (
              <div className='space-y-4'>
                <DialogHeader className='space-y-1 text-left'>
                  <div className='flex items-center justify-between gap-2'>
                    <DialogTitle className='text-lg font-bold tracking-tight'>
                      {alias ? `${alias} · IMSI ${detailSub.imsi}` : `IMSI ${detailSub.imsi}`}
                    </DialogTitle>
                    <Badge
                      variant={isRegistered ? 'default' : 'destructive'}
                      className={cn(
                        'text-[10px] font-semibold uppercase font-mono px-2 py-0.5 rounded-full',
                        isRegistered
                          ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                          : isOffline
                            ? 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                            : 'text-muted-foreground border-border bg-muted/30'
                      )}
                    >
                      {isRegistered
                        ? 'Activo'
                        : isOffline
                          ? 'Inactivo'
                          : 'Sin observación'}
                    </Badge>
                  </div>
                  <p className='text-xs text-muted-foreground font-mono'>
                    {isRegistered
                      ? 'Registrado en la Red 5G'
                      : isOffline
                        ? 'No registrado en la Red 5G'
                        : 'Estado de registro desconocido'}
                  </p>
                  <DialogDescription className='sr-only'>
                    Detalles y telemetría del suscriptor {detailSub.imsi}
                  </DialogDescription>
                </DialogHeader>

                {/* Telemetría esencial minimalista */}
                <div className='rounded-xl border border-border/60 bg-muted/20 p-3'>
                  <div className='grid grid-cols-2 gap-3 text-xs font-mono'>
                    <div>
                      <span className='block text-[10px] uppercase tracking-wider text-muted-foreground font-sans font-medium'>
                        IP de Sesión
                      </span>
                      <span className='font-bold text-foreground text-sm'>
                        {activeIp}
                      </span>
                    </div>
                    <div>
                      <span className='block text-[10px] uppercase tracking-wider text-muted-foreground font-sans font-medium'>
                        Red de Datos (DNN)
                      </span>
                      <span className='font-bold text-foreground text-sm'>
                        {dnn}
                      </span>
                    </div>
                    <div>
                      <span className='block text-[10px] uppercase tracking-wider text-muted-foreground font-sans font-medium'>
                        Slice 5G
                      </span>
                      <span className='text-muted-foreground'>
                        SST {sst}{sd ? ` · SD ${sd}` : ''}
                      </span>
                    </div>
                    <div>
                      <span className='block text-[10px] uppercase tracking-wider text-muted-foreground font-sans font-medium'>
                        Estado NAS
                      </span>
                      <span className='text-muted-foreground'>
                        {detailSub.live_status?.rm_state ?? (isRegistered ? 'RM-REGISTERED' : 'Sin observación')}
                        {detailSub.live_status?.cm_state ? ` (${detailSub.live_status.cm_state})` : ''}
                      </span>
                    </div>
                  </div>
                </div>


                {/* Footer: Cerrar / Operaciones */}
                <DialogFooter className='flex items-center justify-between sm:justify-between pt-3 border-t border-border/40 w-full gap-2'>
                  <Button
                    type='button'
                    variant='ghost'
                    size='sm'
                    onClick={() => setDetailSub(null)}
                    className='text-xs text-muted-foreground hover:text-foreground cursor-pointer h-7 px-2'
                  >
                    Cerrar
                  </Button>

                  {isOperator && (
                    <div className='flex items-center gap-1.5'>
                      <Button
                        type='button'
                        variant='outline'
                        size='sm'
                        onClick={() => {
                          const sub = detailSub
                          setDetailSub(null)
                          openEdit(sub)
                        }}
                        className='h-7 px-2.5 text-[11px] font-medium rounded-lg border-border/70 hover:bg-accent text-foreground cursor-pointer transition-colors'
                        title='Editar suscriptor'
                      >
                        Editar
                      </Button>
                      <Button
                        type='button'
                        variant='outline'
                        size='sm'
                        onClick={() => {
                          const imsi = detailSub.imsi
                          setDetailSub(null)
                          setDeleteTarget(imsi)
                        }}
                        className='h-7 px-2.5 text-[11px] font-medium rounded-lg border-rose-500/25 bg-rose-500/5 text-rose-400 hover:bg-rose-500/15 hover:text-rose-300 cursor-pointer transition-colors'
                        title='Eliminar suscriptor'
                      >
                        Eliminar
                      </Button>
                    </div>
                  )}
                </DialogFooter>
              </div>
            )
          })()}
        </DialogContent>
      </Dialog>

      {/* MODAL CREAR SUSCRIPTOR */}
      <Dialog open={createOpen} onOpenChange={setCreateOpen}>
        <DialogContent className='max-h-[85vh] overflow-y-auto sm:max-w-lg'>
          <DialogHeader>
            <DialogTitle className='flex items-center gap-2 text-base font-bold'>
              <UserPlus className='size-4 text-primary' />
              Aprovisionar Nuevo Suscriptor SIM
            </DialogTitle>
            <DialogDescription className='text-xs'>
              Registra un nuevo IMSI en MongoDB para permitir su registro en el
              Core 5G SA / 4G EPC.
            </DialogDescription>
          </DialogHeader>

          <form
            onSubmit={(e) => {
              e.preventDefault()
              createMutation.mutate(createForm)
            }}
            className='space-y-3 py-2 text-xs'
          >
            <div>
              <Label className='text-xs font-semibold'>
                IMSI (14 o 15 dígitos)
              </Label>
              <Input
                value={createForm.imsi}
                onChange={(e) =>
                  setCreateForm({ ...createForm, imsi: e.target.value })
                }
                placeholder='999700000000002'
                className='mt-1 h-8 font-mono text-xs'
                required
              />
            </div>

            <div className='grid grid-cols-2 gap-3'>
              <div>
                <Label className='text-xs font-semibold'>
                  Clave K (32 hex)
                </Label>
                <Input
                  value={createForm.key}
                  onChange={(e) =>
                    setCreateForm({
                      ...createForm,
                      key: e.target.value.toUpperCase(),
                    })
                  }
                  className='mt-1 h-8 font-mono text-xs'
                  required
                />
              </div>
              <div>
                <Label className='text-xs font-semibold'>OPc (32 hex)</Label>
                <Input
                  value={createForm.opc}
                  onChange={(e) =>
                    setCreateForm({
                      ...createForm,
                      opc: e.target.value.toUpperCase(),
                    })
                  }
                  className='mt-1 h-8 font-mono text-xs'
                  required
                />
              </div>
            </div>

            <div className='grid grid-cols-3 gap-2'>
              <div>
                <Label className='text-xs font-semibold'>APN / DNN</Label>
                <Input
                  value={createForm.apn_dnn}
                  onChange={(e) =>
                    setCreateForm({ ...createForm, apn_dnn: e.target.value })
                  }
                  placeholder='internet'
                  className='mt-1 h-8 font-mono text-xs'
                  required
                />
              </div>
              <div>
                <Label className='text-xs font-semibold'>SST (Slice)</Label>
                <Input
                  type='number'
                  value={createForm.sst}
                  onChange={(e) =>
                    setCreateForm({
                      ...createForm,
                      sst: Number(e.target.value),
                    })
                  }
                  className='mt-1 h-8 font-mono text-xs'
                  min={1}
                  max={255}
                  required
                />
              </div>
              <div>
                <Label className='text-xs font-semibold'>
                  SD (Slice Differentiator)
                </Label>
                <Input
                  value={createForm.sd}
                  onChange={(e) =>
                    setCreateForm({
                      ...createForm,
                      sd: e.target.value.toUpperCase(),
                    })
                  }
                  placeholder='000001'
                  className='mt-1 h-8 font-mono text-xs'
                />
              </div>
            </div>

            <DialogFooter className='pt-3'>
              <Button
                type='button'
                variant='outline'
                onClick={() => setCreateOpen(false)}
              >
                Cancelar
              </Button>
              <Button type='submit' disabled={createMutation.isPending}>
                {createMutation.isPending
                  ? 'Aprovisionando…'
                  : 'Crear Suscriptor'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* MODAL EDITAR SUSCRIPTOR */}
      <Dialog
        open={Boolean(editSub)}
        onOpenChange={(o) => !o && setEditSub(null)}
      >
        <DialogContent className='max-h-[85vh] overflow-y-auto sm:max-w-lg'>
          <DialogHeader>
            <DialogTitle className='flex items-center gap-2 text-base font-bold'>
              <Edit className='size-4 text-primary' />
              Editar Suscriptor · IMSI {editSub?.imsi}
            </DialogTitle>
            <DialogDescription className='text-xs'>
              Modifica los parámetros de slice, sesión de datos o actualiza las
              credenciales de cifrado.
            </DialogDescription>
          </DialogHeader>

          <form
            onSubmit={(e) => {
              e.preventDefault()
              if (!editSub) return
              const payload: Record<string, unknown> = {
                apn_dnn: editForm.apn_dnn,
                sst: editForm.sst,
                sd: editForm.sd,
                amf: editForm.amf,
              }
              if (editForm.key.trim().length === 32) {
                payload.key = editForm.key.trim().toUpperCase()
              }
              if (editForm.opc.trim().length === 32) {
                payload.opc = editForm.opc.trim().toUpperCase()
              }
              updateMutation.mutate({ imsi: editSub.imsi, payload })
            }}
            className='space-y-3 py-2 text-xs'
          >
            <div className='grid grid-cols-2 gap-3'>
              <div>
                <Label className='text-xs font-semibold'>
                  Nueva Clave K (Opcional, 32 hex)
                </Label>
                <Input
                  value={editForm.key}
                  onChange={(e) =>
                    setEditForm({
                      ...editForm,
                      key: e.target.value.toUpperCase(),
                    })
                  }
                  placeholder='Dejar en blanco para mantener'
                  className='mt-1 h-8 font-mono text-xs'
                />
              </div>
              <div>
                <Label className='text-xs font-semibold'>
                  Nuevo OPc (Opcional, 32 hex)
                </Label>
                <Input
                  value={editForm.opc}
                  onChange={(e) =>
                    setEditForm({
                      ...editForm,
                      opc: e.target.value.toUpperCase(),
                    })
                  }
                  placeholder='Dejar en blanco para mantener'
                  className='mt-1 h-8 font-mono text-xs'
                />
              </div>
            </div>

            <div className='grid grid-cols-3 gap-2'>
              <div>
                <Label className='text-xs font-semibold'>APN / DNN</Label>
                <Input
                  value={editForm.apn_dnn}
                  onChange={(e) =>
                    setEditForm({ ...editForm, apn_dnn: e.target.value })
                  }
                  className='mt-1 h-8 font-mono text-xs'
                  required
                />
              </div>
              <div>
                <Label className='text-xs font-semibold'>SST (Slice)</Label>
                <Input
                  type='number'
                  value={editForm.sst}
                  onChange={(e) =>
                    setEditForm({ ...editForm, sst: Number(e.target.value) })
                  }
                  className='mt-1 h-8 font-mono text-xs'
                  min={1}
                  max={255}
                  required
                />
              </div>
              <div>
                <Label className='text-xs font-semibold'>SD (Slice Diff)</Label>
                <Input
                  value={editForm.sd}
                  onChange={(e) =>
                    setEditForm({
                      ...editForm,
                      sd: e.target.value.toUpperCase(),
                    })
                  }
                  className='mt-1 h-8 font-mono text-xs'
                />
              </div>
            </div>

            <DialogFooter className='pt-3'>
              <Button
                type='button'
                variant='outline'
                onClick={() => setEditSub(null)}
              >
                Cancelar
              </Button>
              <Button type='submit' disabled={updateMutation.isPending}>
                {updateMutation.isPending ? 'Guardando…' : 'Guardar Cambios'}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* CONFIRMAR ELIMINACIÓN */}
      <ConfirmDialog
        open={Boolean(deleteTarget)}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title={`Eliminar Suscriptor ${deleteTarget}`}
        desc='Esta acción eliminará el perfil SIM de la base de datos Open5GS UDR permanentemente. El equipo ya no podrá registrarse.'
        confirmText='Eliminar Suscriptor'
        destructive
        isLoading={deleteMutation.isPending}
        handleConfirm={() =>
          deleteTarget && deleteMutation.mutate(deleteTarget)
        }
      />
    </EmsPage>
  )
}
