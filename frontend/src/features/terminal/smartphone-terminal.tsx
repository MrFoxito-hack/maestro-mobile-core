import { TerminalScope } from './terminal-scope'
import type { ReactNode } from 'react'
import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  CreditCard,
  Film,
  Globe,
  Layers,
  Loader2,
  Lock,
  Plane,
  Radio,
  RefreshCw,
  Search,
  Settings2,
  Smartphone,
  X,
  Zap,
} from 'lucide-react'
import { useAuthStore } from '@/stores/auth-store'
import { useScenarioStore } from '@/stores/scenario-store'
import { api } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import { VideoLab } from './video-lab'
import { PhoneApp } from './phone-app'
import { NotesApp } from './notes-app'
import { SafariApp } from './safari-app'

type State = {
  active_apn: 'internet' | 'corporate' | '5g-plus'
  apn_sessions: Array<{
    apn: 'internet' | 'corporate' | '5g-plus'
    interface: string
    address: string
    snssai: { sst: number; sd: number | null } | null
    rx_bytes?: number
    tx_bytes?: number
  }>
  service: string
  registered: boolean
  subscriber: string | null
  observed_at: string
  interfaces: Array<{
    name: string
    addresses: string[]
    rx_bytes?: number
    tx_bytes?: number
  }>
  native_state: Record<string, unknown>
  charging_available: boolean
  balance: {
    quota_bytes: number
    consumed_bytes: number
    reserved_bytes: number
    available_bytes: number
  } | null
  available_nodes?: Array<{
    imsi: string
    label: string
  }>
  af_boost?: {
    active: boolean
    qos: '5QI=2' | '5QI=9'
    pcc_rule?: string | null
    session_url?: string | null
    gbr_dl?: string
    mbr_dl?: string
    applied_at?: string
    note?: string
  }
}

const mb = (bytes: number) =>
  (bytes / 1_000_000).toLocaleString('es-PE', { maximumFractionDigits: 2 })

function IosSwitch({
  checked,
  onCheckedChange,
  disabled,
  color = '#34c759',
  label,
}: {
  checked: boolean
  onCheckedChange: (checked: boolean) => void
  disabled?: boolean
  color?: string
  label: string
}) {
  return (
    <button
      role='switch'
      type='button'
      aria-label={label}
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      style={{
        backgroundColor: checked ? color : '#39393d',
      }}
      className='relative inline-flex h-[30px] w-[50px] shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none disabled:opacity-40'
    >
      <span
        className={cn(
          'pointer-events-none inline-block size-[26px] transform rounded-full bg-white shadow-[0_2px_4px_rgba(0,0,0,0.3)] ring-0 transition duration-200 ease-in-out',
          checked ? 'translate-x-5' : 'translate-x-0'
        )}
      />
    </button>
  )
}

function IosRow({
  icon,
  iconBg,
  label,
  value,
  hasChevron,
  onClick,
  rightElement,
  disabled,
  subtitle,
}: {
  icon?: React.ReactNode
  iconBg?: string
  label: string
  value?: React.ReactNode
  hasChevron?: boolean
  onClick?: () => void
  rightElement?: React.ReactNode
  disabled?: boolean
  subtitle?: string
}) {
  const content = (
    <div className='flex items-center justify-between px-3.5 py-2.5 min-h-[44px]'>
      <div className='flex items-center gap-3 min-w-0'>
        {icon && (
          <div
            style={{ backgroundColor: iconBg || '#007aff' }}
            className='flex size-7 items-center justify-center rounded-[7px] text-white shadow-sm shrink-0'
          >
            {icon}
          </div>
        )}
        <div className='min-w-0'>
          <div className='text-[14px] font-normal text-white truncate'>{label}</div>
          {subtitle && (
            <div className='text-[11px] text-[#8e8e93] leading-tight truncate'>{subtitle}</div>
          )}
        </div>
      </div>
      <div className='flex items-center gap-1.5 shrink-0 ml-2'>
        {value && <span className='text-[13px] text-[#8e8e93] font-normal'>{value}</span>}
        {rightElement}
        {hasChevron && <ChevronRight className='size-4 text-[#8e8e93]/70' />}
      </div>
    </div>
  )

  if (onClick) {
    return (
      <button
        type='button'
        disabled={disabled}
        onClick={onClick}
        className='w-full text-left transition-colors active:bg-[#2c2c2e]/60 disabled:opacity-40'
      >
        {content}
      </button>
    )
  }

  return <div>{content}</div>
}

export function SmartphoneTerminal() {
  const [open, setOpen] = useState(false)
  return <TerminalScope kind='smartphone'>{(device, selector, meta) =>
    <ScopedSmartphone
      selectedImsi={device.supi}
      selector={selector}
      devices={meta?.devices ?? [device]}
      onSelectImsi={meta?.setSelected}
      open={open}
      setOpen={setOpen}
    />
  }</TerminalScope>
}

function ScopedSmartphone({
  selectedImsi,
  selector,
  devices = [],
  onSelectImsi,
  open,
  setOpen,
}: {
  selectedImsi: string
  selector: ReactNode
  devices?: Array<{ id: string; supi: string; label: string; kind: 'smartphone' | 'vehicle' | 'sensor' }>
  onSelectImsi?: (supi: string) => void
  open: boolean
  setOpen: (open: boolean) => void
}) {
  const [islandOpen, setIslandOpen] = useState(false)
  const [activeApp, setActiveApp] = useState<
    'home' | 'settings' | 'stream' | 'balance' | 'phone' | 'notes' | 'safari'
  >('home')
  const [initialDialCode, setInitialDialCode] = useState<string | null>(null)
  const [settingsView, setSettingsView] = useState<'root' | 'apn' | 'qos' | 'diagnostic'>('root')
  const [cellularData, setCellularData] = useState(true)
  const username = useAuthStore((s) => s.auth.user?.username)
  const [currentTime, setCurrentTime] = useState('')
  const [probe, setProbe] = useState<{
    completed: boolean
    received_bytes: number
    duration_seconds: number
    local_ip: string | null
    http_status: number
  } | null>(null)
  const [notice, setNotice] = useState('')
  const [isStreamingActive, setIsStreamingActive] = useState(false)

  const pendingTopup = useRef<string | null>(null)

  // Mouse drag-to-scroll (touch-like smartphone scrolling emulation)
  const scrollRef = useRef<HTMLDivElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const dragStartRef = useRef<{ y: number; scrollTop: number; moved: boolean }>({
    y: 0,
    scrollTop: 0,
    moved: false,
  })

  const handleMouseDown = (e: React.MouseEvent) => {
    if (
      e.button !== 0 ||
      activeApp === 'home' ||
      activeApp === 'phone' ||
      activeApp === 'notes' ||
      activeApp === 'safari'
    )
      return
    const target = e.target as HTMLElement
    if (target.closest('input, select, textarea, [role="switch"]')) return

    dragStartRef.current = {
      y: e.clientY,
      scrollTop: scrollRef.current?.scrollTop ?? 0,
      moved: false,
    }
    setIsDragging(true)
  }

  useEffect(() => {
    if (!isDragging) return

    const handleMouseMove = (e: MouseEvent) => {
      if (!scrollRef.current) return
      const deltaY = e.clientY - dragStartRef.current.y
      if (Math.abs(deltaY) > 4) {
        dragStartRef.current.moved = true
      }
      scrollRef.current.scrollTop = dragStartRef.current.scrollTop - deltaY
    }

    const handleMouseUp = () => {
      setIsDragging(false)
    }

    window.addEventListener('mousemove', handleMouseMove)
    window.addEventListener('mouseup', handleMouseUp)
    return () => {
      window.removeEventListener('mousemove', handleMouseMove)
      window.removeEventListener('mouseup', handleMouseUp)
    }
  }, [isDragging])

  const handleClickCapture = (e: React.MouseEvent) => {
    if (dragStartRef.current.moved) {
      e.stopPropagation()
      e.preventDefault()
      dragStartRef.current.moved = false
    }
  }

  const scenario = useScenarioStore((s) => s.scenario)
  const role = useAuthStore((s) => s.auth.user?.role)
  const enabled = scenario === '5g-sa' && (role === 'student' || role === 'teacher' || role === 'admin')
  const qc = useQueryClient()

  // Real-time clock for status bar
  useEffect(() => {
    const updateTime = () => {
      const now = new Date()
      setCurrentTime(
        now.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })
      )
    }
    updateTime()
    const timer = setInterval(updateTime, 1000)
    return () => clearInterval(timer)
  }, [])

  const status = useQuery({
    queryKey: ['terminal-status', username, selectedImsi],
    queryFn: async () =>
      (
        await api.get<State>(
          selectedImsi
            ? `/terminal/status?imsi=${encodeURIComponent(selectedImsi)}`
            : '/terminal/status'
        )
      ).data,
    enabled: open && enabled,
    refetchInterval: open ? 4000 : false,
    retry: false,
  })

  // Dynamic Island alerts removed by user request (no golden popup or message banner)
  const triggerIslandAlert = (_message?: string) => {}

  // AF Boost Mutation
  const afBoostMutation = useMutation({
    mutationFn: async (enable: boolean) => {
      const res = await api.post('/terminal/af-boost', {
        enabled: enable,
        imsi: selectedImsi,
      })
      return res.data
    },
    onSuccess: (data) => {
      qc.setQueryData<State>(['terminal-status', username, selectedImsi], (curr) =>
        curr ? { ...curr, af_boost: data } : curr
      )
      void qc.invalidateQueries({ queryKey: ['terminal-status'] })
      if (data.active) {
        triggerIslandAlert('⚡ Prioridad 5QI=2 QoS Boost Concedida')
      } else {
        triggerIslandAlert('Prioridad estándar 5QI=9 restablecida')
      }
    },
    onError: (err: unknown) => {
      const errorObj = err as { response?: { data?: { detail?: string } } }
      const msg = errorObj?.response?.data?.detail || 'No se pudo modificar la política N5'
      setNotice(msg)
      triggerIslandAlert('Fallo en autorización de QoS')
    },
  })

  // Command mutation for standard operations
  const command = useMutation({
    mutationFn: async ({ path, data }: { path: string; data?: unknown }) => {
      return (await api.post('/terminal/' + path, data)).data
    },
    onSuccess: (result, variables) => {
      if (variables.path === 'traffic/n6-probe') setProbe(result)
      if (variables.path === 'topup') {
        pendingTopup.current = null
        triggerIslandAlert('+50 MB recargados · Sesión PDU lista')
      }
      if (variables.path === 'apn') {
        qc.setQueryData<State>(['terminal-status', username, selectedImsi], (current) =>
          current ? { ...current, active_apn: result.active_apn } : current
        )
        triggerIslandAlert(
          result.active_apn === 'corporate'
            ? 'Conectado a Red Corporativa'
            : 'Conectado a Red Internet'
        )
      }
      if (variables.path === 'airplane-mode') {
        triggerIslandAlert(result.enabled ? 'Modo avión activado' : 'Conectando a red 5G…')
      }
      void qc.invalidateQueries({ queryKey: ['terminal-status'] })
      void qc.invalidateQueries({ queryKey: ['charging'] })
    },
    onError: () => {
      setNotice('Operación no confirmada por la red.')
    },
  })

  if (!enabled) return null

  const data = status.isError ? undefined : status.data
  const activeApn = data?.active_apn ?? 'internet'
  const activeSession = data?.apn_sessions?.find((s) => s.apn === activeApn)
  const offline = data?.service === 'inactive'
  const balance = data?.balance
  const remaining = balance ? Math.max(0, balance.available_bytes) : null
  const percent =
    balance && remaining !== null
      ? Math.min(100, (remaining / Math.max(1, balance.quota_bytes)) * 100)
      : 0
  const busy = command.isPending || status.isPending || afBoostMutation.isPending || !data
  const isBoostActive = Boolean(data?.af_boost?.active)

  const run = (path: string, body?: unknown) => {
    setNotice('')
    const payload = { ...(body && typeof body === 'object' ? body : {}), imsi: selectedImsi }
    command.mutate({ path, data: payload })
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <button
          aria-label='Abrir terminal 5G'
          className='fixed right-6 bottom-8 z-40 flex items-center gap-2.5 rounded-full border border-teal-500/30 bg-slate-950/90 px-4 py-3 text-sm font-semibold text-white shadow-[0_8px_30px_rgb(0,0,0,0.6)] backdrop-blur-md transition-all hover:scale-105 hover:border-teal-400 hover:bg-slate-900 focus-visible:outline-2 focus-visible:outline-teal-400'
        >
          <div className='relative flex items-center justify-center'>
            <Smartphone className='size-4 text-teal-300' />
            {isBoostActive && (
              <span className='absolute -top-1 -right-1 size-2 rounded-full bg-amber-400 animate-ping' />
            )}
          </div>
          <span>Terminal 5G</span>
          {isBoostActive && (
            <span className='rounded bg-amber-400/20 px-1.5 py-0.5 text-[10px] font-bold text-amber-300'>
              5G+
            </span>
          )}
        </button>
      </DialogTrigger>

      <DialogContent
        showCloseButton={false}
        className='fixed top-auto right-4 bottom-4 left-auto flex h-[780px] max-h-[calc(100dvh-1.5rem)] w-[380px] max-w-[calc(100vw-2rem)] translate-x-0 translate-y-0 flex-col gap-0 overflow-hidden rounded-[52px] border-[10px] border-slate-800 bg-black p-0 text-slate-100 shadow-[0_30px_90px_rgba(0,0,0,0.9),0_0_0_1px_rgba(255,255,255,0.1)] sm:max-w-[380px]'
      >
        <DialogTitle className='sr-only'>Terminal Smartphone 5G SA</DialogTitle>
        <DialogDescription className='sr-only'>
          Dispositivo móvil conectado al core Open5GS SA con soporte de AF N5 QoS Boost.
        </DialogDescription>

        <div className='sr-only'>{selector}</div>

        {/* Authentic iPhone Wallpaper Background (Visible ONLY on Home Screen, completely hidden in Settings & apps) */}
        {activeApp === 'home' && (
          <div className='pointer-events-none absolute inset-0 z-0 overflow-hidden animate-in fade-in duration-200'>
            <div
              className='size-full bg-cover bg-center scale-105 filter blur-[5px]'
              style={{
                backgroundImage: "url('/images/iphone-wallpaper.jpg')",
              }}
            />
          </div>
        )}

        {/* Physical Button Visual Hints on Smartphone Edge */}
        <div className='pointer-events-none absolute -left-[12px] top-28 h-8 w-[3px] rounded-l bg-slate-600/70' />
        <div className='pointer-events-none absolute -left-[12px] top-40 h-12 w-[3px] rounded-l bg-slate-600/70' />
        <div className='pointer-events-none absolute -left-[12px] top-56 h-12 w-[3px] rounded-l bg-slate-600/70' />
        <div className='pointer-events-none absolute -right-[12px] top-36 h-16 w-[3px] rounded-r bg-slate-600/70' />

        {/* Floating Close Button outside hardware bezel */}
        <DialogClose
          className='absolute -top-3.5 -right-3.5 z-50 flex size-8 items-center justify-center rounded-full bg-slate-800 border border-white/20 text-slate-300 shadow-xl backdrop-blur-md transition hover:scale-110 hover:bg-slate-700 hover:text-white focus:outline-none cursor-pointer'
          aria-label='Cerrar teléfono'
        >
          <X className='size-4' />
        </DialogClose>

        {/* TOP STATUS BAR (Authentic iOS Header) */}
        <header className='relative z-30 flex h-11 shrink-0 items-center justify-between px-6 pt-1 text-slate-200 select-none'>
          {/* Left: Clock (Authentic Apple SF Typography) */}
          <div className='flex items-center'>
            <span className='font-semibold text-[14.5px] tracking-tight text-white font-sans'>
              {currentTime || '9:41'}
            </span>
          </div>

          {/* Center: Dynamic Island (Authentic Hardware Notch & Interactive Selector) */}
          <div
            className={cn(
              'absolute left-1/2 top-2 -translate-x-1/2 rounded-full bg-black shadow-lg transition-all duration-300 flex items-center justify-between px-2.5 z-40 select-none',
              isStreamingActive && activeApp !== 'stream'
                ? 'h-6.5 min-w-[120px] border border-sky-500/30 bg-black cursor-pointer'
                : 'h-6.5 min-w-[105px] border border-white/10 hover:border-white/30 cursor-pointer group'
            )}
            onClick={() => {
              if (isStreamingActive && activeApp !== 'stream') {
                setActiveApp('stream')
              } else {
                setIslandOpen((prev) => !prev)
              }
            }}
            title='Dynamic Island · Click para cambiar de terminal'
          >
            {isStreamingActive && activeApp !== 'stream' ? (
              <button
                type='button'
                onClick={() => setActiveApp('stream')}
                className='flex size-full items-center justify-between gap-1.5 px-1 text-[10px] text-white cursor-pointer select-none group'
                title='Regresar a Stream5G'
              >
                <div className='flex items-center gap-1.5 min-w-0'>
                  <div className='size-1.5 rounded-full bg-[#0a84ff] animate-ping shrink-0' />
                  <span className='font-semibold text-white group-hover:text-[#0a84ff] transition truncate text-[9.5px]'>
                    Stream5G
                  </span>
                </div>
                <div className='flex items-center gap-[2px] h-2.5 shrink-0'>
                  <span className='w-[2px] h-2 bg-[#0a84ff] rounded-full animate-pulse' />
                  <span className='w-[2px] h-2.5 bg-[#0a84ff] rounded-full animate-pulse' />
                  <span className='w-[2px] h-1.5 bg-[#0a84ff] rounded-full animate-pulse' />
                </div>
              </button>
            ) : (
              <>
                {/* Left: Camera sensor + Terminal Suffix */}
                <div className='flex items-center gap-1.5'>
                  <div className='size-2 rounded-full bg-slate-900 border border-slate-700/60' />
                  <span className='text-[10px] font-mono font-bold text-slate-300 group-hover:text-emerald-400 transition-colors'>
                    {selectedImsi.slice(-3)}
                  </span>
                </div>

                {/* Right: Slice Tag & Chevron */}
                <div className='flex items-center gap-0.5'>
                  <span className='text-[8.5px] font-semibold uppercase tracking-wider text-slate-400 group-hover:text-white transition-colors'>
                    eMBB
                  </span>
                  <ChevronDown className={cn('size-2.5 text-slate-500 group-hover:text-white transition-transform duration-200', islandOpen && 'rotate-180 text-white')} />
                </div>
              </>
            )}
          </div>

          {/* Right: Signal Bars, 5G Network Badge, Battery */}
          <div className='flex items-center gap-2'>
            {/* Signal Bars (Authentic iOS 4-Bar layout) */}
            <div className='flex items-end gap-[2px] h-3'>
              <span className={cn('w-[3px] rounded-[1px]', offline ? 'h-1 bg-slate-600' : 'h-1 bg-white')} />
              <span className={cn('w-[3px] rounded-[1px]', offline ? 'h-1.5 bg-slate-600' : 'h-1.5 bg-white')} />
              <span className={cn('w-[3px] rounded-[1px]', offline ? 'h-2 bg-slate-600' : 'h-2 bg-white')} />
              <span className={cn('w-[3px] rounded-[1px]', offline ? 'h-2.5 bg-slate-600' : 'h-2.5 bg-white')} />
            </div>

            {/* 5G / 5G+ Dynamic Indicator (Hidden when offline or when cellular data is disabled) */}
            {offline ? (
              <Plane className='size-3 text-[#ff9500]' />
            ) : !cellularData ? null : isBoostActive ? (
              <span className='flex items-center gap-0.5 rounded-[4px] bg-gradient-to-r from-amber-400 via-orange-400 to-amber-500 px-1 py-[1px] text-[9.5px] font-black text-slate-950 shadow-[0_0_8px_rgba(245,158,11,0.5)] animate-pulse leading-none'>
                5G+
              </span>
            ) : (
              <span className='text-[11.5px] font-bold tracking-tight text-white font-sans'>
                5G
              </span>
            )}

            {/* Decorative stimulus; not measured battery or charging balance. */}
            <div className='flex items-center' role='img' aria-label='Batería ilustrativa, no medida' title='Batería ilustrativa, no medida'>
              <div className='relative flex h-[11.5px] w-[21px] items-center rounded-[3.5px] border border-white/70 p-[1.5px]'>
                <div
                  className={cn(
                    'h-full rounded-[1.5px] transition-all',
                    'w-full bg-[#34c759]'
                  )}
                />
                <div className='absolute -right-[3px] top-1/2 -translate-y-1/2 h-[4px] w-[1.5px] rounded-r-xs bg-white/70' />
              </div>
            </div>
          </div>
        </header>

        {/* EXPANDED DYNAMIC ISLAND (Apple-style Modal Overlay) */}
        {islandOpen && (
          <>
            {/* Backdrop click outside to close */}
            <div
              className='absolute inset-0 z-45 bg-black/60 backdrop-blur-xs animate-in fade-in duration-150'
              onClick={() => setIslandOpen(false)}
            />

            {/* Expanded Island Card */}
            <div className='absolute left-1/2 top-2.5 -translate-x-1/2 w-[300px] max-w-[92%] rounded-[22px] bg-black/95 text-white border border-white/15 shadow-[0_20px_50px_rgba(0,0,0,0.9)] backdrop-blur-2xl p-3 z-50 animate-in zoom-in-95 fade-in duration-150'>
              {/* Header */}
              <div className='flex items-center justify-between pb-2 border-b border-white/10 px-1'>
                <span className='text-[12px] font-semibold text-white'>
                  Dispositivos
                </span>
                <button
                  type='button'
                  onClick={(e) => {
                    e.stopPropagation()
                    setIslandOpen(false)
                  }}
                  className='flex size-6 items-center justify-center rounded-full bg-white/10 text-slate-400 hover:bg-white/20 hover:text-white transition-colors cursor-pointer'
                  title='Cerrar'
                >
                  <X className='size-3.5' />
                </button>
              </div>

              {/* Device Options */}
              <div className='mt-2 flex flex-col gap-1.5'>
                {devices.map((d) => {
                  const isSelected = d.supi === selectedImsi
                  const suffix = d.supi.slice(-3)
                  const cleanLabel = d.label
                    .replace(/\s*·\s*Smartphone\s*(\([^)]*\))?/i, '')
                    .replace(/\s*\((eMBB|URLLC|MIoT|5G)\)/i, '')
                    .trim() || d.label

                  return (
                    <button
                      key={d.id}
                      type='button'
                      onClick={() => {
                        if (onSelectImsi && !isSelected) {
                          onSelectImsi(d.supi)
                        }
                        setIslandOpen(false)
                      }}
                      className={cn(
                        'flex items-center justify-between rounded-xl px-2.5 py-2 text-left transition-all cursor-pointer group/item',
                        isSelected
                          ? 'bg-emerald-500/15 border border-emerald-500/40 text-white'
                          : 'bg-white/5 border border-transparent hover:bg-white/10 text-slate-300 hover:text-white'
                      )}
                    >
                      <div className='flex items-center gap-2.5 min-w-0'>
                        <div
                          className={cn(
                            'flex size-7 shrink-0 items-center justify-center rounded-lg font-mono text-[11px] font-bold',
                            isSelected
                              ? 'bg-emerald-500 text-slate-950 font-black'
                              : 'bg-white/10 text-slate-400'
                          )}
                        >
                          {suffix}
                        </div>
                        <span className='text-[12px] font-medium truncate'>
                          {cleanLabel}
                        </span>
                      </div>
                      {isSelected ? (
                        <span className='rounded-full bg-emerald-500/20 px-2 py-0.5 text-[9.5px] font-semibold text-emerald-300 border border-emerald-500/30 shrink-0'>
                          Activo
                        </span>
                      ) : (
                        <span className='text-[9.5px] text-slate-400 group-hover/item:text-white shrink-0 px-2 py-0.5'>
                          Conectar
                        </span>
                      )}
                    </button>
                  )
                })}
              </div>
            </div>
          </>
        )}

        {/* MAIN PHONE SCREEN CONTENT (Scrollable App View with Drag-to-Scroll & Hidden Scrollbar) */}
        <main
          ref={scrollRef}
          onMouseDown={handleMouseDown}
          onClickCapture={handleClickCapture}
          className={cn(
            'relative z-10 min-h-0 flex-1 overflow-y-auto px-4 py-3 select-none',
            '[&::-webkit-scrollbar]:hidden [scrollbar-width:none] [-ms-overflow-style:none]',
            activeApp === 'home'
              ? 'overflow-y-hidden flex flex-col justify-between py-2 px-3 cursor-default'
              : activeApp === 'phone' || activeApp === 'notes' || activeApp === 'safari'
                ? 'overflow-y-hidden flex flex-col p-0 cursor-default'
                : isDragging
                  ? 'cursor-grabbing'
                  : 'cursor-grab'
          )}
        >
          {/* APP 0: iOS Home Screen (SpringBoard) with Original Apple Icons */}
          {activeApp === 'home' && (
            <div className='flex flex-col justify-between h-full pt-1 pb-1 animate-in fade-in zoom-in-95 duration-200 select-none'>
              {/* Top Breathing Space */}
              <div className='h-0.5' />

              {/* iOS 4x3 App Grid with Original Apple Vector Icons */}
              <div className='grid grid-cols-4 gap-x-3 gap-y-4 px-1'>
                {/* 1. Calendario (Apple Dynamic Calendar) */}
                <button
                  type='button'
                  onClick={() =>
                    triggerIslandAlert(
                      `Calendario: ${new Intl.DateTimeFormat('es-PE', {
                        weekday: 'long',
                        day: 'numeric',
                        month: 'short',
                      }).format(new Date())}`
                    )
                  }
                  className='flex flex-col items-center gap-1 group cursor-pointer'
                  title='Calendario'
                >
                  <div className='flex size-[54px] flex-col overflow-hidden rounded-[13px] bg-white shadow-md ring-1 ring-black/5 transition-transform group-active:scale-90'>
                    <div className='flex h-3.5 items-center justify-center bg-[#ff3b30] text-[8.5px] font-bold text-white uppercase tracking-wider'>
                      {new Intl.DateTimeFormat('es-PE', { weekday: 'short' })
                        .format(new Date())
                        .replace('.', '')
                        .slice(0, 3)}
                    </div>
                    <div className='flex flex-1 items-center justify-center text-xl font-bold text-slate-900 leading-none'>
                      {new Date().getDate()}
                    </div>
                  </div>
                  <span className='text-[11px] font-normal text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Calendario
                  </span>
                </button>

                {/* 2. Fotos (Original Apple SVG) */}
                <button
                  type='button'
                  onClick={() => triggerIslandAlert('Fotos: Galería iCloud sincronizada')}
                  className='flex flex-col items-center gap-1 group cursor-pointer'
                  title='Fotos'
                >
                  <img
                    src='/images/ios-icons/photos.svg'
                    alt='Fotos'
                    className='size-[54px] select-none pointer-events-none filter drop-shadow-[0_2px_8px_rgba(0,0,0,0.3)] transition-transform group-active:scale-90'
                  />
                  <span className='text-[11px] font-normal text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Fotos
                  </span>
                </button>

                {/* 3. Cámara (Original Apple SVG) */}
                <button
                  type='button'
                  onClick={() => triggerIslandAlert('Cámara: 4K 60fps ProRes')}
                  className='flex flex-col items-center gap-1 group cursor-pointer'
                  title='Cámara'
                >
                  <img
                    src='/images/ios-icons/camera.svg'
                    alt='Cámara'
                    className='size-[54px] select-none pointer-events-none filter drop-shadow-[0_2px_8px_rgba(0,0,0,0.3)] transition-transform group-active:scale-90'
                  />
                  <span className='text-[11px] font-normal text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Cámara
                  </span>
                </button>

                {/* 4. Clima (Original Apple SVG) */}
                <button
                  type='button'
                  onClick={() => triggerIslandAlert('Clima: Lima 21°C · Soleado')}
                  className='flex flex-col items-center gap-1 group cursor-pointer'
                  title='Clima'
                >
                  <img
                    src='/images/ios-icons/weather.svg'
                    alt='Clima'
                    className='size-[54px] select-none pointer-events-none filter drop-shadow-[0_2px_8px_rgba(0,0,0,0.3)] transition-transform group-active:scale-90'
                  />
                  <span className='text-[11px] font-normal text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Clima
                  </span>
                </button>

                {/* 5. Reloj (Original Apple SVG) */}
                <button
                  type='button'
                  onClick={() =>
                    triggerIslandAlert(`Reloj: ${currentTime || '09:41'} · Alarma 07:00 AM`)
                  }
                  className='flex flex-col items-center gap-1 group cursor-pointer'
                  title='Reloj'
                >
                  <img
                    src='/images/ios-icons/clock.svg'
                    alt='Reloj'
                    className='size-[54px] select-none pointer-events-none filter drop-shadow-[0_2px_8px_rgba(0,0,0,0.3)] transition-transform group-active:scale-90'
                  />
                  <span className='text-[11px] font-normal text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Reloj
                  </span>
                </button>

                {/* 6. Mapas (Original Apple SVG) */}
                <button
                  type='button'
                  onClick={() => triggerIslandAlert('Mapas: Ubicación gNodeB PUCP')}
                  className='flex flex-col items-center gap-1 group cursor-pointer'
                  title='Mapas'
                >
                  <img
                    src='/images/ios-icons/maps.svg'
                    alt='Mapas'
                    className='size-[54px] select-none pointer-events-none filter drop-shadow-[0_2px_8px_rgba(0,0,0,0.3)] transition-transform group-active:scale-90'
                  />
                  <span className='text-[11px] font-normal text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Mapas
                  </span>
                </button>

                {/* 7. Notas (Original Apple SVG) */}
                <button
                  type='button'
                  onClick={() => setActiveApp('notes')}
                  className='flex flex-col items-center gap-1 group cursor-pointer'
                  title='Notas'
                >
                  <img
                    src='/images/ios-icons/notes.svg'
                    alt='Notas'
                    className='size-[54px] select-none pointer-events-none filter drop-shadow-[0_2px_8px_rgba(0,0,0,0.3)] transition-transform group-active:scale-90'
                  />
                  <span className='text-[11px] font-normal text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Notas
                  </span>
                </button>

                {/* 8. Recordatorios (Original Apple SVG) */}
                <button
                  type='button'
                  onClick={() => triggerIslandAlert('Recordatorios: 3 tareas al día')}
                  className='flex flex-col items-center gap-1 group cursor-pointer'
                  title='Recordatorios'
                >
                  <img
                    src='/images/ios-icons/reminders.svg'
                    alt='Recordatorios'
                    className='size-[54px] select-none pointer-events-none filter drop-shadow-[0_2px_8px_rgba(0,0,0,0.3)] transition-transform group-active:scale-90'
                  />
                  <span className='text-[11px] font-normal text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Recordatorios
                  </span>
                </button>

                {/* --- ROW 3: THE 4 CORE 5G APPS (ORIGINAL APPLE ICONS) --- */}
                {/* 9. Configuración (Original Apple Settings SVG) */}
                <button
                  type='button'
                  onClick={() => {
                    setActiveApp('settings')
                    setSettingsView('root')
                  }}
                  className='flex flex-col items-center gap-1 group cursor-pointer'
                  title='Configuración'
                >
                  <img
                    src='/images/ios-icons/settings.svg'
                    alt='Configuración'
                    className='size-[54px] select-none pointer-events-none filter drop-shadow-[0_2px_8px_rgba(0,0,0,0.3)] transition-transform group-active:scale-90'
                  />
                  <span className='text-[11px] font-medium text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Configuración
                  </span>
                </button>

                {/* 10. Stream5G (Original Apple TV SVG) */}
                <button
                  type='button'
                  onClick={() => setActiveApp('stream')}
                  className='flex flex-col items-center gap-1 group cursor-pointer relative'
                  title='Stream5G'
                >
                  <div className='relative transition-transform group-active:scale-90'>
                    <img
                      src='/images/ios-icons/tv.svg'
                      alt='Stream5G'
                      className='size-[54px] select-none pointer-events-none filter drop-shadow-[0_2px_8px_rgba(0,0,0,0.3)]'
                    />
                    {isBoostActive && (
                      <span className='absolute -top-1 -right-1 flex h-4 items-center justify-center rounded-full bg-amber-400 px-1 text-[8.5px] font-black text-slate-950 shadow-md animate-pulse'>
                        5G+
                      </span>
                    )}
                  </div>
                  <span className='text-[11px] font-medium text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Stream5G
                  </span>
                </button>

                {/* 11. Mi 5G (Original Apple Wallet SVG) */}
                <button
                  type='button'
                  onClick={() => setActiveApp('balance')}
                  className='flex flex-col items-center gap-1 group cursor-pointer relative'
                  title='Mi 5G'
                >
                  <div className='relative transition-transform group-active:scale-90'>
                    <img
                      src='/images/ios-icons/wallet.svg'
                      alt='Mi 5G'
                      className='size-[54px] select-none pointer-events-none filter drop-shadow-[0_2px_8px_rgba(0,0,0,0.3)]'
                    />
                    {remaining != null && remaining === 0 && (
                      <span className='absolute -top-1 -right-1 flex size-3 items-center justify-center rounded-full bg-rose-500 text-[8px] font-bold text-white shadow' />
                    )}
                  </div>
                  <span className='text-[11px] font-medium text-white tracking-tight drop-shadow-[0_1px_2px_rgba(0,0,0,0.8)] truncate max-w-[64px]'>
                    Mi 5G
                  </span>
                </button>

              </div>

              {/* iOS SpringBoard Page Dots */}
              <div className='flex items-center justify-center gap-1.5 py-1'>
                <span className='size-1.5 rounded-full bg-white shadow-sm' />
                <span className='size-1.5 rounded-full bg-white/35' />
              </div>

              {/* Floating iOS Dock with Original Apple SVG Icons */}
              <div className='mx-1 rounded-[30px] bg-white/15 p-2 backdrop-blur-sm border border-white/20 shadow-[0_10px_30px_rgba(0,0,0,0.35)] flex items-center justify-around'>
                {/* 1. Teléfono (Original Apple Phone SVG) */}
                <button
                  type='button'
                  onClick={() => setActiveApp('phone')}
                  className='transition-transform active:scale-90 cursor-pointer'
                  title='Teléfono'
                >
                  <img
                    src='/images/ios-icons/phone.svg'
                    alt='Teléfono'
                    className='size-[50px] select-none pointer-events-none filter drop-shadow-[0_2px_6px_rgba(0,0,0,0.3)]'
                  />
                </button>

                {/* 2. Safari (Original Apple Safari SVG) */}
                <button
                  type='button'
                  onClick={() => setActiveApp('safari')}
                  className='transition-transform active:scale-90 cursor-pointer'
                  title='Safari'
                >
                  <img
                    src='/images/ios-icons/safari.svg'
                    alt='Safari'
                    className='size-[50px] select-none pointer-events-none filter drop-shadow-[0_2px_6px_rgba(0,0,0,0.3)]'
                  />
                </button>

                {/* 3. Mensajes (Original Apple Messages SVG) */}
                <button
                  type='button'
                  onClick={() => triggerIslandAlert('Mensajes: 3GPP SMS over NAS Conectado')}
                  className='transition-transform active:scale-90 cursor-pointer'
                  title='Mensajes'
                >
                  <img
                    src='/images/ios-icons/messages.svg'
                    alt='Mensajes'
                    className='size-[50px] select-none pointer-events-none filter drop-shadow-[0_2px_6px_rgba(0,0,0,0.3)]'
                  />
                </button>

                {/* 4. Música (Original Apple Music SVG) */}
                <button
                  type='button'
                  onClick={() => triggerIslandAlert('Música: Audio Espacial 5G Lossless')}
                  className='transition-transform active:scale-90 cursor-pointer'
                  title='Música'
                >
                  <img
                    src='/images/ios-icons/music.svg'
                    alt='Música'
                    className='size-[50px] select-none pointer-events-none filter drop-shadow-[0_2px_6px_rgba(0,0,0,0.3)]'
                  />
                </button>
              </div>
            </div>
          )}

          {/* APP 1: Stream5G Video App (Authentic iOS Multimedia Player with Background PiP) */}
          <div
            className={cn(
              activeApp === 'stream' ? 'flex flex-col gap-3 animate-in fade-in duration-150' : 'contents'
            )}
          >
            {activeApp === 'stream' && (
              /* iOS Navigation Header */
              <div className='flex items-center justify-between py-1'>
                <button
                  type='button'
                  onClick={() => setActiveApp('home')}
                  className='flex items-center text-[14px] font-medium text-[#0a84ff] hover:opacity-80 transition -ml-1 cursor-pointer'
                >
                  <ChevronLeft className='size-5 mr-0.5' />
                  Inicio
                </button>
                <div className='flex items-center gap-1.5'>
                  <span className='text-[15px] font-semibold text-white'>Stream5G</span>
                </div>
                <div className='w-14 flex justify-end'>
                  {isBoostActive ? (
                    <span className='flex items-center gap-1 rounded-full bg-amber-400/20 px-2 py-0.5 text-[9px] font-bold text-amber-300 border border-amber-400/30 animate-pulse'>
                      5G+
                    </span>
                  ) : (
                    <span className='text-[10px] text-[#8e8e93] font-medium'>
                      5QI=9
                    </span>
                  )}
                </div>
              </div>
            )}

            {activeApp === 'stream' && !cellularData ? (
              <div className='rounded-2xl bg-[#1c1c1e] p-6 text-center ring-1 ring-white/5 shadow-md'>
                <div className='mx-auto flex size-14 items-center justify-center rounded-2xl bg-[#ff9f0a]/15 text-[#ff9f0a]'>
                  <Radio className='size-7 opacity-90' />
                </div>
                <h3 className='mt-4 text-[16px] font-semibold text-white'>
                  Datos celulares desactivados
                </h3>
                <p className='mt-2 text-[12px] leading-relaxed text-[#8e8e93]'>
                  Activa los datos celulares en Configuración para reproducir contenido multimedia en alta resolución con 5G.
                </p>
                <button
                  onClick={() => {
                    setActiveApp('settings')
                    setSettingsView('root')
                  }}
                  className='mt-5 inline-flex items-center gap-2 rounded-2xl bg-[#0a84ff] px-5 py-3 text-[13px] font-semibold text-white hover:bg-[#0071e3] transition shadow-md active:scale-98 cursor-pointer'
                >
                  <Settings2 className='size-4' /> Abrir Configuración
                </button>
              </div>
            ) : activeApp === 'stream' && activeApn !== 'internet' ? (
              <div className='rounded-2xl bg-[#1c1c1e] p-6 text-center ring-1 ring-white/5 shadow-md'>
                <div className='mx-auto flex size-14 items-center justify-center rounded-2xl bg-[#ff9f0a]/15 text-[#ff9f0a]'>
                  <Lock className='size-7 opacity-90' />
                </div>
                <h3 className='mt-4 text-[16px] font-semibold text-white'>
                  Streaming no disponible en Red Corporativa
                </h3>
                <p className='mt-2 text-[12px] leading-relaxed text-[#8e8e93]'>
                  La sesión actual utiliza el slice empresarial. Cambia al APN Internet para reproducir contenido multimedia con QoS 5G.
                </p>
                <button
                  onClick={() => run('apn', { apn: 'internet' })}
                  disabled={busy}
                  className='mt-5 inline-flex items-center gap-2 rounded-2xl bg-[#0a84ff] px-5 py-3 text-[13px] font-semibold text-white hover:bg-[#0071e3] transition shadow-md active:scale-98 disabled:opacity-40 cursor-pointer'
                >
                  <Globe className='size-4' /> Conectar a APN Internet
                </button>
              </div>
            ) : (activeApp === 'stream' || isStreamingActive) ? (
              <VideoLab
                connected={cellularData && Boolean(activeSession) && Boolean(data?.registered) && !offline}
                exhausted={remaining === 0}
                afBoost={data?.af_boost}
                onToggleAfBoost={(enable) => afBoostMutation.mutateAsync(enable)}
                isAfBoostPending={afBoostMutation.isPending}
                selectedImsi={selectedImsi}
                isPip={activeApp !== 'stream'}
                onOpenStream={() => setActiveApp('stream')}
                onOpenBalance={() => setActiveApp('balance')}
                onActiveChange={setIsStreamingActive}
              />
            ) : null}
          </div>

          {/* APP 2: Mi 5G (Carrier Self-Care / Data Balance / Apple Wallet Style) */}
          {activeApp === 'balance' && (
            <div className='flex flex-col gap-3 animate-in fade-in duration-150'>
              {/* iOS Navigation Header */}
              <div className='flex items-center justify-between py-1'>
                <button
                  type='button'
                  onClick={() => setActiveApp('home')}
                  className='flex items-center text-[14px] font-medium text-[#0a84ff] hover:opacity-80 transition -ml-1 cursor-pointer'
                >
                  <ChevronLeft className='size-5 mr-0.5' />
                  Inicio
                </button>
                <div className='flex items-center gap-1.5'>
                  <span className='text-[15px] font-semibold text-white'>Mi 5G</span>
                </div>
                <span className='text-[10px] font-medium text-[#30d158] bg-[#30d158]/15 px-2 py-0.5 rounded-full border border-[#30d158]/20'>
                  CHF Online
                </span>
              </div>

              {/* Apple Card Hero Container */}
              <div className='relative overflow-hidden rounded-3xl bg-[#1c1c1e] p-5 ring-1 ring-white/5 shadow-xl'>
                <div className='flex items-center justify-between'>
                  <div className='flex items-center gap-1.5'>
                    <span className='size-2 rounded-full bg-[#30d158] animate-pulse' />
                    <span className='text-[11px] font-medium tracking-wider text-[#8e8e93] uppercase'>
                      Bolsa de Datos 5G SA
                    </span>
                  </div>
                  <span className='rounded-full bg-[#2c2c2e] px-2 py-0.5 font-mono text-[10px] text-[#8e8e93]'>
                    {data?.subscriber ? data.subscriber.replace('imsi-', '') : 'SIM activa'}
                  </span>
                </div>

                {/* Circular Data Usage Ring (Apple Activity Style) */}
                <div className='my-5 flex flex-col items-center justify-center'>
                  <div className='relative flex size-36 items-center justify-center'>
                    <svg className='size-full -rotate-90' viewBox='0 0 100 100'>
                      <circle
                        cx='50'
                        cy='50'
                        r='42'
                        className='stroke-[#2c2c2e]'
                        strokeWidth='8'
                        fill='transparent'
                      />
                      <circle
                        cx='50'
                        cy='50'
                        r='42'
                        className={cn(
                          'transition-all duration-700 ease-out',
                          remaining === 0 ? 'stroke-rose-500' : 'stroke-[#30d158]'
                        )}
                        strokeWidth='8'
                        strokeDasharray='264'
                        strokeDashoffset={264 - (264 * percent) / 100}
                        strokeLinecap='round'
                        fill='transparent'
                      />
                    </svg>
                    <div className='absolute flex flex-col items-center justify-center text-center'>
                      <span className='text-3xl font-bold tracking-tight text-white'>
                        {remaining === null ? '—' : mb(remaining)}
                      </span>
                      <span className='text-[10px] font-medium text-[#8e8e93] uppercase tracking-wider'>
                        MB Libres
                      </span>
                    </div>
                  </div>
                </div>

                {/* Apple Wallet Style Recharge Button */}
                <button
                  disabled={busy || !balance}
                  onClick={() => {
                    pendingTopup.current ??= crypto.randomUUID()
                    run('topup', { request_id: pendingTopup.current })
                  }}
                  className='flex w-full items-center justify-center gap-2 rounded-2xl bg-[#30d158] hover:bg-[#28cd41] py-3 text-[13.5px] font-semibold text-black shadow-md transition-all active:scale-98 disabled:opacity-40 cursor-pointer'
                >
                  <CreditCard className='size-4' />
                  Recargar Paquete (+50 MB)
                </button>
              </div>

              {/* Inset Grouped Section for Breakdown */}
              <div className='flex flex-col gap-1.5'>
                <div className='px-1 text-[11px] font-medium tracking-wider text-[#8e8e93] uppercase'>
                  Detalle de Tarificación (3GPP N40)
                </div>

                <div className='overflow-hidden rounded-2xl bg-[#1c1c1e] divide-y divide-[#2c2c2e] ring-1 ring-white/5'>
                  <div className='flex items-center justify-between px-3.5 py-3 text-[13px]'>
                    <span className='text-[#8e8e93]'>Bolsa Contratada</span>
                    <span className='font-semibold text-white'>
                      {balance ? `${mb(balance.quota_bytes)} MB` : '—'}
                    </span>
                  </div>
                  <div className='flex items-center justify-between px-3.5 py-3 text-[13px]'>
                    <span className='text-[#8e8e93]'>Consumo Debitado</span>
                    <span className='font-semibold text-[#30d158]'>
                      {balance ? `${mb(balance.consumed_bytes)} MB` : '—'}
                    </span>
                  </div>
                  <div className='flex items-center justify-between px-3.5 py-3 text-[13px]'>
                    <span className='text-[#8e8e93]'>Reserva Activa UPF (N4)</span>
                    <span className='font-semibold text-white'>
                      {balance ? `${mb(balance.reserved_bytes)} MB` : '—'}
                    </span>
                  </div>
                  <div className='flex items-center justify-between px-3.5 py-3 text-[13px]'>
                    <span className='text-[#8e8e93]'>Función de Red CHF</span>
                    <span className='font-medium text-[#0a84ff]'>Open5GS Converged</span>
                  </div>
                </div>
              </div>

              {/* iOS Style Footnote */}
              <p className='px-2 text-[11px] text-[#8e8e93] text-center leading-relaxed'>
                Tarificación convergente 3GPP N40 en tiempo real con Open5GS CHF. Cada flujo de datos y segmento HLS descuenta cuota del saldo activo.
              </p>
            </div>
          )}

          {/* APP 1: Ajustes (iOS Style System Settings) */}
          {activeApp === 'settings' && (
            <div className='flex flex-col gap-3 pb-2'>
              {/* SUBVIEW: APN SELECTOR */}
              {settingsView === 'apn' && (
                <div className='flex flex-col gap-3 animate-in fade-in slide-in-from-right-4 duration-150'>
                  {/* iOS Navigation Header */}
                  <div className='flex items-center justify-between py-1'>
                    <button
                      type='button'
                      onClick={() => setSettingsView('root')}
                      className='flex items-center text-[14px] font-medium text-[#0a84ff] hover:opacity-80 transition -ml-1'
                    >
                      <ChevronLeft className='size-5 mr-0.5' />
                      Configuración
                    </button>
                    <span className='text-[15px] font-semibold text-white'>Punto de acceso</span>
                    <div className='w-14' />
                  </div>

                  {/* Section Title */}
                  <div className='px-3 pt-1 text-[11px] font-medium tracking-wider text-[#8e8e93] uppercase'>
                    Redes de Datos Disponibles (DNN)
                  </div>

                  {/* Options List */}
                  <div className='overflow-hidden rounded-2xl bg-[#1c1c1e] divide-y divide-[#2c2c2e] ring-1 ring-white/5'>
                    {/* Option internet */}
                    <button
                      type='button'
                      disabled={busy}
                      onClick={() => run('apn', { apn: 'internet' })}
                      className='w-full flex items-center justify-between px-3.5 py-3 text-left transition-colors active:bg-[#2c2c2e]/60'
                    >
                      <div>
                        <div className='text-[14px] font-medium text-white'>Smartphone eMBB · internet</div>
                        <div className='text-[11px] text-[#8e8e93] mt-0.5'>
                          Acceso público general a Internet y streaming. Slice SST 1 · SD 000001.
                        </div>
                      </div>
                      {activeApn === 'internet' && (
                        <Check className='size-5 text-[#0a84ff] shrink-0 ml-2' />
                      )}
                    </button>

                  </div>

                  <div className='px-3 pt-1 text-[11px] text-[#8e8e93] leading-relaxed'>
                    El APN (Access Point Name / DNN) indica a la función SMF del core 5G qué plano de
                    usuario (UPF) y rebanada de red (S-NSSAI) deben asociarse a la sesión PDU del smartphone.
                  </div>
                </div>
              )}

              {/* SUBVIEW: QOS (5QI) SELECTOR */}
              {settingsView === 'qos' && (
                <div className='flex flex-col gap-3 animate-in fade-in slide-in-from-right-4 duration-150'>
                  {/* iOS Navigation Header */}
                  <div className='flex items-center justify-between py-1'>
                    <button
                      type='button'
                      onClick={() => setSettingsView('root')}
                      className='flex items-center text-[14px] font-medium text-[#0a84ff] hover:opacity-80 transition -ml-1'
                    >
                      <ChevronLeft className='size-5 mr-0.5' />
                      Configuración
                    </button>
                    <span className='text-[15px] font-semibold text-white'>Calidad de servicio</span>
                    <div className='w-14' />
                  </div>

                  {/* Section Title */}
                  <div className='px-3 pt-1 text-[11px] font-medium tracking-wider text-[#8e8e93] uppercase'>
                    Perfiles 3GPP QoS (5QI)
                  </div>

                  {/* Options List */}
                  <div className='overflow-hidden rounded-2xl bg-[#1c1c1e] divide-y divide-[#2c2c2e] ring-1 ring-white/5'>
                    {/* Option 5QI=9 */}
                    <button
                      type='button'
                      disabled={busy || afBoostMutation.isPending}
                      onClick={() => {
                        if (isBoostActive) afBoostMutation.mutate(false)
                      }}
                      className='w-full flex items-center justify-between px-3.5 py-3 text-left transition-colors active:bg-[#2c2c2e]/60'
                    >
                      <div>
                        <div className='text-[14px] font-medium text-white'>
                          5QI=9 · Tráfico Estándar
                        </div>
                        <div className='text-[11px] text-[#8e8e93] mt-0.5'>
                          Prioridad por defecto Best-Effort. Sin reserva de ancho de banda garantizado.
                        </div>
                      </div>
                      {!isBoostActive && (
                        <Check className='size-5 text-[#0a84ff] shrink-0 ml-2' />
                      )}
                    </button>

                    {/* Option 5QI=2 */}
                    <button
                      type='button'
                      disabled={busy || afBoostMutation.isPending}
                      onClick={() => {
                        if (!isBoostActive) afBoostMutation.mutate(true)
                      }}
                      className='w-full flex items-center justify-between px-3.5 py-3 text-left transition-colors active:bg-[#2c2c2e]/60'
                    >
                      <div>
                        <div className='flex items-center gap-1.5'>
                          <span className='text-[14px] font-medium text-white'>
                            5QI=2 · 5G+ Turbo (QoS Boost)
                          </span>
                          <span className='rounded bg-amber-400/20 px-1 py-0.2 text-[9px] font-bold text-amber-300'>
                            TURBO
                          </span>
                        </div>
                        <div className='text-[11px] text-[#8e8e93] mt-0.5'>
                          Flujo GBR de alta prioridad (GBR 10 Mbps / MBR 20 Mbps) autorizado dinámicamente vía N5 en PCF.
                        </div>
                      </div>
                      {isBoostActive && (
                        <Check className='size-5 text-[#0a84ff] shrink-0 ml-2' />
                      )}
                    </button>
                  </div>

                  <div className='px-3 pt-1 text-[11px] text-[#8e8e93] leading-relaxed'>
                    La función de aplicación (AF) utiliza el estándar 3GPP Release 16 (interfaz N5 basada
                    en HTTP/2 SBI) para solicitar a la PCF una regla PCC dinámica con QoS dedicado.
                    La SMF actualiza el túnel PFCP en el UPF en tiempo real.
                  </div>
                </div>
              )}

              {/* SUBVIEW: N6 DIAGNOSTIC */}
              {settingsView === 'diagnostic' && (
                <div className='flex flex-col gap-3 animate-in fade-in slide-in-from-right-4 duration-150'>
                  {/* iOS Navigation Header */}
                  <div className='flex items-center justify-between py-1'>
                    <button
                      type='button'
                      onClick={() => setSettingsView('root')}
                      className='flex items-center text-[14px] font-medium text-[#0a84ff] hover:opacity-80 transition -ml-1'
                    >
                      <ChevronLeft className='size-5 mr-0.5' />
                      Configuración
                    </button>
                    <span className='text-[15px] font-semibold text-white'>Diagnóstico N6</span>
                    <div className='w-14' />
                  </div>

                  {/* Clarifying What N6 is in pure human language */}
                  <div className='rounded-2xl bg-[#1c1c1e] p-3 text-[11px] text-[#8e8e93] ring-1 ring-white/5 leading-relaxed'>
                    <span className='font-semibold text-white'>¿Qué es el enlace N6? </span>
                    En la arquitectura 3GPP 5G, el punto de referencia <strong className='text-teal-300 font-medium'>N6</strong> conecta
                    el plano de usuario (<strong className='text-slate-200'>UPF</strong>) con las redes externas de datos (<strong className='text-slate-200'>DN / Internet</strong>).
                    Esta prueba envía una solicitud HTTP a través de la interfaz del teléfono para comprobar que los paquetes IP atraviesan el túnel GTP-U hacia el exterior de forma real.
                  </div>

                  {/* PDU Session Parameters */}
                  <div className='px-3 pt-1 text-[11px] font-medium tracking-wider text-[#8e8e93] uppercase'>
                    Parámetros de la Sesión PDU
                  </div>
                  <div className='overflow-hidden rounded-2xl bg-[#1c1c1e] divide-y divide-[#2c2c2e] ring-1 ring-white/5'>
                    <IosRow
                      icon={<Smartphone className='size-4' />}
                      iconBg='#5856d6'
                      label='Dirección IP del UE'
                      value={activeSession?.address ?? 'Sin sesión activa'}
                    />
                    <IosRow
                      icon={<Radio className='size-4' />}
                      iconBg='#34c759'
                      label='Interfaz de red UE'
                      value={activeSession?.interface ?? 'uesimtun0'}
                    />
                    <IosRow
                      icon={<Layers className='size-4' />}
                      iconBg='#af52de'
                      label='Rebanada (S-NSSAI)'
                      value={`SST ${activeSession?.snssai?.sst ?? 1} · SD ${activeSession?.snssai?.sd == null ? '000001' : activeSession.snssai.sd.toString(16).padStart(6, '0')}`}
                    />
                    <IosRow
                      icon={<Globe className='size-4' />}
                      iconBg='#007aff'
                      label='Punto de acceso activo'
                      value={activeApn}
                    />
                  </div>

                  {/* Connectivity Test */}
                  <div className='px-3 pt-1 text-[11px] font-medium tracking-wider text-[#8e8e93] uppercase'>
                    Comprobación de Conectividad Externa
                  </div>
                  <div className='overflow-hidden rounded-2xl bg-[#1c1c1e] p-3 ring-1 ring-white/5'>
                    <button
                      type='button'
                      disabled={busy || !activeSession || activeApn !== 'internet'}
                      onClick={() => {
                        setProbe(null)
                        run('traffic/n6-probe')
                      }}
                      className='w-full flex items-center justify-center gap-2 rounded-xl bg-[#0a84ff] py-2.5 text-xs font-semibold text-white transition hover:bg-[#0071e3] active:scale-98 disabled:opacity-40 shadow-sm'
                    >
                      {command.isPending && command.variables?.path === 'traffic/n6-probe' ? (
                        <>
                          <Loader2 className='size-3.5 animate-spin' />
                          <span>Comprobando enlace N6...</span>
                        </>
                      ) : (
                        <>
                          <Activity className='size-3.5' />
                          <span>Ejecutar comprobación de enlace N6</span>
                        </>
                      )}
                    </button>

                    {probe && (
                      <div className='mt-3 space-y-1.5 rounded-xl bg-black/40 p-2.5 text-[11px] text-slate-300'>
                        <div className='flex justify-between'>
                          <span className='text-[#8e8e93]'>Resultado:</span>
                          <span className={probe.completed ? 'text-emerald-400 font-semibold' : 'text-rose-400 font-semibold'}>
                            {probe.completed ? '✓ Conectividad confirmada (256 KB)' : 'Interrumpido'}
                          </span>
                        </div>
                        <div className='flex justify-between'>
                          <span className='text-[#8e8e93]'>Latencia de enlace:</span>
                          <span>{probe.duration_seconds.toFixed(2)} s</span>
                        </div>
                        <div className='flex justify-between'>
                          <span className='text-[#8e8e93]'>Código de respuesta:</span>
                          <span className='font-mono text-teal-300'>{probe.http_status} OK</span>
                        </div>
                        <div className='flex justify-between'>
                          <span className='text-[#8e8e93]'>Ruta 3GPP:</span>
                          <span className='text-slate-300 font-mono text-[10px]'>uesimtun0 → UPF ogstun → DN N6</span>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* ROOT VIEW: MAIN SETTINGS */}
              {settingsView === 'root' && (
                <div className='flex flex-col gap-3 animate-in fade-in duration-150'>
                  {/* iOS Large Title Header */}
                  <div className='px-1 pt-1'>
                    <h2 className='text-2xl font-bold tracking-tight text-white'>Configuración</h2>
                  </div>

                  {/* iOS Search Bar */}
                  <div className='px-0.5'>
                    <div className='flex items-center gap-2 rounded-xl bg-[#1c1c1e] px-3 py-1.5 text-xs text-[#8e8e93] ring-1 ring-white/5'>
                      <Search className='size-3.5' />
                      <span>Buscar</span>
                    </div>
                  </div>

                  {/* Apple ID Style Profile Row */}
                  <div className='flex items-center gap-3.5 rounded-2xl bg-[#1c1c1e] p-3 shadow-sm ring-1 ring-white/5'>
                    <div className='flex size-12 items-center justify-center rounded-full bg-[#3a3a3c] font-semibold text-sm text-white shadow-md shrink-0 border border-white/10'>
                      5G
                    </div>
                    <div className='flex-1 min-w-0'>
                      <div className='text-[15px] font-semibold text-white truncate'>
                        {data?.subscriber ? `SIM: ${data.subscriber.replace('imsi-', '')}` : 'Terminal 5G SA'}
                      </div>
                      <div className='text-[12px] text-[#8e8e93] truncate'>
                        ID de Apple, iCloud y más
                      </div>
                    </div>
                    <ChevronRight className='size-4 text-[#8e8e93]/60 shrink-0 mr-0.5' />
                  </div>

                  {/* iOS Group 1: Conectividad Primaria */}
                  <div>
                    <div className='px-3 pb-1 text-[11px] font-medium tracking-wider text-[#8e8e93] uppercase'>
                      Conectividad y Red
                    </div>
                    <div className='overflow-hidden rounded-2xl bg-[#1c1c1e] divide-y divide-[#2c2c2e] ring-1 ring-white/5'>
                      <IosRow
                        icon={<Plane className='size-4' />}
                        iconBg='#ff9500'
                        label='Modo de vuelo'
                        rightElement={
                          <IosSwitch
                            label='Modo de vuelo'
                            color='#ff9500'
                            checked={Boolean(offline)}
                            disabled={busy}
                            onCheckedChange={(val) => run('airplane-mode', { enabled: val })}
                          />
                        }
                      />
                      <IosRow
                        icon={<Radio className='size-4' />}
                        iconBg='#34c759'
                        label='Datos celulares'
                        value={
                          offline
                            ? 'Desactivados'
                            : !cellularData
                              ? 'Desactivados'
                              : data?.registered
                                ? 'Activados'
                                : 'Buscando red'
                        }
                        rightElement={
                          <IosSwitch
                            label='Datos celulares'
                            color='#34c759'
                            checked={cellularData && !offline}
                            disabled={busy || offline}
                            onCheckedChange={(val) => {
                              setCellularData(val)
                              triggerIslandAlert(
                                val ? 'Datos celulares activados (5G)' : 'Datos celulares desactivados'
                              )
                            }}
                          />
                        }
                      />
                      <IosRow
                        icon={<Globe className='size-4' />}
                        iconBg='#007aff'
                        label='Punto de acceso (APN)'
                        value={activeApn}
                        hasChevron
                        onClick={() => setSettingsView('apn')}
                      />
                      <IosRow
                        icon={<Zap className='size-4' />}
                        iconBg='#f59e0b'
                        label='Voz y datos (QoS)'
                        value={isBoostActive ? '5QI=2 (Turbo)' : '5QI=9 (Estándar)'}
                        hasChevron
                        onClick={() => setSettingsView('qos')}
                      />
                    </div>
                  </div>

                  {/* iOS Group 2: Servicios y Aplicaciones */}
                  <div>
                    <div className='px-3 pb-1 text-[11px] font-medium tracking-wider text-[#8e8e93] uppercase'>
                      Servicios y Datos
                    </div>
                    <div className='overflow-hidden rounded-2xl bg-[#1c1c1e] divide-y divide-[#2c2c2e] ring-1 ring-white/5'>
                      <IosRow
                        icon={<CreditCard className='size-4' />}
                        iconBg='#30d158'
                        label='Saldo y recargas (CHF)'
                        value={remaining != null ? `${mb(remaining)} MB` : '—'}
                        hasChevron
                        onClick={() => setActiveApp('balance')}
                      />
                      <IosRow
                        icon={<Film className='size-4' />}
                        iconBg='#bf5af2'
                        label='Stream5G (Video Lab)'
                        value='4K HLS'
                        hasChevron
                        onClick={() => setActiveApp('stream')}
                      />
                    </div>
                  </div>

                  {/* iOS Group 3: Información y Diagnóstico */}
                  <div>
                    <div className='px-3 pb-1 text-[11px] font-medium tracking-wider text-[#8e8e93] uppercase'>
                      Diagnóstico de Red
                    </div>
                    <div className='overflow-hidden rounded-2xl bg-[#1c1c1e] divide-y divide-[#2c2c2e] ring-1 ring-white/5'>
                      <IosRow
                        icon={<Smartphone className='size-4' />}
                        iconBg='#5856d6'
                        label='Dirección IP (PDU)'
                        value={activeSession?.address ?? 'Sin sesión'}
                      />
                      <IosRow
                        icon={<Activity className='size-4' />}
                        iconBg='#32ade6'
                        label='Diagnóstico de enlace N6'
                        subtitle='Comprueba conectividad UPF'
                        value={probe ? (probe.completed ? 'Enlace OK' : 'Fallo') : 'Verificar'}
                        hasChevron
                        onClick={() => setSettingsView('diagnostic')}
                      />
                    </div>
                  </div>

                  {/* iOS Footer Note */}
                  <div className='flex items-center justify-between px-3 pt-0.5 text-[11px] text-[#8e8e93]'>
                    <span>Core Open5GS SA Release 16</span>
                    <button
                      onClick={() => void status.refetch()}
                      disabled={status.isFetching}
                      className='hover:text-white transition flex items-center gap-1 text-[11px]'
                    >
                      <RefreshCw className={cn('size-3', status.isFetching && 'animate-spin')} />
                      Actualizar
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* APP: Teléfono (iOS Dialer, Keypad, USSD *1#, *99#, MMI *#06#, Field Test *3001#12345#*) */}
          {activeApp === 'phone' && (
            <div className='flex-1 h-full min-h-0 flex flex-col'>
              <PhoneApp
                subscriber={data?.subscriber ?? selectedImsi}
                registered={data?.registered ?? true}
                ipAddress={activeSession?.address}
                activeApn={activeApn}
                initialNumber={initialDialCode ?? undefined}
                balance={data?.balance}
                afBoost={data?.af_boost}
                traffic={{
                  rx_bytes: activeSession?.rx_bytes ?? data?.interfaces?.[0]?.rx_bytes,
                  tx_bytes: activeSession?.tx_bytes ?? data?.interfaces?.[0]?.tx_bytes,
                }}
                snssai={activeSession?.snssai}
                cellInfo={{
                  cellId: (data?.native_state as Record<string, unknown> | undefined)?.[
                    'current-cell'
                  ] as string | number | undefined,
                  tac: (data?.native_state as Record<string, unknown> | undefined)?.[
                    'current-tac'
                  ] as string | number | undefined,
                }}
                onClose={() => {
                  setActiveApp('home')
                  setInitialDialCode(null)
                }}
                onTriggerIslandAlert={triggerIslandAlert}
              />
            </div>
          )}

          {/* APP: Notas (iOS Notes App with 5G MMI/USSD Directory) */}
          {activeApp === 'notes' && (
            <div className='flex-1 h-full min-h-0 flex flex-col'>
              <NotesApp
                onClose={() => setActiveApp('home')}
                onOpenPhone={(code) => {
                  setInitialDialCode(code ?? null)
                  setActiveApp('phone')
                }}
                onTriggerIslandAlert={triggerIslandAlert}
                imsi={selectedImsi.replace('imsi-', '')}
              />
            </div>
          )}

          {/* APP: Safari (iOS 17/18 Safari Browser with 5G Speedtest & Intranet) */}
          {activeApp === 'safari' && (
            <div className='flex-1 h-full min-h-0 flex flex-col'>
              <SafariApp
                onClose={() => setActiveApp('home')}
                onTriggerIslandAlert={triggerIslandAlert}
                imsi={selectedImsi.replace('imsi-', '')}
                ipAddress={activeSession?.address ?? 'Sin IP observada'}
                activeApn={activeApn}
                afBoost={data?.af_boost}
                snssai={activeSession?.snssai}
              />
            </div>
          )}

          {/* Inline Action Notice */}
          {notice && (
            <div className='mt-3 flex items-center justify-between rounded-xl bg-slate-900 border border-teal-500/20 px-3 py-2 text-xs text-teal-200'>
              <span>{notice}</span>
              <button onClick={() => setNotice('')} className='text-slate-400 hover:text-white'>
                <X className='size-3' />
              </button>
            </div>
          )}
        </main>

        {/* BOTTOM HOME INDICATOR (Authentic iOS Home Bar) */}
        <div className='flex justify-center bg-black pb-2 pt-1.5 shrink-0'>
          <button
            type='button'
            onClick={() => {
              setActiveApp('home')
              setSettingsView('root')
            }}
            className='group flex h-4 w-36 items-center justify-center cursor-pointer transition'
            title='Ir a la pantalla de inicio (Home)'
            aria-label='Ir a la pantalla de inicio'
          >
            <span className='h-1 w-28 rounded-full bg-white/40 transition-all duration-200 group-hover:w-32 group-hover:bg-white/80 group-active:scale-95' />
          </button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
