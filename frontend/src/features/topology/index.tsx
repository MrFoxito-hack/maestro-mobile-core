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
import { ScrollArea } from '@/components/ui/scroll-area'
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
      description='Vista física del testbed y arquitectura de modelos de comunicación SBA 3GPP Rel-16.'
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
                const isUpf = component.id === 'upf' || component.id === 'upf2'
                const upfInstances = isUpf
                  ? (status.data?.components ?? []).filter(
                      (c) =>
                        c.id === 'upf' ||
                        c.id === 'upf2' ||
                        c.kind === 'user-plane'
                    )
                  : []
                const isMultiUpf = upfInstances.length > 1

                const isSmf = component.id === 'smf' || component.id === 'smf2'
                const smfInstances = isSmf
                  ? (status.data?.components ?? []).filter(
                      (c) => c.id === 'smf' || c.id === 'smf2'
                    )
                  : []
                const isMultiSmf = smfInstances.length > 1

                const isMultiInstance = isMultiUpf || isMultiSmf
                const activeInstances = isMultiUpf ? upfInstances : smfInstances

                const componentAlarms = (alarmCenter.data?.items ?? []).filter(
                  (a) => {
                    if (isMultiUpf) {
                      return upfInstances.some(
                        (u) =>
                          a.component === u.id ||
                          a.component?.toLowerCase() === u.id.toLowerCase() ||
                          (a.node_id &&
                            (a.node_id === u.node_id ||
                              a.node_id === 'upf-vm' ||
                              a.node_id === 'upf-vm2'))
                      )
                    }
                    if (isMultiSmf) {
                      return smfInstances.some(
                        (s) =>
                          a.component === s.id ||
                          a.component?.toLowerCase() === s.id.toLowerCase()
                      )
                    }
                    return alarmBelongsToComponent(a, component)
                  }
                )

                const allInstancesRunning =
                  isMultiInstance &&
                  activeInstances.every((c) => c.status === 'running')

                const isRunning = isMultiInstance
                  ? allInstancesRunning
                  : component.status === 'running'

                const modalTitle = isMultiUpf
                  ? 'UPF (Plano de Usuario CUPS)'
                  : isMultiSmf
                    ? 'SMF (Plano de Control Dual-SMF)'
                    : component.label

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
                          {isMultiInstance
                            ? `${activeInstances.filter((c) => c.status === 'running').length}/${activeInstances.length} activas`
                            : isRunning
                              ? 'Activo'
                              : 'Detenido'}
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

                    {/* Tarjetas compactas si es Multi-Instancia (UPF o SMF) */}
                    {isMultiInstance && (
                      <div className='grid grid-cols-2 gap-2 text-xs'>
                        {activeInstances.map((inst) => {
                          const isCorp =
                            inst.id.includes('2') ||
                            inst.label.toLowerCase().includes('corporate')
                          const sliceName = isCorp
                            ? 'Slice Corporativo'
                            : 'Slice Internet'
                          const isSelected = component.id === inst.id
                          return (
                            <div
                              key={inst.id}
                              onClick={() =>
                                setSelection({ type: 'component', id: inst.id })
                              }
                              className={cn(
                                'rounded-lg border p-2 space-y-1 cursor-pointer transition select-none',
                                isSelected
                                  ? 'border-primary bg-primary/10 shadow-sm'
                                  : 'bg-muted/30 hover:bg-muted/60 border-border/70'
                              )}
                            >
                              <div className='flex items-center justify-between'>
                                <span className={cn('font-mono font-bold text-[11px]', isSelected && 'text-primary')}>
                                  {inst.label}
                                </span>
                                <span
                                  className={cn(
                                    'size-2 rounded-full',
                                    inst.status === 'running'
                                      ? 'bg-emerald-400'
                                      : 'bg-rose-400'
                                  )}
                                />
                              </div>
                              <div className='flex items-center justify-between text-[10px] text-muted-foreground'>
                                <span>{sliceName}</span>
                                {isSelected && (
                                  <span className='text-[9px] font-semibold text-primary uppercase'>
                                    Seleccionado
                                  </span>
                                )}
                              </div>
                            </div>
                          )
                        })}
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
  const title = host?.hostname ?? runtime?.hostname ?? 'Host del testbed'
  const role =
    host?.role ??
    `Vista física obtenida por ${runtime?.source ?? 'fuente desconocida'}`
  const ifaces = host?.interfaces ?? runtime?.interfaces ?? []
  const ports = host?.listening_ports ?? runtime?.listening_ports ?? []
  const hostComps = host
    ? components.filter((c) => {
        if (host.id === 'upf-vm')
          return c.node_id === 'upf-vm' || c.id === 'upf'
        if (host.id === 'upf-vm2')
          return c.node_id === 'upf-vm2' || c.id === 'upf2'
        if (host.id === 'gnb-vm')
          return c.node_id === 'gnb-vm' || c.id === 'gnb'
        if (host.id === 'ue-vm') return c.node_id === 'ue-vm' || c.id === 'ue'
        return (
          c.node_id === 'core' ||
          (!['upf-vm', 'upf-vm2', 'gnb-vm', 'ue-vm'].includes(c.node_id) &&
            !['upf', 'upf2', 'gnb', 'ue'].includes(c.id))
        )
      })
    : components

  return (
    <div className='space-y-4'>
      <DialogHeader className='space-y-1 text-left'>
        <div className='flex items-center justify-between gap-2'>
          <DialogTitle className='font-mono font-bold'>{title}</DialogTitle>
          {host?.ip && (
            <Badge
              variant='outline'
              className='font-mono text-xs font-semibold'
            >
              {host.ip}
            </Badge>
          )}
        </div>
        <DialogDescription className='text-xs text-muted-foreground'>{role}</DialogDescription>
      </DialogHeader>
      <ScrollArea className='max-h-80 px-1'>
        <h3 className='mb-2 text-sm font-semibold'>Interfaces de Red</h3>
        <div className='space-y-2'>
          {ifaces.map((item) => (
            <div key={item.name} className='rounded-md border bg-card/60 p-3'>
              <div className='flex items-center justify-between'>
                <b className='font-mono text-sm'>{item.name}</b>
                <Badge variant={item.state === 'up' ? 'default' : 'secondary'}>
                  {item.state}
                </Badge>
              </div>
              <p className='mt-1 font-mono text-xs text-muted-foreground'>
                {item.addresses
                  .map(
                    (address) => `${address.address}/${address.prefix_length}`
                  )
                  .join(', ') || 'Sin direcciones'}
              </p>
            </div>
          ))}
        </div>
        <h3 className='mt-5 mb-2 text-sm font-semibold'>
          Funciones alojadas en este host ({hostComps.length})
        </h3>
        <div className='flex flex-wrap gap-2'>
          {hostComps.map((item) => (
            <Badge
              key={item.id}
              variant={item.status === 'running' ? 'default' : 'destructive'}
            >
              {item.label}
            </Badge>
          ))}
        </div>
        <h3 className='mt-5 mb-2 text-sm font-semibold'>Sockets en escucha</h3>
        <p className='text-sm text-muted-foreground'>
          {ports.length} endpoints de red detectados.
        </p>
      </ScrollArea>
      <DialogFooter className='pt-2 border-t border-border/40'>
        <Button
          type='button'
          variant='ghost'
          size='sm'
          onClick={onClose}
          className='text-xs text-muted-foreground hover:text-foreground cursor-pointer'
        >
          Cerrar
        </Button>
      </DialogFooter>
    </div>
  )
}
