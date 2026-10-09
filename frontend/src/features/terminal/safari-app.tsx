import { useState, useRef } from 'react'
import {
  ChevronLeft,
  ChevronRight,
  RotateCcw,
  Share2,
  BookOpen,
  Lock,
  Search,
  X,
  Zap,
  ArrowDown,
  ArrowUp,
  Globe,
  Building2,
  GraduationCap,
  Radio,
  Sparkles,
  CheckCircle2,
  Shield,
} from 'lucide-react'
import { api, apiErrorMessage } from '@/lib/api'
import { toast } from 'sonner'
import { cn } from '@/lib/utils'

interface SpeedtestResult {
  status: string
  timestamp: string
  client_ip: string
  server: string
  server_ip: string
  interface: string
  apn: string
  snssai: { sst: number; sd: string | number }
  qos: string
  qos_level: number | null
  is_boosted: boolean
  pcc_rule: string
  ping_ms: number | null
  jitter_ms: number | null
  download_mbps: number | null
  upload_mbps: number | null
  bytes_downloaded: number
  bytes_uploaded: number | null
  note: string
}

interface SafariAppProps {
  onClose: () => void
  onTriggerIslandAlert?: (msg: string) => void
  imsi: string
  ipAddress?: string
  activeApn?: string
  afBoost?: {
    active: boolean
    qos: string
    mbr_dl?: string
    mbr_ul?: string
    pcc_rule?: string | null
    session_url?: string | null
  }
  snssai?: {
    sst: number
    sd: number | null
  } | null
}

type WebPage = 'start' | 'speedtest' | 'intranet' | 'pucp' | 'open5gs'

export function SafariApp({
  onClose,
  onTriggerIslandAlert,
  imsi,
  ipAddress = 'Sin IP observada',
  activeApn = 'internet',
  afBoost,
  snssai,
}: SafariAppProps) {
  const [currentPage, setCurrentPage] = useState<WebPage>('start')
  const [pageHistory, setPageHistory] = useState<WebPage[]>(['start'])
  const [historyIndex, setHistoryIndex] = useState(0)

  // URL / Search Bar State
  const [urlInput, setUrlInput] = useState('')
  const [isEditingUrl, setIsEditingUrl] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [loadProgress, setLoadProgress] = useState(0)
  const urlInputRef = useRef<HTMLInputElement>(null)

  // Speedtest State
  const [testPhase, setTestPhase] = useState<'idle' | 'ping' | 'download' | 'upload' | 'completed'>('idle')
  const [currentGaugeValue, setCurrentGaugeValue] = useState(0)
  const [livePing, setLivePing] = useState<number | null>(null)
  const [liveJitter, setLiveJitter] = useState<number | null>(null)
  const [liveDownload, setLiveDownload] = useState<number | null>(null)
  const [liveUpload, setLiveUpload] = useState<number | null>(null)
  const [testResult, setTestResult] = useState<SpeedtestResult | null>(null)

  const cleanImsi = imsi.replace('imsi-', '')

  // Navigate to a specific page
  const navigateTo = (page: WebPage, customUrl?: string) => {
    setIsEditingUrl(false)
    setIsLoading(true)
    setLoadProgress(15)

    const timer1 = setTimeout(() => setLoadProgress(65), 180)
    const timer2 = setTimeout(() => {
      setLoadProgress(100)
      setCurrentPage(page)
      setUrlInput(customUrl || getUrlForPage(page))
      const newHistory = [...pageHistory.slice(0, historyIndex + 1), page]
      setPageHistory(newHistory)
      setHistoryIndex(newHistory.length - 1)
      setIsLoading(false)
    }, 380)

    return () => {
      clearTimeout(timer1)
      clearTimeout(timer2)
    }
  }

  const getUrlForPage = (page: WebPage): string => {
    switch (page) {
      case 'start':
        return ''
      case 'speedtest':
        return 'speedtest.5g.pucp'
      case 'intranet':
        return 'intranet.empresa.5g:8080'
      case 'pucp':
        return 'campus.pucp.edu.pe'
      case 'open5gs':
        return 'open5gs.org'
    }
  }

  const handleBack = () => {
    if (historyIndex > 0) {
      const prevIndex = historyIndex - 1
      const prevPage = pageHistory[prevIndex]
      setHistoryIndex(prevIndex)
      setCurrentPage(prevPage)
      setUrlInput(getUrlForPage(prevPage))
    }
  }

  const handleForward = () => {
    if (historyIndex < pageHistory.length - 1) {
      const nextIndex = historyIndex + 1
      const nextPage = pageHistory[nextIndex]
      setHistoryIndex(nextIndex)
      setCurrentPage(nextPage)
      setUrlInput(getUrlForPage(nextPage))
    }
  }

  const handleUrlSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    const query = urlInput.trim().toLowerCase()
    if (!query) return

    if (query.includes('speed') || query.includes('fast') || query.includes('test') || query.includes('velocidad')) {
      navigateTo('speedtest', 'speedtest.5g.pucp')
    } else if (query.includes('intra') || query.includes('corp') || query.includes('empresa')) {
      navigateTo('intranet', 'intranet.empresa.5g:8080')
    } else if (query.includes('pucp') || query.includes('campus')) {
      navigateTo('pucp', 'campus.pucp.edu.pe')
    } else if (query.includes('open5gs') || query.includes('core')) {
      navigateTo('open5gs', 'open5gs.org')
    } else {
      // Default to speedtest if they typed a search like "speed"
      navigateTo('speedtest', `speedtest.5g.pucp?q=${encodeURIComponent(query)}`)
    }
  }

  // Speedtest Runner
  const runSpeedtest = async () => {
    setTestPhase('ping')
    setCurrentGaugeValue(0)
    setLivePing(null)
    setLiveJitter(null)
    setLiveDownload(null)
    setLiveUpload(null)
    setTestResult(null)

    onTriggerIslandAlert?.('⚡ Speedtest 5G: Conectando con UPF-01…')

    try {
      setTestPhase('download')
      const { data } = await api.post<SpeedtestResult>('/terminal/speedtest', { imsi: `imsi-${cleanImsi}` })
      setCurrentGaugeValue(data.download_mbps ?? 0)
      setLiveDownload(data.download_mbps)
      setLiveUpload(data.upload_mbps)
      setLivePing(data.ping_ms)
      setLiveJitter(data.jitter_ms)
      setTestResult(data)
      setTestPhase('completed')
      onTriggerIslandAlert?.(data.note)
    } catch (err) {
      toast.error(apiErrorMessage(err, 'Error al ejecutar test de velocidad'))
      setTestPhase('idle')
    }
  }

  // Calculate gauge angle for speedometer (dynamically scaled to provisioned MBR)
  const maxDisplaySpeed = Math.max(
    30,
    Math.ceil(
      Math.max(
        currentGaugeValue,
        testResult?.download_mbps ?? 0,
        afBoost?.active && afBoost.mbr_dl ? parseFloat(afBoost.mbr_dl) : 0
      ) * 1.25 / 10
    ) * 10
  )
  const normalizedSpeed = Math.min(maxDisplaySpeed, Math.max(0, currentGaugeValue))
  const gaugeAngle = -120 + (normalizedSpeed / maxDisplaySpeed) * 240

  return (
    <div className='flex h-full flex-col bg-black text-white select-none relative animate-in fade-in duration-200'>
      
      {/* ================= 1. SAFARI TOP BAR (PROGRESS & DOMAIN STATUS) ================= */}
      <div className='relative z-20 shrink-0 bg-black pt-2 pb-1.5 px-3 flex items-center justify-between'>
        <div className='flex items-center gap-1.5 text-xs text-white/80'>
          <Lock className='size-3 text-emerald-400' />
          <span className='font-medium text-[11px] truncate max-w-[190px]'>
            {currentPage === 'start' ? 'Página de inicio' : getUrlForPage(currentPage)}
          </span>
        </div>
        <div className='flex items-center gap-2'>
          <button
            type='button'
            onClick={() => {
              void navigator.clipboard.writeText(getUrlForPage(currentPage))
              onTriggerIslandAlert?.('✓ Enlace copiado en Safari')
            }}
            className='text-white/60 hover:text-white transition-colors'
            title='Compartir enlace'
          >
            <Share2 className='size-3.5' />
          </button>
          <button
            type='button'
            onClick={() => {
              if (currentPage === 'speedtest') void runSpeedtest()
              else navigateTo(currentPage)
            }}
            className='text-white/60 hover:text-white transition-colors'
            title='Recargar'
          >
            <RotateCcw className='size-3.5' />
          </button>
          <button
            type='button'
            onClick={onClose}
            className='text-xs font-semibold text-[#0a84ff] hover:text-[#0077ed] transition-colors ml-1 px-1 py-0.5 cursor-pointer'
            title='Salir de Safari'
          >
            Listo
          </button>
        </div>

        {/* Safari Top Loading Progress Bar */}
        {isLoading && (
          <div
            className='absolute bottom-0 left-0 h-[2px] bg-[#0a84ff] transition-all duration-200 ease-out'
            style={{ width: `${loadProgress}%` }}
          />
        )}
      </div>

      {/* ================= 2. WEB VIEW CONTAINER (BROWSER CANVAS) ================= */}
      <div className='flex-1 min-h-0 overflow-y-auto px-3 py-3 [&::-webkit-scrollbar]:hidden [scrollbar-width:none] [-ms-overflow-style:none]'>
        
        {/* VIEW A: SAFARI START PAGE (FAVORITES GRID) */}
        {currentPage === 'start' && (
          <div className='flex flex-col gap-5 pt-2 animate-in fade-in zoom-in-95 duration-200'>
            
            {/* Header / Search Greeting */}
            <div className='flex flex-col items-center text-center pt-2 pb-1'>
              <div className='flex size-14 items-center justify-center rounded-2xl bg-gradient-to-br from-[#0a84ff] to-[#0055b3] shadow-lg shadow-blue-500/20 mb-2'>
                <Globe className='size-7 text-white' />
              </div>
              <h2 className='text-lg font-semibold tracking-tight'>Safari 5G</h2>
              <p className='text-xs text-white/50 mt-0.5'>Navegación sobre túnel 5G SA · DNS 10.45.0.1</p>
            </div>

            {/* Favorites Section */}
            <div className='space-y-2'>
              <h3 className='text-xs font-semibold uppercase tracking-wider text-white/50 px-1'>
                Favoritos
              </h3>
              <div className='grid grid-cols-4 gap-2.5'>
                
                {/* 1. Speedtest 5G */}
                <button
                  type='button'
                  onClick={() => navigateTo('speedtest', 'speedtest.5g.pucp')}
                  className='flex flex-col items-center gap-1.5 p-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/5 transition-all group'
                >
                  <div className='flex size-11 items-center justify-center rounded-xl bg-gradient-to-tr from-amber-500 to-rose-500 shadow-md group-hover:scale-105 transition-transform'>
                    <Zap className='size-5 text-white' />
                  </div>
                  <span className='text-[10px] font-medium text-white/90 text-center leading-tight truncate w-full'>
                    Speedtest 5G
                  </span>
                </button>

                {/* 2. Intranet 5G */}
                <button
                  type='button'
                  onClick={() => navigateTo('intranet', 'intranet.empresa.5g:8080')}
                  className='flex flex-col items-center gap-1.5 p-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/5 transition-all group'
                >
                  <div className='flex size-11 items-center justify-center rounded-xl bg-gradient-to-tr from-blue-600 to-cyan-500 shadow-md group-hover:scale-105 transition-transform'>
                    <Building2 className='size-5 text-white' />
                  </div>
                  <span className='text-[10px] font-medium text-white/90 text-center leading-tight truncate w-full'>
                    Intranet 5G
                  </span>
                </button>

                {/* 3. Campus PUCP */}
                <button
                  type='button'
                  onClick={() => navigateTo('pucp', 'campus.pucp.edu.pe')}
                  className='flex flex-col items-center gap-1.5 p-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/5 transition-all group'
                >
                  <div className='flex size-11 items-center justify-center rounded-xl bg-gradient-to-tr from-indigo-600 to-blue-700 shadow-md group-hover:scale-105 transition-transform'>
                    <GraduationCap className='size-5 text-white' />
                  </div>
                  <span className='text-[10px] font-medium text-white/90 text-center leading-tight truncate w-full'>
                    Portal PUCP
                  </span>
                </button>

                {/* 4. Open5GS Core */}
                <button
                  type='button'
                  onClick={() => navigateTo('open5gs', 'open5gs.org')}
                  className='flex flex-col items-center gap-1.5 p-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/5 transition-all group'
                >
                  <div className='flex size-11 items-center justify-center rounded-xl bg-gradient-to-tr from-emerald-600 to-teal-500 shadow-md group-hover:scale-105 transition-transform'>
                    <Radio className='size-5 text-white' />
                  </div>
                  <span className='text-[10px] font-medium text-white/90 text-center leading-tight truncate w-full'>
                    Open5GS
                  </span>
                </button>
              </div>
            </div>

            {/* Privacy Report Section (Authentic iOS Safari Style) */}
            <div className='space-y-1.5'>
              <h3 className='text-xs font-semibold uppercase tracking-wider text-white/50 px-1'>
                Reporte de privacidad
              </h3>
              <div
                onClick={() =>
                  onTriggerIslandAlert?.(`Reporte de privacidad: IP ${ipAddress} · 0 rastreadores bloqueados`)
                }
                className='rounded-2xl bg-neutral-950 border border-white/10 p-3.5 flex items-center gap-3.5 cursor-pointer hover:bg-white/[0.03] transition-colors'
              >
                <div className='flex size-10 shrink-0 items-center justify-center rounded-xl bg-white/5 border border-white/10'>
                  <Shield className='size-5 text-white/80' />
                </div>
                <p className='text-xs text-white/80 leading-relaxed font-sans'>
                  En los últimos siete días, Safari evitó que 0 rastreadores crearan un perfil tuyo.
                </p>
              </div>
            </div>

            {/* Quick Search Chips */}
            <div className='space-y-1.5'>
              <h4 className='text-[11px] font-medium text-white/50 px-1'>Búsquedas frecuentes</h4>
              <div className='flex flex-wrap gap-1.5'>
                {['speedtest', 'test de velocidad', 'fast.com', 'intranet corporate'].map((tag) => (
                  <button
                    key={tag}
                    type='button'
                    onClick={() => {
                      setUrlInput(tag)
                      if (tag.includes('speed') || tag.includes('fast')) navigateTo('speedtest', 'speedtest.5g.pucp')
                      else navigateTo('intranet', 'intranet.empresa.5g:8080')
                    }}
                    className='rounded-full bg-white/5 hover:bg-white/10 px-3 py-1 text-[11px] text-white/80 border border-white/5 transition-colors flex items-center gap-1.5'
                  >
                    <Search className='size-3 text-white/40' />
                    <span>{tag}</span>
                  </button>
                ))}
              </div>
            </div>

          </div>
        )}

        {/* VIEW B: SPEEDTEST 5G WEB APPLICATION */}
        {currentPage === 'speedtest' && (
          <div className='flex flex-col items-center gap-4 py-1 animate-in fade-in duration-300'>
            
            {/* Speedtest Header */}
            <div className='w-full rounded-2xl bg-neutral-950 border border-white/10 p-3 flex items-center justify-between shadow-lg'>
              <div className='flex items-center gap-2'>
                <div className='flex size-8 items-center justify-center rounded-lg bg-amber-500/10 border border-amber-500/30 text-amber-400'>
                  <Zap className='size-4' />
                </div>
                <div>
                  <h3 className='text-xs font-bold text-white tracking-tight flex items-center gap-1.5'>
                    <span>SPEEDTEST 5G SA</span>
                    <span className='rounded bg-amber-500/20 px-1 py-0.2 text-[9px] font-mono text-amber-300'>
                      n78
                    </span>
                  </h3>
                  <p className='text-[10px] text-white/50 font-mono'>PUCP Telecom · Lima, PE</p>
                </div>
              </div>

              {/* QoS Badge */}
              <div
                className={cn(
                  'rounded-full px-2 py-0.5 text-[10px] font-semibold flex items-center gap-1 shadow-sm',
                  (afBoost?.active || (testResult?.qos_level != null && testResult.qos_level !== 9))
                    ? 'bg-gradient-to-r from-amber-500 to-amber-600 text-black font-bold animate-pulse'
                    : 'bg-white/10 text-white/70 border border-white/10'
                )}
              >
                <Sparkles className='size-2.5' />
                <span>
                  {testResult
                    ? (testResult.qos_level == null ? '5QI no observado' : `5G 5QI=${testResult.qos_level}`)
                    : (afBoost?.active ? `5G+ ${afBoost.qos}` : '5G 5QI=9')}
                </span>
              </div>
            </div>

            {/* Circular Speedometer SVG Gauge */}
            <div className='relative flex flex-col items-center justify-center my-2'>
              <svg className='w-56 h-40 overflow-visible' viewBox='0 0 200 130'>
                {/* Background Arc */}
                <path
                  d='M 20 120 A 80 80 0 0 1 180 120'
                  fill='none'
                  stroke='rgba(255,255,255,0.08)'
                  strokeWidth='14'
                  strokeLinecap='round'
                />
                {/* Active Glowing Arc */}
                <path
                  d='M 20 120 A 80 80 0 0 1 180 120'
                  fill='none'
                  stroke={(afBoost?.active || (testResult?.qos_level != null && testResult.qos_level !== 9)) ? '#f59e0b' : '#0a84ff'}
                  strokeWidth='14'
                  strokeLinecap='round'
                  strokeDasharray='251.2'
                  strokeDashoffset={251.2 - (251.2 * normalizedSpeed) / maxDisplaySpeed}
                  className='transition-all duration-150 ease-out filter drop-shadow-[0_0_8px_rgba(10,132,255,0.4)]'
                />

                {/* Needle */}
                <g
                  transform={`rotate(${gaugeAngle} 100 120)`}
                  className='transition-transform duration-100 ease-out origin-[100px_120px]'
                >
                  <polygon points='98,120 102,120 100,42' fill={(afBoost?.active || (testResult?.qos_level != null && testResult.qos_level !== 9)) ? '#fbbf24' : '#60a5fa'} />
                  <circle cx='100' cy='120' r='6' fill='#ffffff' />
                </g>
              </svg>

              {/* Digital Readout Center */}
              <div className='absolute bottom-2 flex flex-col items-center'>
                <div className='text-3xl font-extrabold font-mono tracking-tight text-white flex items-baseline gap-1'>
                  <span>{currentGaugeValue > 0 ? currentGaugeValue.toFixed(2) : '0.00'}</span>
                  <span className='text-xs font-sans font-medium text-white/50'>Mbps</span>
                </div>
                <span className='text-[10px] uppercase font-semibold tracking-wider text-white/40 mt-0.5'>
                  {testPhase === 'download'
                    ? 'Descargando N6…'
                    : testPhase === 'upload'
                      ? 'Subiendo N6…'
                      : testPhase === 'ping'
                        ? 'Verificando Ping…'
                        : testPhase === 'completed'
                          ? 'Test Completado'
                          : 'Listo para medir'}
                </span>
              </div>
            </div>

            {/* Real-Time Metrics Strip (Ping, Jitter, Download, Upload) */}
            <div className='grid grid-cols-4 gap-1.5 w-full'>
              <div className='rounded-xl bg-neutral-950 border border-white/10 p-2 flex flex-col items-center text-center'>
                <span className='text-[9px] font-medium text-white/40 uppercase'>Ping</span>
                <span className='font-mono text-xs font-bold text-white mt-0.5'>
                  {livePing != null ? `${livePing} ms` : '—'}
                </span>
              </div>
              <div className='rounded-xl bg-neutral-950 border border-white/10 p-2 flex flex-col items-center text-center'>
                <span className='text-[9px] font-medium text-white/40 uppercase'>Jitter</span>
                <span className='font-mono text-xs font-bold text-white mt-0.5'>
                  {liveJitter != null ? `${liveJitter} ms` : '—'}
                </span>
              </div>
              <div className='rounded-xl bg-neutral-950 border border-white/10 p-2 flex flex-col items-center text-center'>
                <span className='text-[9px] font-medium text-emerald-400 uppercase flex items-center gap-0.5'>
                  <ArrowDown className='size-2.5' /> DL
                </span>
                <span className='font-mono text-xs font-bold text-emerald-400 mt-0.5'>
                  {liveDownload != null ? `${liveDownload.toFixed(1)}M` : '—'}
                </span>
              </div>
              <div className='rounded-xl bg-neutral-950 border border-white/10 p-2 flex flex-col items-center text-center'>
                <span className='text-[9px] font-medium text-cyan-400 uppercase flex items-center gap-0.5'>
                  <ArrowUp className='size-2.5' /> UL
                </span>
                <span className='font-mono text-xs font-bold text-cyan-400 mt-0.5'>
                  {liveUpload != null ? `${liveUpload.toFixed(1)}M` : '—'}
                </span>
              </div>
            </div>

            {/* Test Action Button */}
            {testPhase === 'idle' || testPhase === 'completed' ? (
              <button
                type='button'
                onClick={() => void runSpeedtest()}
                className='w-full py-3 rounded-2xl bg-gradient-to-r from-[#0a84ff] to-[#0066cc] hover:from-[#0077ed] hover:to-[#0055b3] text-white font-bold text-sm tracking-wide shadow-lg shadow-blue-500/25 transition-all active:scale-[0.98]'
              >
                {testPhase === 'completed' ? 'REPETIR TEST 5G' : 'INICIAR TEST DE VELOCIDAD'}
              </button>
            ) : (
              <div className='w-full py-3 rounded-2xl bg-white/10 text-white/70 font-semibold text-xs text-center border border-white/10 animate-pulse'>
                Ejecutando ráfagas de prueba sobre uesimtun0…
              </div>
            )}

            {/* Results Breakdown Card (Available when completed) */}
            {testResult && (
              <div className='w-full rounded-2xl bg-neutral-950 border border-white/10 p-3 space-y-2 text-xs animate-in fade-in slide-in-from-bottom-2 duration-300'>
                <div className='flex items-center justify-between pb-1.5 border-b border-white/5'>
                  <span className='font-semibold text-white'>Telemetría 3GPP Rel 16</span>
                  <span className='text-[10px] font-mono text-emerald-400 font-medium'>Validado N6</span>
                </div>
                <div className='grid grid-cols-2 gap-y-1.5 text-[11px]'>
                  <div className='text-white/50'>Túnel PDU:</div>
                  <div className='font-mono text-right text-white'>{testResult.interface} ({testResult.client_ip})</div>
                  
                  <div className='text-white/50'>Regla PCC:</div>
                  <div className='font-mono text-right text-amber-400'>{testResult.pcc_rule ?? 'No observado'}</div>

                  <div className='text-white/50'>Flujo de Políticas:</div>
                  <div className='font-mono text-right text-white'>{testResult.qos}</div>

                  <div className='text-white/50'>Slice S-NSSAI:</div>
                  <div className='font-mono text-right text-sky-400'>
                    SST {snssai?.sst ?? testResult.snssai.sst}
                    {(snssai?.sd ?? testResult.snssai.sd) ? ` · SD ${snssai?.sd ?? testResult.snssai.sd}` : ''}
                  </div>

                  <div className='text-white/50'>Servidor UPF:</div>
                  <div className='font-mono text-right text-white'>{testResult.server_ip}</div>
                  <p className='col-span-2 text-xs text-white/60'>{testResult.note}</p>
                </div>
              </div>
            )}

          </div>
        )}

        {/* VIEW C: CORPORATE INTRANET PORTAL */}
        {currentPage === 'intranet' && (
          <div className='flex flex-col gap-3 py-2 animate-in fade-in duration-200'>
            <div className='rounded-2xl bg-neutral-950 border border-white/10 p-3.5 space-y-3'>
              <div className='flex items-center gap-2'>
                <div className='flex size-9 items-center justify-center rounded-xl bg-blue-500/10 border border-blue-500/20 text-blue-400'>
                  <Building2 className='size-5' />
                </div>
                <div>
                  <h3 className='text-xs font-bold text-white'>Intranet Corporativa 5G</h3>
                  <p className='text-[10px] text-white/50 font-mono'>http://intranet.empresa.5g:8080</p>
                </div>
              </div>

              {activeApn === 'corporate' ? (
                <div className='space-y-2 pt-1'>
                  <div className='rounded-xl bg-emerald-500/10 border border-emerald-500/30 p-2.5 text-emerald-400 text-xs flex items-center gap-2'>
                    <CheckCircle2 className='size-4 shrink-0' />
                    <span>Conectado al Slice Empresarial (UPF-03 / S-NSSAI 3:000003)</span>
                  </div>
                  <p className='text-xs text-white/70 leading-relaxed'>
                    Bienvenido al portal interno de la empresa. Tráfico estrictamente aislado de la salida pública a Internet.
                  </p>
                </div>
              ) : (
                <div className='space-y-2 pt-1'>
                  <div className='rounded-xl bg-amber-500/10 border border-amber-500/30 p-2.5 text-amber-300 text-xs'>
                    Estás navegando con el APN <strong>internet</strong>. La red <strong>corporate</strong> pertenece al gateway industrial independiente, disponible en Servicios → Casos Verticales.
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* VIEW D: CAMPUS PUCP */}
        {currentPage === 'pucp' && (
          <div className='flex flex-col gap-3 py-2 animate-in fade-in duration-200'>
            <div className='rounded-2xl bg-neutral-950 border border-white/10 p-3.5 space-y-3'>
              <div className='flex items-center gap-2'>
                <div className='flex size-9 items-center justify-center rounded-xl bg-indigo-500/10 border border-indigo-500/20 text-indigo-400'>
                  <GraduationCap className='size-5' />
                </div>
                <div>
                  <h3 className='text-xs font-bold text-white'>Portal Campus PUCP</h3>
                  <p className='text-[10px] text-white/50 font-mono'>campus.pucp.edu.pe</p>
                </div>
              </div>
              <p className='text-xs text-white/70 leading-relaxed'>
                Pontificia Universidad Católica del Perú · Laboratorio de Telecomunicaciones 5G Standalone.
              </p>
              <div className='rounded-xl bg-white/5 p-2.5 text-[11px] text-white/80 space-y-1 font-mono'>
                <div>• Alumno ID: 2026-TEL-5G</div>
                <div>• Proyecto: Core 5G SA con Tarificación Convergente (CHF)</div>
                <div>• Estado de Red: Conectado a gNodeB Local</div>
              </div>
            </div>
          </div>
        )}

        {/* VIEW E: OPEN5GS */}
        {currentPage === 'open5gs' && (
          <div className='flex flex-col gap-3 py-2 animate-in fade-in duration-200'>
            <div className='rounded-2xl bg-neutral-950 border border-white/10 p-3.5 space-y-2'>
              <h3 className='text-xs font-bold text-white flex items-center gap-2'>
                <Radio className='size-4 text-emerald-400' />
                <span>Open5GS 5G Standalone Documentation</span>
              </h3>
              <p className='text-xs text-white/70 leading-relaxed'>
                Open source implementation for 5G Core and EPC (3GPP Release 16). Funciones activas: AMF, SMF, UPF (x2), PCF, NRF, SCP, UDR, UDM, AUSF, CHF.
              </p>
            </div>
          </div>
        )}

      </div>

      {/* ================= 3. FLOATING BOTTOM ADDRESS BAR (IOS 17/18 SAFARI STYLE) ================= */}
      <div className='relative z-30 shrink-0 px-3 pt-2 pb-1.5 bg-black'>
        <form
          onSubmit={handleUrlSubmit}
          className='flex items-center gap-2 rounded-full bg-[#121214] border border-white/15 px-3.5 py-2 shadow-inner transition-all focus-within:border-blue-500/70 focus-within:ring-1 focus-within:ring-blue-500/30'
        >
          {/* Format / Reader button */}
          <span className='font-serif text-xs font-semibold text-white/50 hover:text-white cursor-pointer select-none'>
            aA
          </span>

          {/* Lock Icon */}
          <Lock className='size-3 text-white/40 shrink-0' />

          {/* Omnibox Input */}
          <input
            ref={urlInputRef}
            type='text'
            value={urlInput}
            onFocus={() => setIsEditingUrl(true)}
            onBlur={() => setTimeout(() => setIsEditingUrl(false), 200)}
            onChange={(e) => setUrlInput(e.target.value)}
            placeholder='Buscar o escribir dirección web…'
            className='flex-1 bg-transparent text-xs text-white placeholder-white/40 outline-none font-sans text-center'
            aria-label='Dirección web de Safari'
          />

          {/* Right Action (Clear or Refresh) */}
          {urlInput && isEditingUrl ? (
            <button
              type='button'
              onClick={() => setUrlInput('')}
              className='text-white/40 hover:text-white p-0.5'
            >
              <X className='size-3.5' />
            </button>
          ) : (
            <button
              type='button'
              onClick={() => {
                if (currentPage === 'speedtest') void runSpeedtest()
                else navigateTo(currentPage)
              }}
              className='text-white/40 hover:text-white p-0.5'
              title='Recargar página'
            >
              <RotateCcw className='size-3.5' />
            </button>
          )}
        </form>
      </div>

      {/* ================= 4. SAFARI BOTTOM TOOLBAR (BACK, FORWARD, SHARE, TABS) ================= */}
      <div className='relative z-20 shrink-0 h-11 bg-black px-5 flex items-center justify-between text-white/70'>
        {/* Back */}
        <button
          type='button'
          onClick={handleBack}
          disabled={historyIndex <= 0}
          className='p-1.5 hover:text-white disabled:opacity-25 transition-colors cursor-pointer'
          title='Página anterior'
        >
          <ChevronLeft className='size-5' />
        </button>

        {/* Forward */}
        <button
          type='button'
          onClick={handleForward}
          disabled={historyIndex >= pageHistory.length - 1}
          className='p-1.5 hover:text-white disabled:opacity-25 transition-colors cursor-pointer'
          title='Página siguiente'
        >
          <ChevronRight className='size-5' />
        </button>

        {/* Share */}
        <button
          type='button'
          onClick={() => {
            void navigator.clipboard.writeText(getUrlForPage(currentPage))
            onTriggerIslandAlert?.('✓ URL copiada al portapapeles')
          }}
          className='p-1.5 hover:text-white transition-colors cursor-pointer'
          title='Compartir'
        >
          <Share2 className='size-4' />
        </button>

        {/* Start / Home Page inside Safari */}
        <button
          type='button'
          onClick={() => navigateTo('start')}
          className={cn(
            'p-1.5 hover:text-white transition-colors cursor-pointer',
            currentPage === 'start' ? 'text-[#0a84ff]' : 'text-white/70'
          )}
          title='Favoritos'
        >
          <BookOpen className='size-4' />
        </button>

        {/* Tabs button */}
        <button
          type='button'
          onClick={() => onTriggerIslandAlert?.('Safari: 1 pestaña abierta')}
          className='p-1.5 hover:text-white transition-colors cursor-pointer flex items-center justify-center'
          title='Pestañas'
        >
          <div className='size-4 rounded-xs border border-current flex items-center justify-center text-[9px] font-bold'>
            1
          </div>
        </button>
      </div>

    </div>
  )
}
