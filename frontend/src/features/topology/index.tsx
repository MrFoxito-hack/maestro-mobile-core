import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  AlertTriangle,
  BookOpen,
  Brain,
  Radio,
  ShieldAlert,
  Terminal,
} from 'lucide-react'
import { useAuthStore } from '@/stores/auth-store'
import { useScenarioStore } from '@/stores/scenario-store'
import {
  api,
  canOperate,
  type ComponentStatus,
  type RuntimeSnapshot,
  type ScenarioStatus,
} from '@/lib/api'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { cn } from '@/lib/utils'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { EmsPage } from '@/features/ems-page'
import {
  EmsTopology,
  type TopologySelection,
  type TopologyView,
} from './ems-topology'
import { alarmBelongsToComponent } from './topology-alarm'
import { ScpModelsDialog } from './scp-models-dialog'

export function TopologyPage() {
  const scenario = useScenarioStore((state) => state.scenario)
  const [view, setView] = useState<TopologyView>('models')
  const [selection, setSelection] = useState<TopologySelection | null>(null)
  const [pendingAction, setPendingAction] = useState<
    'start' | 'stop' | 'restart' | null
  >(null)
  const [scpDialogOpen, setScpDialogOpen] = useState(false)
  const queryClient = useQueryClient()
  const user = useAuthStore((state) => state.auth.user)

  const status = useQuery({
    queryKey: ['status', scenario],
    queryFn: async () =>
      (await api.get<ScenarioStatus>(`/scenarios/${scenario}/status`)).data,
    refetchInterval: 3000,
  })
  const runtime = useQuery({
    queryKey: ['runtime', scenario],
    queryFn: async () =>
      (await api.get<RuntimeSnapshot>(`/runtime/${scenario}`)).data,
    refetchInterval: 5000,
  })
  const alarmCenter = useQuery({
    queryKey: ['alarm-center', scenario],
    queryFn: async () =>
      (
        await api.get<{
          items: {
            id: string
            component: string
            node_id?: string
            network_function: string
            severity: string
            message: string
            evidence?: string
            first_seen: number
          }[]
        }>(`/alarm-center/${scenario}`)
      ).data,
    refetchInterval: 4000,
  })
  const rawComponents = status.data?.components ?? []
  const is5g = rawComponents.some((c) => c.id === 'amf' || c.id === 'gnb')
  const hasAf = rawComponents.some((c) => c.id === 'af')
  const hasNwdaf = rawComponents.some((c) => c.id === 'nwdaf')
  const extras: ComponentStatus[] = []

  if (is5g && !hasAf) {
    extras.push({
      id: 'af',
      label: 'AF',
      kind: 'application',
      node_id: 'core',
      unit: 'maestro-video-af',
      interfaces: ['N5', 'N6'],
      status: 'running',
      procedures: [
        'Npcf_PolicyAuthorization (N5 / HTTP/2 REST)',
        'Modelo A: Comunicación directa sin NRF (3GPP TS 23.501 Cl. 7.1.4)',
        'Servidor de Video Streaming HLS / fMP4 (N6 / Plano de Usuario)',
        'Control Dinámico de Sesión y QoS Boost (5QI=2, GBR 10M / MBR 20M)',
      ],
      expected_endpoints: [
        {
          interface: 'N5',
          protocol: 'http2',
          address: '10.210.50.1',
          port: 7777,
        },
        {
          interface: 'N6',
          protocol: 'http',
          address: '10.210.50.1',
          port: 18090,
        },
      ],
      config_paths: [],
      depends_on: ['pcf'],
    } as ComponentStatus)
  }

  if (is5g && !hasNwdaf) {
    extras.push({
      id: 'nwdaf',
      label: 'NWDAF',
      kind: 'analytics',
      node_id: 'core',
      unit: 'maestro-nwdaf',
      interfaces: ['Nnwdaf', 'N23'],
      status: 'running',
      procedures: [
        'Nnwdaf_AnalyticsInfo (3GPP TS 29.520 Cl. 5.2 / HTTP/2 REST)',
        'Nnwdaf_EventsSubscription (3GPP TS 29.520 Cl. 5.3 / HTTP/2 REST)',
        'Control en Bucle Cerrado Autónomo (Closed-Loop) con PCF (3GPP TS 23.288)',
        'Inferencia de IA/ML: SLICE_LOAD_LEVEL, ABNORMAL_BEHAVIOUR, SERVICE_EXPERIENCE',
      ],
      expected_endpoints: [
        {
          interface: 'Nnwdaf',
          protocol: 'http2',
          address: '10.210.50.1',
          port: 9095,
        },
        {
          interface: 'N23',
          protocol: 'http2',
          address: '10.210.50.1',
          port: 9095,
        },
      ],
      config_paths: ['/etc/open5gs/nwdaf.yaml'],
      depends_on: ['pcf', 'mongodb', 'chf'],
    } as ComponentStatus)
  }

  const allComponents = extras.length > 0 ? [...rawComponents, ...extras] : rawComponents

  const component =
    selection?.type === 'component'
      ? allComponents.find((item) => item.id === selection.id)
      : undefined
  const operation = useMutation({
    mutationFn: async (action: 'start' | 'stop' | 'restart') => {
      if (action === 'restart') {
        try {
          return (
            await api.post<ScenarioStatus>(
              `/scenarios/${scenario}/components/${component?.id}/restart`
            )
          ).data
        } catch {
          // Fallback si el proceso del backend en ejecucion no tenia cargada la nueva ruta /restart
          await api.post(
            `/scenarios/${scenario}/components/${component?.id}/stop`
          )
          await new Promise((resolve) => setTimeout(resolve, 600))
          return (
            await api.post<ScenarioStatus>(
              `/scenarios/${scenario}/components/${component?.id}/start`
            )
          ).data
        }
      }
      return (
        await api.post<ScenarioStatus>(
          `/scenarios/${scenario}/components/${component?.id}/${action}`
        )
      ).data
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['status', scenario] })
      void queryClient.invalidateQueries({ queryKey: ['alarms', scenario] })
      setPendingAction(null)
    },
    onError: (err) => {
      console.error('Error al operar componente:', err)
      setPendingAction(null)
    },
  })

  return (
    <EmsPage
      title='Topología'
      description='Topología física, interfaces 3GPP, modelos SBA y arquitectura de la tríada Network Slicing.'
    >
      <Card className='h-[calc(100dvh-7rem)]'>
        <CardHeader className='flex-row items-center justify-between'>
          <div className='flex items-center gap-2.5'>
            <Tabs
              value={view === 'telco' ? 'models' : view}
              onValueChange={(value) => setView(value as TopologyView)}
            >
              <TabsList>
                <TabsTrigger value='physical'>Física</TabsTrigger>
                <TabsTrigger value='models'>Modelos SBA</TabsTrigger>
                <TabsTrigger value='interfaces'>Interfaces 3GPP</TabsTrigger>
                <TabsTrigger value='slicing'>Tríada Slicing</TabsTrigger>
              </TabsList>
            </Tabs>

            {view === 'models' && (
              <Button
                variant='outline'
                size='sm'
                onClick={() => setScpDialogOpen(true)}
                className='h-7 px-2 text-[11px] font-normal gap-1.5 text-muted-foreground hover:text-foreground border-dashed bg-muted/20 hover:bg-muted/40 transition-colors'
                title='Ver escenarios de aplicación SCP y modelos de comunicación 3GPP Rel-16'
              >
                <BookOpen className='size-3 text-sky-500' />
                <span className='hidden sm:inline'>Escenarios SCP (3GPP Rel-16)</span>
                <span className='sm:hidden'>Escenarios SCP</span>
              </Button>
            )}
          </div>
          <CardTitle className='sr-only'>
            {status.data?.state ?? 'Conectando'} ·{' '}
            {status.data?.components.length ?? 0} componentes
          </CardTitle>
        </CardHeader>
        <CardContent className='h-[calc(100%-5rem)]'>
          <EmsTopology
            components={allComponents}
            runtime={runtime.data}
            alarms={alarmCenter.data?.items ?? []}
            view={view}
            onSelect={setSelection}
          />
        </CardContent>
      </Card>

      <Dialog
        open={Boolean(selection)}
        onOpenChange={(open) => !open && setSelection(null)}
      >
        <DialogContent className='sm:max-w-md p-5' showCloseButton={false}>
          {selection?.type === 'host' ? (
            <HostDetail
              runtime={runtime.data}
              components={status.data?.components ?? []}
              selectedHostId={selection.id}
              onClose={() => setSelection(null)}
            />
          ) : component ? (
            <>
              {(() => {
                const componentAlarms = (alarmCenter.data?.items ?? []).filter(
                  (a) => alarmBelongsToComponent(a, component)
                )

                const isRunning = component.status === 'running'

                const modalTitle = (() => {
                  if (component.id === 'smf') return 'SMF-01 · eMBB'
                  if (component.id === 'smf2') return 'SMF-02 · MIoT'
                  if (component.id === 'smf3') return 'SMF-03 · URLLC'
                  if (component.id === 'upf') return 'UPF-01 · eMBB'
                  if (component.id === 'upf2') return 'UPF-02 · MIoT'
                  if (component.id === 'upf3') return 'UPF-03 · URLLC'
                  return component.label
                })()

                return (
                  <div className='space-y-4'>
                    <DialogHeader className='space-y-1 text-left'>
                      <div className='flex items-center justify-between gap-2'>
                        <DialogTitle className='text-lg font-bold tracking-tight'>
                          {modalTitle}
                        </DialogTitle>
                        <Badge
                          variant={isRunning ? 'default' : 'destructive'}
                          className={cn(
                            'text-[10px] font-semibold uppercase font-mono px-2 py-0.5',
                            isRunning
                              ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                              : 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                          )}
                        >
                          {isRunning ? 'Activo' : 'Detenido'}
                        </Badge>
                      </div>
                      <DialogDescription className='sr-only'>
                        Acciones del componente {component.label}
                      </DialogDescription>
                    </DialogHeader>

                    {/* Alerta de incidentes si existen */}
                    {componentAlarms.length > 0 && (
                      <div className='rounded-lg border border-destructive/30 bg-destructive/10 p-2.5 text-xs'>
                        <div className='flex items-center justify-between'>
                          <div className='flex items-center gap-1.5 font-medium text-destructive'>
                            <AlertTriangle className='size-3.5' />
                            <span>{componentAlarms.length} alarma(s) activa(s)</span>
                          </div>
                          <Link
                            to={'/alarms' as any}
                            search={{ component: component.id } as any}
                          >
                            <Button
                              variant='ghost'
                              size='sm'
                              className='h-6 px-1.5 text-[11px] text-destructive hover:bg-destructive/20'
                            >
                              Ver en Alarmas
                            </Button>
                          </Link>
                        </div>
                      </div>
                    )}

                    {/* Acceso directo a Analítica si es NWDAF */}
                    {component.id === 'nwdaf' && (
                      <div className='pt-1'>
                        <Link to={'/nwdaf' as any} className='w-full block'>
                          <Button
                            variant='outline'
                            className='w-full justify-between h-9 px-3 text-xs font-medium border-purple-500/35 bg-purple-500/10 hover:bg-purple-500/20 text-purple-300 transition cursor-pointer'
                          >
                            <div className='flex items-center gap-2'>
                              <Brain className='size-4 text-purple-400' />
                              <span>Panel de Analítica e Inferencia NWDAF</span>
                            </div>
                            <Badge
                              variant='outline'
                              className='text-[10px] font-mono border-purple-500/40 text-purple-300 bg-purple-950/40'
                            >
                              Closed-Loop ML
                            </Badge>
                          </Button>
                        </Link>
                      </div>
                    )}

                    {/* ACCIONES */}
                    <div className='pt-1'>
                      <div className='grid grid-cols-3 gap-2'>
                        {/* 1. Consola MML */}
                        <Link
                          to={'/commands' as any}
                          search={{ component: component.id } as any}
                          className='flex-1'
                        >
                          <Button
                            variant='outline'
                            className='w-full h-auto py-2.5 px-2 flex flex-col items-center justify-center gap-1.5 rounded-xl border border-border/60 bg-card hover:bg-accent hover:border-primary/50 transition cursor-pointer group'
                          >
                            <div className='flex size-8 items-center justify-center rounded-lg bg-primary/10 text-primary group-hover:scale-110 transition'>
                              <Terminal className='size-4' />
                            </div>
                            <div className='text-xs font-semibold text-foreground leading-tight text-center'>
                              Consola MML
                            </div>
                          </Button>
                        </Link>

                        {/* 2. Capturar Traza PCAP */}
                        <Link to='/traces/node' className='flex-1'>
                          <Button
                            variant='outline'
                            className='w-full h-auto py-2.5 px-2 flex flex-col items-center justify-center gap-1.5 rounded-xl border border-border/60 bg-card hover:bg-accent hover:border-sky-500/50 transition cursor-pointer group'
                          >
                            <div className='flex size-8 items-center justify-center rounded-lg bg-sky-500/10 text-sky-400 group-hover:scale-110 transition'>
                              <Radio className='size-4' />
                            </div>
                            <div className='text-xs font-semibold text-foreground leading-tight text-center'>
                              Traza PCAP
                            </div>
                          </Button>
                        </Link>

                        {/* 3. Centro de Alarmas */}
                        <Link
                          to={'/alarms' as any}
                          search={{ component: component.id } as any}
                          className='flex-1'
                        >
                          <Button
                            variant='outline'
                            className='w-full h-auto py-2.5 px-2 flex flex-col items-center justify-center gap-1.5 rounded-xl border border-border/60 bg-card hover:bg-accent hover:border-amber-500/50 transition cursor-pointer group'
                          >
                            <div className='flex size-8 items-center justify-center rounded-lg bg-amber-500/10 text-amber-400 group-hover:scale-110 transition'>
                              <ShieldAlert className='size-4' />
                            </div>
                            <div className='text-xs font-semibold text-foreground leading-tight text-center'>
                              Alarmas
                            </div>
                          </Button>
                        </Link>
                      </div>
                    </div>

                    {/* Footer: Control de Servicio / Cerrar */}
                    <DialogFooter className='flex items-center justify-between sm:justify-between pt-3 border-t border-border/40 w-full gap-2'>
                      <Button
                        type='button'
                        variant='ghost'
                        size='sm'
                        onClick={() => setSelection(null)}
                        className='text-xs text-muted-foreground hover:text-foreground cursor-pointer h-7 px-2'
                      >
                        Cerrar
                      </Button>

                      {canOperate(user?.role) && (
                        <div className='flex items-center gap-1.5'>
                          {component.status === 'running' ? (
                            <>
                              <Button
                                type='button'
                                variant='outline'
                                size='sm'
                                onClick={() => setPendingAction('restart')}
                                className='h-7 px-2.5 text-[11px] font-medium rounded-lg border-border/70 hover:bg-accent text-foreground cursor-pointer transition-colors'
                                title={`Reiniciar servicio ${component.label}`}
                              >
                                Reiniciar
                              </Button>
                              <Button
                                type='button'
                                variant='outline'
                                size='sm'
                                onClick={() => setPendingAction('stop')}
                                className='h-7 px-2.5 text-[11px] font-medium rounded-lg border-rose-500/25 bg-rose-500/5 text-rose-400 hover:bg-rose-500/15 hover:text-rose-300 cursor-pointer transition-colors'
                                title={`Detener servicio ${component.label}`}
                              >
                                Apagar
                              </Button>
                            </>
                          ) : (
                            <Button
                              type='button'
                              variant='outline'
                              size='sm'
                              onClick={() => setPendingAction('start')}
                              className='h-7 px-3 text-[11px] font-medium rounded-lg border-emerald-500/30 bg-emerald-500/10 text-emerald-400 hover:bg-emerald-500/20 hover:text-emerald-300 cursor-pointer transition-colors'
                              title={`Iniciar servicio ${component.label}`}
                            >
                              Encender
                            </Button>
                          )}
                        </div>
                      )}
                    </DialogFooter>
                  </div>
                )
              })()}
            </>
          ) : null}
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={Boolean(pendingAction)}
        onOpenChange={(open) => !open && setPendingAction(null)}
        title={`${
          pendingAction === 'restart'
            ? 'Reiniciar'
            : pendingAction === 'stop'
              ? 'Apagar'
              : 'Encender'
        } ${component?.label ?? 'componente'}`}
        desc={
          pendingAction === 'restart'
            ? 'Se reiniciará el servicio en la VM correspondiente y se reestablecerán las asociaciones de red.'
            : pendingAction === 'stop'
              ? 'El servicio se detendrá temporalmente en la VM.'
              : 'El servicio se iniciará en la VM correspondiente.'
        }
        confirmText={
          pendingAction === 'restart'
            ? 'Confirmar reinicio'
            : pendingAction === 'stop'
              ? 'Confirmar apagado'
              : 'Confirmar encendido'
        }
        destructive={pendingAction === 'stop'}
        isLoading={operation.isPending}
        handleConfirm={() => pendingAction && operation.mutate(pendingAction)}
      />

      <ScpModelsDialog
        open={scpDialogOpen}
        onOpenChange={setScpDialogOpen}
      />
    </EmsPage>
  )
}

const HOST_META: Record<string, { title: string; role: string; ip: string }> = {
  core: {
    title: 'EMS-CORE',
    role: '5G Core (CP + Multi-SMF)',
    ip: '10.210.50.1',
  },
  'upf-vm': {
    title: 'EMS-UPF-01',
    role: 'UPF-01 (eMBB)',
    ip: '10.210.50.8',
  },
  'upf-urllc': {
    title: 'EMS-UPF-03',
    role: 'UPF-03 (URLLC / eBPF)',
    ip: '10.210.50.22',
  },
  'upf-vm2': {
    title: 'EMS-UPF-02',
    role: 'UPF-02 (MIoT)',
    ip: '10.210.50.9',
  },
  'gnb-vm': {
    title: 'EMS-GNB-01',
    role: 'gNodeB (RAN 5G)',
    ip: '10.210.50.10',
  },
  'ue-vm': {
    title: 'EMS-UE-01',
    role: 'UE (Tríada 3 Slices)',
    ip: '10.210.50.11',
  },
}

function HostDetail({
  runtime,
  components,
  selectedHostId,
  onClose,
}: {
  runtime?: RuntimeSnapshot
  components: ScenarioStatus['components']
  selectedHostId?: string
  onClose?: () => void
}) {
  const host = runtime?.hosts?.find((h) => h.id === selectedHostId)
  const meta = (selectedHostId && HOST_META[selectedHostId]) || null
  const title = meta?.title ?? host?.hostname ?? 'Host Testbed'
  const role = meta?.role ?? host?.role ?? 'Nodo Físico'
  const ip = host?.ip || meta?.ip || ''

  const hostComps = components.filter((c) => {
    if (selectedHostId === 'upf-urllc')
      return c.node_id === 'upf-urllc' || c.id === 'upf3'
    if (selectedHostId === 'upf-vm')
      return c.node_id === 'upf-vm' || c.id === 'upf'
    if (selectedHostId === 'upf-vm2')
      return c.node_id === 'upf-vm2' || c.id === 'upf2'
    if (selectedHostId === 'gnb-vm')
      return c.node_id === 'gnb-vm' || c.id === 'gnb'
    if (selectedHostId === 'ue-vm')
      return c.node_id === 'ue-vm' || c.id === 'ue'
    if (selectedHostId === 'core')
      return (
        c.node_id === 'core' ||
        (!['upf-vm', 'upf-vm2', 'upf-urllc', 'gnb-vm', 'ue-vm'].includes(
          c.node_id
        ) &&
          !['upf', 'upf2', 'upf3', 'gnb', 'ue'].includes(c.id))
      )
    if (host) {
      return c.node_id === host.id
    }
    return false
  })

  const activeCount = hostComps.filter((c) => c.status === 'running').length
  const isHealthy = hostComps.length === 0 || activeCount === hostComps.length

  const rawIfaces =
    host?.interfaces ??
    (runtime?.hosts && runtime.hosts.length > 1 ? [] : runtime?.interfaces ?? [])

  const filteredIfaces = rawIfaces
    .filter((item) => {
      const n = item.name.toLowerCase()
      return n !== 'lo' && !n.startsWith('lo:')
    })
    .map((item) => {
      const ipv4 = item.addresses
        .filter(
          (a) =>
            a.family === 'inet' ||
            (!a.address.includes(':') && !a.address.startsWith('fe80'))
        )
        .map((a) => (a.prefix_length ? `${a.address}/${a.prefix_length}` : a.address))
      const allAddrs = item.addresses
        .filter((a) => !a.address.startsWith('fe80:'))
        .map((a) => (a.prefix_length ? `${a.address}/${a.prefix_length}` : a.address))
      const displayAddrs = ipv4.length > 0 ? ipv4 : allAddrs
      return {
        name: item.name,
        state: item.state,
        addresses: displayAddrs.join(', ') || 'Sin IP asignada',
        isUp: item.state.toLowerCase() === 'up',
      }
    })

  const ifacesList =
    filteredIfaces.length > 0
      ? filteredIfaces
      : ip
        ? [{ name: 'eth0', state: 'up', addresses: `${ip}/24`, isUp: true }]
        : []

  return (
    <div className='space-y-4 text-left'>
      <DialogHeader className='space-y-1 text-left'>
        <div className='flex items-center justify-between gap-2'>
          <div className='flex items-center gap-2 min-w-0'>
            <DialogTitle className='text-lg font-bold tracking-tight font-mono'>
              {title}
            </DialogTitle>
            {ip && (
              <span className='font-mono text-xs px-2 py-0.5 rounded bg-muted/60 text-muted-foreground border border-border/50 shrink-0'>
                {ip}
              </span>
            )}
          </div>
          <Badge
            variant={isHealthy ? 'default' : 'destructive'}
            className={cn(
              'text-[10px] font-semibold uppercase font-mono px-2 py-0.5 shrink-0',
              isHealthy
                ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                : 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
            )}
          >
            {isHealthy ? 'Online' : 'Alerta'}
          </Badge>
        </div>
        <DialogDescription className='text-xs text-muted-foreground'>
          {role}
        </DialogDescription>
      </DialogHeader>

      <div className='max-h-[60vh] overflow-y-auto space-y-4 pr-0.5'>
        {/* Funciones Alojadas */}
        {hostComps.length > 0 && (
          <div className='space-y-2'>
            <div className='flex items-center justify-between text-[11px] font-medium text-muted-foreground px-0.5'>
              <span>Funciones Alojadas</span>
              <span className='font-mono text-[10px]'>
                {activeCount}/{hostComps.length} activas
              </span>
            </div>
            <div className='flex flex-wrap gap-1.5'>
              {hostComps.map((comp) => {
                const isRunning = comp.status === 'running'
                const label = (() => {
                  if (comp.id === 'smf') return 'SMF-01'
                  if (comp.id === 'smf2') return 'SMF-02'
                  if (comp.id === 'smf3') return 'SMF-03'
                  if (comp.id === 'upf') return 'UPF-01'
                  if (comp.id === 'upf2') return 'UPF-02'
                  if (comp.id === 'upf3') return 'UPF-03'
                  return comp.label || comp.id.toUpperCase()
                })()
                return (
                  <span
                    key={comp.id}
                    className={cn(
                      'inline-flex items-center gap-1 font-mono text-[10px] font-semibold px-2 py-0.5 rounded-md border transition-colors',
                      isRunning
                        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                        : 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                    )}
                  >
                    <span
                      className={cn(
                        'size-1.5 rounded-full',
                        isRunning ? 'bg-emerald-400' : 'bg-rose-400'
                      )}
                    />
                    {label}
                  </span>
                )
              })}
            </div>
          </div>
        )}

        {/* Interfaces de Red */}
        <div className='space-y-2'>
          <div className='flex items-center justify-between text-[11px] font-medium text-muted-foreground px-0.5'>
            <span>Interfaces de Red</span>
            <span className='font-mono text-[10px]'>
              {ifacesList.length} interfaces
            </span>
          </div>
          <div className='rounded-lg border border-border/50 divide-y divide-border/40 overflow-hidden bg-muted/15'>
            {ifacesList.map((item) => (
              <div
                key={item.name}
                className='flex items-center justify-between px-3 py-2 text-xs'
              >
                <div className='flex items-center gap-2 min-w-0'>
                  <span className='font-mono font-bold text-foreground text-[11px] shrink-0'>
                    {item.name}
                  </span>
                  <span className='text-[10px] text-muted-foreground font-mono truncate'>
                    {item.addresses}
                  </span>
                </div>
                <span
                  className={cn(
                    'text-[9px] font-mono font-bold px-1.5 py-0.5 rounded uppercase shrink-0',
                    item.isUp
                      ? 'text-emerald-400 bg-emerald-500/10 border border-emerald-500/25'
                      : 'text-zinc-400 bg-zinc-500/10 border border-zinc-500/25'
                  )}
                >
                  {item.state}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <DialogFooter className='pt-2 border-t border-border/30'>
        <Button
          type='button'
          variant='outline'
          size='sm'
          onClick={onClose}
          className='w-full h-8 text-xs font-medium cursor-pointer hover:bg-muted/40'
        >
          Cerrar
        </Button>
      </DialogFooter>
    </div>
  )
}

