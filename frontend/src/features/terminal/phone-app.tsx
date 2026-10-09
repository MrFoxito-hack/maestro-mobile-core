import { useState, useEffect } from 'react'
import {
  ChevronLeft,
  Delete,
  Mic,
  Phone,
  PhoneOff,
  Plus,
  Star,
  Clock,
  User,
  Volume2,
  Video,
  Users as UsersIcon,
  Radio,
  Sparkles,
} from 'lucide-react'
import { cn } from '@/lib/utils'

type PhoneTab = 'favorites' | 'recents' | 'contacts' | 'keypad' | 'voicemail'

interface PhoneAppProps {
  subscriber: string | null
  registered: boolean
  ipAddress?: string
  activeApn?: string
  balance?: {
    quota_bytes: number
    consumed_bytes: number
    reserved_bytes: number
    available_bytes: number
  } | null
  afBoost?: {
    active: boolean
    qos: '5QI=2' | '5QI=9'
    gbr_dl?: string
    mbr_dl?: string
  }
  traffic?: {
    rx_bytes?: number
    tx_bytes?: number
  }
  snssai?: {
    sst: number
    sd: number | null
  } | null
  cellInfo?: {
    cellId?: string | number
    tac?: string | number
  }
  initialNumber?: string
  onClose: () => void
  onTriggerIslandAlert?: (msg: string) => void
}

export function PhoneApp({
  subscriber,
  registered,
  ipAddress = '10.45.0.2',
  activeApn = 'internet',
  initialNumber,
  balance,
  afBoost,
  traffic,
  snssai,
  cellInfo,
  onClose,
  onTriggerIslandAlert,
}: PhoneAppProps) {
  const [tab, setTab] = useState<PhoneTab>('keypad')
  const [dialedNumber, setDialedNumber] = useState(initialNumber ?? '')

  useEffect(() => {
    if (initialNumber) {
      setDialedNumber(initialNumber)
      setTab('keypad')
    }
  }, [initialNumber])
  const [ussdResponse, setUssdResponse] = useState<string | null>(null)
  const [showDeviceInfo, setShowDeviceInfo] = useState(false)
  const [showFieldTest, setShowFieldTest] = useState(false)
  const [activeCall, setActiveCall] = useState<{
    number: string
    name?: string
    duration: number
    isMuted: boolean
    isSpeaker: boolean
  } | null>(null)

  // Real subscriber IMSI/MSISDN from testbed (100% unmasked, no asterisks or bullets)
  const cleanSubscriber = subscriber?.replace('imsi-', '') ?? '999700000000001'
  const imsi = /[\u2022*]/.test(cleanSubscriber) ? '999700000000001' : cleanSubscriber
  const msisdnMap: Record<string, string> = {
    '999700000000001': '51987654321',
    '999700000000002': '51939289458',
    '999700000000003': '51939289459',
    '999700000000004': '51939289460',
    '999700000000005': '51939289461',
  }
  const msisdn = msisdnMap[imsi] ?? `5193928${imsi.slice(-4)}`
  const imei = `86${imsi.slice(-13)}`

  // Handle in-call timer
  useEffect(() => {
    if (!activeCall) return
    const timer = setInterval(() => {
      setActiveCall((prev) => (prev ? { ...prev, duration: prev.duration + 1 } : null))
    }, 1000)
    return () => clearInterval(timer)
  }, [activeCall])

  const formatTimer = (seconds: number) => {
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }

  // Keypad button click
  const handleDigit = (digit: string) => {
    const next = dialedNumber + digit
    setDialedNumber(next)

    // Immediate iOS interception for *#06# (Device Info / IMEI)
    if (next === '*#06#') {
      setShowDeviceInfo(true)
      setDialedNumber('')
    }
  }

  const handleDelete = () => {
    setDialedNumber((prev) => prev.slice(0, -1))
  }

  const handleClear = () => {
    setDialedNumber('')
  }

  // Call / USSD Action
  const handleCall = () => {
    if (!dialedNumber) return

    const trimmed = dialedNumber.trim()

    // 1. USSD Carrier Query (*1#, *99#, *#100#)
    if (trimmed === '*1#' || trimmed === '*99#' || trimmed === '*#100#') {
      if (!registered) {
        setUssdResponse('Error de conexión o código MMI no válido.\nSin registro en la red 5G.')
      } else {
        setUssdResponse(`MSISDN:\n${msisdn}`)
      }
      setDialedNumber('')
      return
    }

    // Saldo / Bolsa (*777#)
    if (trimmed === '*777#') {
      if (!registered) {
        setUssdResponse('Error de conexión o código MMI no válido.\nSin registro en la red 5G.')
      } else if (!balance) {
        setUssdResponse('Saldo de Datos:\nSin paquete activo o CHF no disponible.')
      } else {
        const mb = (bytes: number) =>
          (bytes / 1_000_000).toLocaleString('es-PE', { maximumFractionDigits: 2 })
        const remainingBytes = Math.max(0, balance.quota_bytes - balance.consumed_bytes)
        setUssdResponse(
          `Saldo de Datos 5G:\n${mb(remainingBytes)} MB disponibles\nConsumo: ${mb(balance.consumed_bytes)} MB / ${mb(balance.quota_bytes)} MB\nBolsa: Paquete 5G SA`
        )
      }
      setDialedNumber('')
      return
    }

    // Desvío de llamadas MMI (*#21#)
    if (trimmed === '*#21#' || trimmed === '*#62#' || trimmed === '*#67#') {
      setUssdResponse('Desvío de llamadas\nVoz: Desactivado\nDatos: Desactivado')
      setDialedNumber('')
      return
    }

    if (trimmed === '*123#') {
      setUssdResponse(
        `Servicios MAEstro 5G SA:\n1. Consultar Saldo (*777#)\n2. Mi Número (*1#)\n3. Salir`
      )
      setDialedNumber('')
      return
    }

    // 2. iOS Field Test Mode (*3001#12345#*)
    if (trimmed === '*3001#12345#*' || trimmed === '*3001#12345#') {
      setShowFieldTest(true)
      setDialedNumber('')
      return
    }

    // 3. Regular Call
    let calleeName = 'Llamada 5G VoNR'
    if (trimmed === '123') calleeName = 'Atención al Cliente MAEstro'
    else if (trimmed === '911' || trimmed === '105') calleeName = 'Emergencias 5G'
    else if (trimmed === '999700000000002') calleeName = 'Terminal 5G Secundario'

    setActiveCall({
      number: trimmed,
      name: calleeName,
      duration: 0,
      isMuted: false,
      isSpeaker: false,
    })
    setDialedNumber('')
  }

  // --- SUBVIEW 1: IN-CALL SCREEN ---
  if (activeCall) {
    return (
      <div className='flex h-full flex-col justify-between bg-gradient-to-b from-[#1c1c1e] to-black px-6 py-8 text-white animate-in fade-in duration-200'>
        {/* Call Info Header */}
        <div className='flex flex-col items-center pt-8 text-center'>
          <h2 className='text-2xl font-semibold tracking-tight'>{activeCall.name}</h2>
          <p className='mt-1 text-base text-white/70 font-mono'>{activeCall.number}</p>
          <p className='mt-2 text-xs font-medium text-emerald-400 font-mono'>
            {activeCall.duration === 0 ? 'Conectando VoNR…' : formatTimer(activeCall.duration)}
          </p>
        </div>

        {/* In-Call 6-Button Grid (Apple iOS Style) */}
        <div className='grid grid-cols-3 gap-6 px-4'>
          {/* Mute */}
          <button
            type='button'
            onClick={() =>
              setActiveCall((prev) => (prev ? { ...prev, isMuted: !prev.isMuted } : null))
            }
            className='flex flex-col items-center gap-1.5'
          >
            <div
              className={cn(
                'flex size-16 items-center justify-center rounded-full transition-colors',
                activeCall.isMuted ? 'bg-white text-black' : 'bg-white/15 text-white hover:bg-white/25'
              )}
            >
              <Mic className='size-6' />
            </div>
            <span className='text-[11px] text-white/80'>Silenciar</span>
          </button>

          {/* Keypad */}
          <button type='button' className='flex flex-col items-center gap-1.5 opacity-60'>
            <div className='flex size-16 items-center justify-center rounded-full bg-white/15 text-white'>
              <span className='font-mono text-xl'>#</span>
            </div>
            <span className='text-[11px] text-white/80'>Teclado</span>
          </button>

          {/* Speaker */}
          <button
            type='button'
            onClick={() =>
              setActiveCall((prev) => (prev ? { ...prev, isSpeaker: !prev.isSpeaker } : null))
            }
            className='flex flex-col items-center gap-1.5'
          >
            <div
              className={cn(
                'flex size-16 items-center justify-center rounded-full transition-colors',
                activeCall.isSpeaker ? 'bg-white text-black' : 'bg-white/15 text-white hover:bg-white/25'
              )}
            >
              <Volume2 className='size-6' />
            </div>
            <span className='text-[11px] text-white/80'>Altavoz</span>
          </button>

          {/* Add Call */}
          <button type='button' className='flex flex-col items-center gap-1.5 opacity-60'>
            <div className='flex size-16 items-center justify-center rounded-full bg-white/15 text-white'>
              <Plus className='size-6' />
            </div>
            <span className='text-[11px] text-white/80'>Añadir</span>
          </button>

          {/* FaceTime */}
          <button type='button' className='flex flex-col items-center gap-1.5 opacity-60'>
            <div className='flex size-16 items-center justify-center rounded-full bg-white/15 text-white'>
              <Video className='size-6' />
            </div>
            <span className='text-[11px] text-white/80'>FaceTime</span>
          </button>

          {/* Contacts */}
          <button type='button' className='flex flex-col items-center gap-1.5 opacity-60'>
            <div className='flex size-16 items-center justify-center rounded-full bg-white/15 text-white'>
              <UsersIcon className='size-6' />
            </div>
            <span className='text-[11px] text-white/80'>Contactos</span>
          </button>
        </div>

        {/* End Call Button */}
        <div className='flex justify-center pb-6'>
          <button
            type='button'
            onClick={() => setActiveCall(null)}
            className='flex size-[68px] items-center justify-center rounded-full bg-[#ff3b30] text-white shadow-xl shadow-red-950/40 hover:bg-[#ff453a] active:scale-95 transition-transform'
            title='Finalizar llamada'
          >
            <PhoneOff className='size-8' />
          </button>
        </div>
      </div>
    )
  }

  // --- SUBVIEW 2: FIELD TEST MODE (*3001#12345#*) ---
  if (showFieldTest) {
    const pci = cellInfo?.cellId ?? 1
    const tacStr = cellInfo?.tac ? String(cellInfo.tac).padStart(4, '0') : '0001'
    const sst = snssai?.sst ?? 1
    const sdStr =
      snssai?.sd == null
        ? activeApn === 'corporate'
          ? '000002'
          : '000001'
        : snssai.sd.toString(16).padStart(6, '0')

    return (
      <div className='flex h-full flex-col bg-[#000] text-white animate-in fade-in duration-200'>
        {/* Navigation Bar */}
        <div className='flex items-center justify-between border-b border-white/15 px-4 py-3'>
          <button
            type='button'
            onClick={() => setShowFieldTest(false)}
            className='flex items-center text-sm font-medium text-[#0a84ff]'
          >
            <ChevronLeft className='size-5 -ml-1' />
            Teléfono
          </button>
          <span className='text-sm font-semibold'>Field Test</span>
          <div className='w-12' />
        </div>

        {/* Content */}
        <div className='flex-1 overflow-y-auto p-4 space-y-4 text-xs select-text [&::-webkit-scrollbar]:hidden [scrollbar-width:none]'>
          <div className='flex items-center gap-2 text-sky-400 select-none'>
            <Radio className='size-4' />
            <span className='font-semibold uppercase tracking-wider text-[11px]'>5G NR Serving Cell Info</span>
          </div>

          <div className='rounded-xl bg-[#1c1c1e] divide-y divide-white/10 overflow-hidden font-mono text-[11px]'>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>PLMN</span>
              <span className='text-white font-semibold'>999-70</span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>NR Band</span>
              <span className='text-sky-400 font-semibold'>n78 (3500 MHz TDD)</span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>Physical Cell ID (PCI)</span>
              <span className='text-white'>{pci}</span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>Tracking Area Code (TAC)</span>
              <span className='text-white'>{tacStr} (0x{tacStr})</span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>gNodeB ID</span>
              <span className='text-white'>131072</span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>RSRP (Potencia Señal)</span>
              <span className='text-emerald-400 font-semibold'>-83 dBm (Excelente)</span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>RSRQ (Calidad Señal)</span>
              <span className='text-emerald-400'>-11 dB</span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>SINR (Relación Señal/Ruido)</span>
              <span className='text-emerald-400'>24.5 dB</span>
            </div>
          </div>

          <div className='flex items-center gap-2 text-emerald-400 pt-2'>
            <Sparkles className='size-4' />
            <span className='font-semibold uppercase tracking-wider text-[11px]'>Sesión PDU / Network Slicing</span>
          </div>

          <div className='rounded-xl bg-[#1c1c1e] divide-y divide-white/10 overflow-hidden font-mono text-[11px]'>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>S-NSSAI</span>
              <span className='text-white'>SST: {sst} ({sst === 1 ? 'eMBB' : 'Custom'}) · SD: {sdStr}</span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>APN / DNN</span>
              <span className='text-white uppercase'>{activeApn}</span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>IPv4 Asignada</span>
              <span className='text-sky-400'>{ipAddress}</span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>QoS Flow (5QI)</span>
              <span className={cn('font-semibold', afBoost?.active ? 'text-amber-400' : 'text-slate-300')}>
                {afBoost?.active ? '5QI=2 · GBR Video (10M / 20M)' : '5QI=9 · Default eMBB'}
              </span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>Tráfico Interfaz (uesimtun0)</span>
              <span className='text-white font-mono'>
                {traffic?.rx_bytes != null && traffic?.tx_bytes != null
                  ? `Rx: ${(traffic.rx_bytes / 1024).toFixed(1)} KB · Tx: ${(traffic.tx_bytes / 1024).toFixed(1)} KB`
                  : 'Rx: 0 KB · Tx: 0 KB'}
              </span>
            </div>
            <div className='flex justify-between p-3'>
              <span className='text-white/60 font-sans'>Estado Registro AMF</span>
              <span className='text-emerald-400 font-sans'>{registered ? '5GMM-REGISTERED' : 'DEREGISTERED'}</span>
            </div>
          </div>

          <p className='text-[10px] text-white/40 leading-relaxed px-1 text-center pt-2'>
            * Telemetría de sesión PDU, QoS N5, Slicing y cuota CHF 100% en vivo del Core Open5GS; métricas de canal RF emuladas por UERANSIM.
          </p>
        </div>
      </div>
    )
  }

  // --- MAIN VIEW: KEYPAD & OTHER TABS ---
  return (
    <div className='flex h-full flex-col justify-between bg-black text-white relative animate-in fade-in duration-150 select-none'>
      {/* Top Header Bar */}
      <div className='flex items-center justify-between px-4 pt-2 shrink-0'>
        <button
          type='button'
          onClick={onClose}
          className='flex items-center text-sm font-medium text-[#0a84ff] hover:opacity-80 transition cursor-pointer'
        >
          <ChevronLeft className='size-5 -ml-1' />
          Inicio
        </button>
        <span className='text-sm font-semibold text-white/90'>Teléfono</span>
        <div className='w-12' />
      </div>

      {/* Main Content Area */}
      <div className='flex-1 flex flex-col justify-between px-6 pb-2 pt-2 min-h-0 overflow-y-auto [&::-webkit-scrollbar]:hidden [scrollbar-width:none]'>
        {tab === 'keypad' && (
          <>
            {/* Dialed Number Display */}
            <div className='flex flex-col items-center justify-center min-h-[56px] px-2'>
              <span
                className={cn(
                  'font-light tracking-tight text-white select-all text-center break-all transition-all duration-150',
                  dialedNumber.length > 12
                    ? 'text-2xl'
                    : dialedNumber.length > 8
                      ? 'text-3xl'
                      : 'text-4xl'
                )}
              >
                {dialedNumber}
              </span>
              {dialedNumber && (
                <button
                  type='button'
                  onClick={() => onTriggerIslandAlert?.(`Número marcado: ${dialedNumber}`)}
                  className='text-xs font-medium text-[#0a84ff] mt-1 hover:underline cursor-pointer'
                >
                  Añadir número
                </button>
              )}
            </div>

            {/* 3x4 iOS Dialpad Grid */}
            <div className='grid grid-cols-3 gap-x-5 gap-y-3.5 max-w-[280px] mx-auto w-full my-auto'>
              {[
                { digit: '1', letters: '' },
                { digit: '2', letters: 'ABC' },
                { digit: '3', letters: 'DEF' },
                { digit: '4', letters: 'GHI' },
                { digit: '5', letters: 'JKL' },
                { digit: '6', letters: 'MNO' },
                { digit: '7', letters: 'PQRS' },
                { digit: '8', letters: 'TUV' },
                { digit: '9', letters: 'WXYZ' },
                { digit: '*', letters: '' },
                { digit: '0', letters: '+' },
                { digit: '#', letters: '' },
              ].map(({ digit, letters }) => (
                <button
                  key={digit}
                  type='button'
                  onClick={() => handleDigit(digit)}
                  className='group relative flex size-[68px] flex-col items-center justify-center rounded-full bg-[#2c2c2e] hover:bg-[#3a3a3c] active:bg-[#48484a] text-white transition-all active:scale-95 shadow-md shadow-black/40 cursor-pointer mx-auto'
                >
                  <span
                    className={cn(
                      'font-normal leading-none',
                      letters ? 'text-2xl mt-0.5' : 'text-3xl'
                    )}
                  >
                    {digit}
                  </span>
                  {letters && (
                    <span className='text-[9px] font-semibold tracking-[1.5px] text-white/60 leading-none mt-1 uppercase'>
                      {letters}
                    </span>
                  )}
                </button>
              ))}
            </div>

            {/* Call & Delete Control Bar */}
            <div className='grid grid-cols-3 items-center max-w-[280px] mx-auto w-full mb-1'>
              <div />

              {/* Green Call Button */}
              <div className='flex justify-center'>
                <button
                  type='button'
                  onClick={handleCall}
                  disabled={!dialedNumber}
                  className='flex size-[68px] items-center justify-center rounded-full bg-[#30d158] hover:bg-[#34c759] active:bg-[#28b84d] text-white shadow-lg shadow-emerald-950/40 active:scale-95 transition-transform disabled:opacity-40 disabled:pointer-events-none cursor-pointer'
                  title='Llamar'
                >
                  <Phone className='size-8 fill-current' />
                </button>
              </div>

              {/* Backspace Delete Button */}
              <div className='flex justify-center'>
                {dialedNumber && (
                  <button
                    type='button'
                    onClick={handleDelete}
                    onDoubleClick={handleClear}
                    className='flex size-12 items-center justify-center rounded-full text-white/70 hover:text-white active:scale-90 transition'
                    title='Borrar dígito'
                  >
                    <Delete className='size-6' />
                  </button>
                )}
              </div>
            </div>
          </>
        )}

        {tab === 'favorites' && (
          <div className='flex-1 flex flex-col gap-3 pt-2 text-left animate-in fade-in duration-150'>
            <h2 className='text-2xl font-bold tracking-tight text-white px-1'>Favoritos</h2>
            <div className='rounded-2xl bg-[#1c1c1e] divide-y divide-[#2c2c2e] overflow-hidden ring-1 ring-white/5'>
              <button
                type='button'
                onClick={() => {
                  setDialedNumber('*777#')
                  setTab('keypad')
                }}
                className='w-full flex items-center justify-between p-3.5 hover:bg-[#2c2c2e]/60 transition text-left cursor-pointer'
              >
                <div>
                  <div className='text-sm font-semibold text-white'>Consulta de Saldo CHF</div>
                  <div className='text-xs text-[#8e8e93] font-mono'>*777# · Cuota N40</div>
                </div>
                <Phone className='size-4 text-[#30d158]' />
              </button>
              <button
                type='button'
                onClick={() => {
                  setDialedNumber('*1#')
                  setTab('keypad')
                }}
                className='w-full flex items-center justify-between p-3.5 hover:bg-[#2c2c2e]/60 transition text-left cursor-pointer'
              >
                <div>
                  <div className='text-sm font-semibold text-white'>Mi Número (MSISDN)</div>
                  <div className='text-xs text-[#8e8e93] font-mono'>*1# · Identidad 3GPP</div>
                </div>
                <Phone className='size-4 text-[#30d158]' />
              </button>
              <button
                type='button'
                onClick={() => {
                  setDialedNumber('123')
                  setTab('keypad')
                }}
                className='w-full flex items-center justify-between p-3.5 hover:bg-[#2c2c2e]/60 transition text-left cursor-pointer'
              >
                <div>
                  <div className='text-sm font-semibold text-white'>Atención al Cliente MAEstro</div>
                  <div className='text-xs text-[#8e8e93] font-mono'>123 · Soporte 5G Core</div>
                </div>
                <Phone className='size-4 text-[#30d158]' />
              </button>
              <button
                type='button'
                onClick={() => {
                  setDialedNumber('911')
                  setTab('keypad')
                }}
                className='w-full flex items-center justify-between p-3.5 hover:bg-[#2c2c2e]/60 transition text-left cursor-pointer'
              >
                <div>
                  <div className='text-sm font-semibold text-white'>Central de Emergencias</div>
                  <div className='text-xs text-[#8e8e93] font-mono'>911 · Prioridad Alta</div>
                </div>
                <Phone className='size-4 text-[#ff3b30]' />
              </button>
            </div>
          </div>
        )}

        {tab === 'recents' && (
          <div className='flex-1 flex flex-col gap-3 pt-2 text-left animate-in fade-in duration-150'>
            <h2 className='text-2xl font-bold tracking-tight text-white px-1'>Recientes</h2>
            <div className='rounded-2xl bg-[#1c1c1e] divide-y divide-[#2c2c2e] overflow-hidden ring-1 ring-white/5'>
              <button
                type='button'
                onClick={() => {
                  setDialedNumber('*777#')
                  setTab('keypad')
                }}
                className='w-full flex items-center justify-between p-3.5 hover:bg-[#2c2c2e]/60 transition text-left cursor-pointer'
              >
                <div>
                  <div className='text-sm font-semibold text-white'>*777#</div>
                  <div className='text-xs text-[#8e8e93]'>Consulta de Saldo USSD</div>
                </div>
                <span className='text-[11px] text-[#8e8e93] font-mono'>Hoy</span>
              </button>
              <button
                type='button'
                onClick={() => {
                  setDialedNumber('*1#')
                  setTab('keypad')
                }}
                className='w-full flex items-center justify-between p-3.5 hover:bg-[#2c2c2e]/60 transition text-left cursor-pointer'
              >
                <div>
                  <div className='text-sm font-semibold text-white'>*1#</div>
                  <div className='text-xs text-[#8e8e93]'>Consulta de MSISDN</div>
                </div>
                <span className='text-[11px] text-[#8e8e93] font-mono'>Hoy</span>
              </button>
              <button
                type='button'
                onClick={() => {
                  setShowFieldTest(true)
                }}
                className='w-full flex items-center justify-between p-3.5 hover:bg-[#2c2c2e]/60 transition text-left cursor-pointer'
              >
                <div>
                  <div className='text-sm font-semibold text-sky-400'>*3001#12345#*</div>
                  <div className='text-xs text-[#8e8e93]'>Field Test Diagnostic Mode</div>
                </div>
                <span className='text-[11px] text-[#8e8e93] font-mono'>Hoy</span>
              </button>
            </div>
          </div>
        )}

        {tab === 'contacts' && (
          <div className='flex-1 flex flex-col gap-3 pt-2 text-left animate-in fade-in duration-150'>
            <h2 className='text-2xl font-bold tracking-tight text-white px-1'>Contactos</h2>
            <div className='rounded-2xl bg-[#1c1c1e] divide-y divide-[#2c2c2e] overflow-hidden ring-1 ring-white/5'>
              <div className='p-3.5 select-text'>
                <div className='text-xs text-white/50 uppercase tracking-wider font-semibold select-none'>Mi Dispositivo</div>
                <div
                  onClick={() => {
                    void navigator.clipboard.writeText(msisdn)
                    onTriggerIslandAlert?.(`✓ ${msisdn} copiado`)
                  }}
                  title='Haz clic para copiar'
                  className='text-sm font-semibold text-white mt-0.5 select-text cursor-text selection:bg-[#0a84ff] selection:text-white'
                >
                  {msisdn}
                </div>
                <div
                  onClick={() => {
                    void navigator.clipboard.writeText(imsi)
                    onTriggerIslandAlert?.(`✓ SUPI ${imsi} copiado`)
                  }}
                  title='Haz clic para copiar'
                  className='text-[11px] text-teal-400 font-mono mt-0.5 select-text cursor-text selection:bg-[#0a84ff] selection:text-white'
                >
                  SUPI: {imsi}
                </div>
              </div>
              <button
                type='button'
                onClick={() => {
                  setDialedNumber('123')
                  setTab('keypad')
                }}
                className='w-full flex items-center justify-between p-3.5 hover:bg-[#2c2c2e]/60 transition text-left cursor-pointer'
              >
                <div>
                  <div className='text-sm font-semibold text-white'>Atención MAEstro 5G</div>
                  <div className='text-xs text-[#8e8e93] font-mono'>123</div>
                </div>
                <Phone className='size-4 text-[#30d158]' />
              </button>
              <button
                type='button'
                onClick={() => {
                  setDialedNumber('911')
                  setTab('keypad')
                }}
                className='w-full flex items-center justify-between p-3.5 hover:bg-[#2c2c2e]/60 transition text-left cursor-pointer'
              >
                <div>
                  <div className='text-sm font-semibold text-white'>Central de Emergencias</div>
                  <div className='text-xs text-[#8e8e93] font-mono'>911</div>
                </div>
                <Phone className='size-4 text-[#ff3b30]' />
              </button>
              <button
                type='button'
                onClick={() => {
                  setDialedNumber('999700000000002')
                  setTab('keypad')
                }}
                className='w-full flex items-center justify-between p-3.5 hover:bg-[#2c2c2e]/60 transition text-left cursor-pointer'
              >
                <div>
                  <div className='text-sm font-semibold text-white'>Terminal 5G Secundario</div>
                  <div className='text-xs text-[#8e8e93] font-mono'>999700000000002</div>
                </div>
                <Phone className='size-4 text-[#30d158]' />
              </button>
            </div>
          </div>
        )}

        {tab === 'voicemail' && (
          <div className='flex-1 flex flex-col items-center justify-center gap-4 text-center px-4 animate-in fade-in duration-150'>
            <div className='flex size-16 items-center justify-center rounded-full bg-[#1c1c1e] text-white/40 border border-white/10'>
              <div className='flex items-center -space-x-1.5'>
                <span className='size-6 rounded-full border-2 border-current' />
                <span className='size-6 rounded-full border-2 border-current' />
              </div>
            </div>
            <div>
              <h3 className='text-base font-semibold text-white'>No hay mensajes de voz</h3>
              <p className='text-xs text-white/50 mt-1 max-w-[220px]'>
                Tu buzón 5G SA en Open5GS no registra mensajes pendientes.
              </p>
            </div>
            <button
              type='button'
              onClick={() => {
                setDialedNumber('*86')
                setTab('keypad')
              }}
              className='mt-2 rounded-xl bg-[#0a84ff] px-4 py-2 text-xs font-semibold text-white hover:bg-[#0071e3] transition cursor-pointer'
            >
              Llamar al buzón (*86)
            </button>
          </div>
        )}
      </div>

      {/* iOS Phone Tab Bar */}
      <div className='flex items-center justify-around bg-black px-2 py-2 shrink-0 text-[10px]'>
        <button
          type='button'
          onClick={() => setTab('favorites')}
          className={cn(
            'flex flex-col items-center gap-0.5 cursor-pointer transition',
            tab === 'favorites' ? 'text-[#0a84ff]' : 'text-white/40 hover:text-white/70'
          )}
        >
          <Star className='size-5' />
          <span>Favoritos</span>
        </button>

        <button
          type='button'
          onClick={() => setTab('recents')}
          className={cn(
            'flex flex-col items-center gap-0.5 cursor-pointer transition',
            tab === 'recents' ? 'text-[#0a84ff]' : 'text-white/40 hover:text-white/70'
          )}
        >
          <Clock className='size-5' />
          <span>Recientes</span>
        </button>

        <button
          type='button'
          onClick={() => setTab('contacts')}
          className={cn(
            'flex flex-col items-center gap-0.5 cursor-pointer transition',
            tab === 'contacts' ? 'text-[#0a84ff]' : 'text-white/40 hover:text-white/70'
          )}
        >
          <User className='size-5' />
          <span>Contactos</span>
        </button>

        <button
          type='button'
          onClick={() => setTab('keypad')}
          className={cn(
            'flex flex-col items-center gap-0.5 cursor-pointer transition',
            tab === 'keypad' ? 'text-[#0a84ff]' : 'text-white/40 hover:text-white/70'
          )}
        >
          <div className='grid grid-cols-3 gap-0.5 size-4 p-0.5'>
            {[...Array(9)].map((_, i) => (
              <span key={i} className='size-1 rounded-full bg-current' />
            ))}
          </div>
          <span>Teclado</span>
        </button>

        <button
          type='button'
          onClick={() => setTab('voicemail')}
          className={cn(
            'flex flex-col items-center gap-0.5 cursor-pointer transition',
            tab === 'voicemail' ? 'text-[#0a84ff]' : 'text-white/40 hover:text-white/70'
          )}
        >
          <div className='flex items-center -space-x-1'>
            <span className='size-3 rounded-full border border-current' />
            <span className='size-3 rounded-full border border-current' />
          </div>
          <span>Buzón</span>
        </button>
      </div>

      {/* --- MODAL 1: AUTHENTIC iOS USSD ALERT (*1#, *99#, etc.) --- */}
      {ussdResponse && (
        <div className='absolute inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-6 animate-in fade-in duration-150 select-text'>
          <div className='w-full max-w-[270px] rounded-[14px] bg-[#252528] border border-white/10 shadow-2xl overflow-hidden flex flex-col text-center select-text'>
            <div className='p-5 space-y-1.5 select-text'>
              <p
                onClick={() => {
                  const match = ussdResponse.match(/\b\d{9,15}\b/)
                  const toCopy = match ? match[0] : ussdResponse
                  void navigator.clipboard.writeText(toCopy)
                  onTriggerIslandAlert?.(`✓ ${toCopy} copiado`)
                }}
                title='Arrastra con el mouse para seleccionar o haz clic para copiar'
                className='text-[15px] font-medium text-white whitespace-pre-line leading-relaxed font-sans select-text cursor-text selection:bg-[#0a84ff] selection:text-white active:opacity-80 transition'
              >
                {ussdResponse}
              </p>
            </div>
            <div className='border-t border-white/10'>
              <button
                type='button'
                onClick={() => setUssdResponse(null)}
                className='w-full py-2.5 text-[16px] font-medium text-[#0a84ff] hover:bg-white/5 active:bg-white/10 transition cursor-pointer select-none'
              >
                Cerrar
              </button>
            </div>
          </div>
        </div>
      )}

      {/* --- MODAL 2: AUTHENTIC iOS DEVICE INFO (*#06#) --- */}
      {showDeviceInfo && (
        <div className='absolute inset-0 z-50 flex flex-col justify-end bg-black/75 backdrop-blur-md animate-in slide-in-from-bottom duration-200 select-text'>
          <div className='w-full rounded-t-3xl bg-[#1c1c1e] border-t border-white/10 p-5 space-y-5 text-center select-text'>
            <div className='mx-auto h-1 w-10 rounded-full bg-white/20 select-none' />

            <h3 className='text-base font-semibold text-white select-none'>Información del dispositivo</h3>

            {/* Simulated Barcode */}
            <div className='mx-auto flex h-12 w-48 items-center justify-center gap-1 bg-white p-2 rounded select-none'>
              {[2, 1, 3, 1, 2, 4, 1, 2, 3, 1, 2, 1, 4, 2, 1, 3, 1, 2].map((w, i) => (
                <span
                  key={i}
                  className='h-full bg-black'
                  style={{ width: `${w * 2}px` }}
                />
              ))}
            </div>

            <div className='rounded-xl bg-[#2c2c2e] p-3 text-left space-y-2 font-mono text-[11px] select-text'>
              <div>
                <p className='text-[10px] text-white/50 font-sans uppercase select-none'>EID (eSIM)</p>
                <p
                  onClick={() => {
                    void navigator.clipboard.writeText('89049032000001000000000000000001')
                    onTriggerIslandAlert?.('✓ EID copiado')
                  }}
                  title='Haz clic para copiar'
                  className='text-white font-semibold select-text cursor-text selection:bg-[#0a84ff] selection:text-white'
                >
                  89049032000001000000000000000001
                </p>
              </div>
              <div className='border-t border-white/10 pt-2'>
                <p className='text-[10px] text-white/50 font-sans uppercase select-none'>IMEI</p>
                <p
                  onClick={() => {
                    void navigator.clipboard.writeText(imei)
                    onTriggerIslandAlert?.(`✓ IMEI ${imei} copiado`)
                  }}
                  title='Haz clic para copiar'
                  className='text-white font-semibold select-text cursor-text selection:bg-[#0a84ff] selection:text-white'
                >
                  {imei}
                </p>
              </div>
              <div className='border-t border-white/10 pt-2'>
                <p className='text-[10px] text-white/50 font-sans uppercase select-none'>IMEI SV</p>
                <p className='text-white select-text cursor-text selection:bg-[#0a84ff] selection:text-white'>01</p>
              </div>
            </div>

            <button
              type='button'
              onClick={() => setShowDeviceInfo(false)}
              className='w-full rounded-xl bg-white/10 py-3 text-sm font-semibold text-white hover:bg-white/20 active:scale-98 transition cursor-pointer select-none'
            >
              Cerrar
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
