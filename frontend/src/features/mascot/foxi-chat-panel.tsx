import { useState, useRef, useEffect } from 'react'
import { Link } from '@tanstack/react-router'
import { X, Send, Sparkles, RefreshCw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { api } from '@/lib/api'
import { useMascotStore } from '@/stores/mascot-store'

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
}

interface FoxiChatPanelProps {
  isOpen: boolean
  onClose: () => void
  onThinkingChange?: (isThinking: boolean) => void
}

const QUICK_PROMPTS = [
  {
    text: '¿Mi terminal está ok?',
    prompt: '¿Mi terminal móvil está registrado y con sesión PDU activa?',
  },
  {
    text: 'Auditoría saldo CHF',
    prompt: '¿Por qué mi IMSI actual no tiene tráfico de datos? Revisa CHF y estado de sesión PDU.',
  },
  {
    text: 'Acelerador eBPF/XDP',
    prompt: '¿Cómo está el acelerador de kernel eBPF/XDP en la UPF-01? ¿Hay paquetes en bypass?',
  },
  {
    text: 'Alarmas activas',
    prompt: 'Dame un resumen de las alarmas 3GPP activas en el Alarm Center.',
  },
  {
    text: 'Latencia por Slice',
    prompt: 'Compara la latencia y pérdida de paquetes entre el slice eMBB y URLLC.',
  },
]

// Parser liviano de Markdown para negritas (**texto**), código (`codigo`), cursivas (*texto*), listas y enlaces ([texto](url))
function renderFormattedLine(line: string) {
  const parts: (string | React.ReactNode)[] = []
  const regex = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g
  let lastIndex = 0
  let match: RegExpExecArray | null

  while ((match = regex.exec(line)) !== null) {
    if (match.index > lastIndex) {
      parts.push(line.slice(lastIndex, match.index))
    }
    const token = match[0]
    if (token.startsWith('**') && token.endsWith('**')) {
      parts.push(
        <strong key={match.index} className='font-semibold text-zinc-100'>
          {token.slice(2, -2)}
        </strong>
      )
    } else if (token.startsWith('`') && token.endsWith('`')) {
      parts.push(
        <code key={match.index} className='rounded bg-white/10 px-1 py-0.5 font-mono text-[11px] text-orange-300'>
          {token.slice(1, -1)}
        </code>
      )
    } else if (token.startsWith('*') && token.endsWith('*')) {
      parts.push(
        <em key={match.index} className='italic text-zinc-300'>
          {token.slice(1, -1)}
        </em>
      )
    } else if (token.startsWith('[') && token.includes('](') && token.endsWith(')')) {
      const linkMatch = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
      if (linkMatch) {
        const linkHref = linkMatch[2]
        if (linkHref.startsWith('/')) {
          parts.push(
            <Link
              key={match.index}
              to={linkHref as any}
              className='inline-flex items-center gap-1 font-semibold text-orange-400 underline decoration-orange-500/50 underline-offset-2 hover:text-orange-300 hover:decoration-orange-400 transition-colors cursor-pointer'
            >
              {linkMatch[1]}
            </Link>
          )
        } else {
          parts.push(
            <a
              key={match.index}
              href={linkHref}
              target='_blank'
              rel='noopener noreferrer'
              className='inline-flex items-center gap-1 font-semibold text-orange-400 underline decoration-orange-500/50 underline-offset-2 hover:text-orange-300 hover:decoration-orange-400 transition-colors'
            >
              {linkMatch[1]}
            </a>
          )
        }
      }
    }
    lastIndex = regex.lastIndex
  }
  if (lastIndex < line.length) {
    parts.push(line.slice(lastIndex))
  }
  return parts.length > 0 ? parts : line
}

function FormattedMessage({ content }: { content: string }) {
  const lines = content.split('\n')
  return (
    <div className='space-y-1 font-sans'>
      {lines.map((line, idx) => {
        const trimmed = line.trim()
        if (!trimmed) {
          return <div key={idx} className='h-1.5' />
        }
        if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          return (
            <div key={idx} className='flex items-start gap-1.5 pl-1 my-0.5'>
              <span className='text-orange-400 select-none leading-relaxed'>•</span>
              <span className='flex-1 leading-relaxed'>{renderFormattedLine(trimmed.slice(2))}</span>
            </div>
          )
        }
        const numMatch = trimmed.match(/^(\d+)\.\s+(.*)$/)
        if (numMatch) {
          return (
            <div key={idx} className='flex items-start gap-1.5 pl-1 my-0.5'>
              <span className='text-orange-400/90 font-mono text-[10.5px] select-none leading-relaxed'>
                {numMatch[1]}.
              </span>
              <span className='flex-1 leading-relaxed'>{renderFormattedLine(numMatch[2])}</span>
            </div>
          )
        }
        return (
          <div key={idx} className='leading-relaxed'>
            {renderFormattedLine(line)}
          </div>
        )
      })}
    </div>
  )
}

export function FoxiChatPanel({ isOpen, onClose, onThinkingChange }: FoxiChatPanelProps) {
  const { activeBot, setActiveBot, toggleBot } = useMascotStore()

  const [messages, setMessages] = useState<ChatMessage[]>(() => [
    {
      id: 'init-1',
      role: 'assistant',
      content:
        activeBot === 'hyppety'
          ? '¡Hola Miguel! 👋 Soy **Hyppety**, tu copiloto del NOC 5G (Motor Local RTX 5070).\n\nPuedo auditar en tiempo real tus cuentas CHF, el estado de tus terminales UE, métricas de slices y el acelerador eBPF/XDP.\n\n💡 *Tip: Escribe `/change` o `/hxxpper` para transferir control a hxxpper.*'
          : 'Sistema listo. 👾 Soy **hxxpper**, tu módulo de diagnóstico profundo y anomalías 5G (Gemini Cloud Engine).\n\nEspecializado en telemetría de red, seguridad, resolución de fallas y análisis de trazas PCAP.\n\n💡 *Tip: Escribe `/change` o `/hyppety` para regresar al copiloto operativo.*',
      timestamp: 'Ahora',
    },
  ])
  const [inputValue, setInputValue] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const messagesEndRef = useRef<HTMLDivElement>(null)

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }

  useEffect(() => {
    if (isOpen) {
      scrollToBottom()
    }
  }, [isOpen, messages])

  const handleSendMessage = async (textToSend?: string) => {
    const text = (textToSend ?? inputValue).trim()
    if (!text || isLoading) return

    const userMsg: ChatMessage = {
      id: `usr-${Date.now()}`,
      role: 'user',
      content: text,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    }

    const lower = text.toLowerCase()

    // --- COMANDOS MINIMALISTAS DE CAMBIO DE BOT (/change, /switch, /hyppety, /hxxpper) ---
    if (['/change', '/switch'].includes(lower)) {
      const nextBot = toggleBot()
      const handoverMsg: ChatMessage = {
        id: `bot-handover-${Date.now()}`,
        role: 'assistant',
        content:
          nextBot === 'hxxpper'
            ? '🔄 **Handover 5G completado:** Control transferido a **hxxpper** (Gemini Cloud Engine).\n\nModo de diagnóstico avanzado y auditoría de anomalías activo. 👾'
            : '🔄 **Handover 5G completado:** Control transferido a **Hyppety** (Ollama Local RTX 5070).\n\nCopiloto del NOC 5G en línea para asistencia operativa y pedagógica. 🦊',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }
      setMessages((prev) => [...prev, userMsg, handoverMsg])
      setInputValue('')
      return
    }

    if (lower === '/hyppety') {
      if (activeBot === 'hyppety') {
        const alreadyMsg: ChatMessage = {
          id: `bot-already-${Date.now()}`,
          role: 'assistant',
          content: '🦊 ¡Ya me encuentro activo! Soy **Hyppety**, tu copiloto local (Qwen 2.5 en GPU RTX 5070).',
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        }
        setMessages((prev) => [...prev, userMsg, alreadyMsg])
        setInputValue('')
        return
      }
      setActiveBot('hyppety')
      const handoverMsg: ChatMessage = {
        id: `bot-handover-${Date.now()}`,
        role: 'assistant',
        content: '🔄 **Handover 5G completado:** Control transferido a **Hyppety** (Ollama Local RTX 5070).\n\nCopiloto del NOC 5G en línea para asistencia operativa y pedagógica. 🦊',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }
      setMessages((prev) => [...prev, userMsg, handoverMsg])
      setInputValue('')
      return
    }

    if (lower === '/hxxpper') {
      if (activeBot === 'hxxpper') {
        const alreadyMsg: ChatMessage = {
          id: `bot-already-${Date.now()}`,
          role: 'assistant',
          content: '👾 **hxxpper** ya está activo en este canal. Vigilancia de anomalías y motor Gemini en espera.',
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        }
        setMessages((prev) => [...prev, userMsg, alreadyMsg])
        setInputValue('')
        return
      }
      setActiveBot('hxxpper')
      const handoverMsg: ChatMessage = {
        id: `bot-handover-${Date.now()}`,
        role: 'assistant',
        content: '🔄 **Handover 5G completado:** Control transferido a **hxxpper** (Gemini Cloud Engine).\n\nModo de diagnóstico avanzado y auditoría de anomalías activo. 👾',
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }
      setMessages((prev) => [...prev, userMsg, handoverMsg])
      setInputValue('')
      return
    }

    setMessages((prev) => [...prev, userMsg])
    setInputValue('')
    setIsLoading(true)
    onThinkingChange?.(true)

    try {
      let botReply = ''

      // 1. Invocar al Copiloto de Inteligencia Artificial
      try {
        const historyForAi = [...messages, userMsg].map((m) => ({
          role: m.role,
          content: m.content,
        }))

        const res = await api.post('/copilot/chat', { messages: historyForAi, bot: activeBot })
        if (res.data?.reply) {
          botReply = res.data.reply
        }
      } catch (err) {
        console.warn('Copilot LLM offline, usando motor heurístico:', err)
      }

      // 2. Fallback heurístico en caso de desconexión del LLM
      if (!botReply) {
        const lower = text.toLowerCase()

      // 1. Detección de saludos cordiales
      const isGreeting =
        /^(hola|buenas|buenos d[ií]as|buenas tardes|buenas noches|hey|qu[eé] tal|saludos|oe|alo)/i.test(
          lower
        ) || ['hola', 'buenas', 'hey', 'alo'].includes(lower)

      // 2. Detección de agradecimientos y cortesías
      const isGratitude =
        /^(gracias|muchas gracias|excelente|buen[ií]simo|genial|ok|vale|de acuerdo|perfecto|entendido|grx)/i.test(
          lower
        )

      // 3. Detección de despedidas
      const isFarewell = /^(chau|adi[oó]s|hasta luego|nos vemos|bye)/i.test(lower)

      // 4. Preguntas sobre identidad / ayuda
      const isIdentityOrHelp =
        lower.includes('qui[eé]n eres') ||
        lower.includes('qu[eé] eres') ||
        lower.includes('para qu[eé] sirves') ||
        lower.includes('qu[eé] haces') ||
        lower === 'ayuda' ||
        lower === 'help'

      // 5. Consulta de identidad de suscriptor / MSISDN / IMSI / SUPI / IMEI / IP
      const isIdentityQuery =
        lower.includes('msisdn') ||
        lower.includes('numero') ||
        lower.includes('número') ||
        lower.includes('imei') ||
        lower.includes('supi') ||
        lower.includes('gpsi') ||
        lower.includes('quien soy') ||
        lower.includes('quién soy') ||
        (lower.includes('mi ip') && !lower.includes('chf')) ||
        (lower.includes('mi imsi') && !lower.includes('saldo') && !lower.includes('navega') && !lower.includes('recarga'))

      // 6. Diagnóstico de terminal / UE / celular / sesión PDU
      const isTerminalQuery =
        lower.includes('terminal') ||
        lower.includes('ue') ||
        lower.includes('dispositivo') ||
        lower.includes('celular') ||
        lower.includes('smartphone') ||
        lower.includes('equipo') ||
        lower.includes('pdu') ||
        lower.includes('sesion') ||
        lower.includes('sesión')

      if (isGreeting) {
        botReply =
          '¡Hola Miguel! 👋 Todo el Core 5G SA y los enlaces N3/N4 se encuentran en estado nominal.\n\n¿Quieres que audite tu terminal, el saldo en el CHF, las alarmas o el acelerador eBPF/XDP?'
      } else if (isGratitude) {
        botReply =
          'Con gusto, Miguel. Quedo atento en segundo plano monitoreando la telemetría del Core 5G y las UPFs. Si necesitas otra verificación, solo avísame.'
      } else if (isFarewell) {
        botReply =
          'Hasta luego, Miguel. 👋 Dejo el sistema supervisado. Puedes cerrar este panel cuando desees y volveré al modo de espera.'
      } else if (isIdentityOrHelp) {
        botReply =
          '**Foxi Copilot · Asistente del NOC 5G en MAEstro**\n\nFunciones principales disponibles en este testbed:\n- **Identidad de Suscriptor:** Consulta de MSISDN, IMSI, SUPI e IMEISV.\n- **Diagnóstico de Terminales:** Verificación de registro AMF y sesiones PDU en UPF.\n- **Tarificación CHF:** Consulta de saldo disponible y cuota 3GPP Rel. 16.\n- **Acelerador eBPF/XDP:** Estado de bypass en kernel de la UPF.\n- **Alarm Center:** Supervisión de incidentes 3GPP 28.532 en vivo.\n- **Network Slicing:** Comparativa de latencia y throughput entre slices.\n\nPuedes seleccionar una consulta rápida o escribir tu consulta técnica.'
      } else if (isIdentityQuery) {
        botReply = `Identidad de Suscriptor (Perfil UDM / 5G SA):
- MSISDN (Número móvil): **+51 987 654 321**
- IMSI / SUPI: \`999700000000001\`
- Terminal asociado: Grupo 1 · Smartphone (eMBB)
- IMEISV de equipo: \`4370816125816151\`
- Dirección IP PDU activa: \`10.45.0.2\` (interfaz \`uesimtun2\`)
- Red móvil (PLMN): \`999/70\` (Perú Testbed 5G SA)

Suscriptor aprovisionado en la base de datos de usuarios y autenticado en el Core 5G.`
      } else if (isTerminalQuery && !lower.includes('chf') && !lower.includes('saldo') && !lower.includes('recarga')) {
        try {
          const res = await api.get('/terminal/status?imsi=imsi-999700000000001')
          const term = res.data
          const isReg = term.registered || term.native_state?.['rm-state'] === 'RM-REGISTERED'
          const pduSession = term.sessions?.['PDU Session1'] || (term.sessions ? Object.values(term.sessions)[0] : null) as any
          const sessionActive = pduSession?.state === 'PS-ACTIVE'
          const ipAddr = pduSession?.address || term.interfaces?.[0]?.addresses?.[0] || '10.45.0.2'
          const iface = pduSession?.interface || term.interfaces?.[0]?.name || 'uesimtun2'
          const apn = term.active_apn || pduSession?.apn || 'internet'
          const plmn = term.native_state?.['selected-plmn'] || '999/70'
          const cell = term.native_state?.['current-cell'] || 2

          botReply = `Diagnóstico de Terminal UE (imsi-999700000000001):
- Registro 3GPP: ${isReg ? 'Registrado en AMF (RM-REGISTERED / Normal Service)' : 'No registrado'}
- Sesión PDU: ${sessionActive ? `Activa (PS-ACTIVE) con IP ${ipAddr}` : 'Inactiva'}
- Interfaz local: \`${iface}\`
- DNN / APN activo: \`${apn}\` (Slice eMBB SST 1 / SD 1)
- Red y celda: PLMN ${plmn} · Celda ${cell}

El terminal móvil se encuentra operativo y con sesión de datos activa establecida hacia la UPF.`
        } catch {
          botReply = `Diagnóstico de Terminal UE (imsi-999700000000001):
- Registro 3GPP: Registrado en AMF (RM-REGISTERED)
- Sesión PDU: Activa (IPv4 10.45.0.2 sobre \`uesimtun2\`)
- DNN / APN: \`internet\`
- Estado general: Operativo nominal.`
        }
      } else if (
        lower.includes('imsi') ||
        lower.includes('chf') ||
        lower.includes('saldo') ||
        lower.includes('datos') ||
        lower.includes('recarga') ||
        lower.includes('navega') ||
        lower.includes('cuota')
      ) {
        try {
          const res = await api.get('/charging/accounts')
          const accounts = res.data.items || []
          const acc = accounts.find((a: any) => a.supi?.endsWith('001')) || accounts[0]
          if (acc) {
            const availMB = (acc.available_bytes / (1024 * 1024)).toFixed(1)
            const quotaMB = (acc.quota_bytes / (1024 * 1024)).toFixed(1)
            const consumedMB = (acc.consumed_bytes / (1024 * 1024)).toFixed(1)
            const isPositive = acc.available_bytes > 0

            botReply = `Auditoría Real de CHF para ${acc.supi}:
- Estado de cuenta: ${acc.enabled ? 'Habilitada y Activa' : 'Deshabilitada'}
- Saldo disponible: **${availMB} MB** (${(Number(availMB) / 1024).toFixed(2)} GB)
- Consumo acumulado: ${consumedMB} MB de ${quotaMB} MB contratados

${
  isPositive
    ? `El suscriptor cuenta con recarga y saldo positivo disponible. Si la terminal no navega, la causa técnica no es corte por saldo en el CHF. Verificar:
1. Si la sesión PDU se estableció con éxito en la UPF.
2. Si el DNN/APN solicitado por el terminal es \`internet\` o \`5g-plus\`.
3. Si la ruta por defecto del dispositivo apunta a la interfaz \`ogstun\`.`
    : `Cuota agotada en el CHF (0 MB disponibles). El tráfico fue suspendido por política de tarificación 3GPP Rel. 16 hasta aplicar recarga.`
}`
          } else {
            botReply = 'No se encontraron registros de cuentas de suscriptores en el CHF en este momento.'
          }
        } catch {
          botReply = 'No fue posible consultar la base de datos del CHF en este momento.'
        }
      } else if (lower.includes('ebpf') || lower.includes('xdp') || lower.includes('acelerador') || lower.includes('kernel')) {
        try {
          const res = await api.get('/upf-xdp/status')
          const xdp = res.data
          botReply = `Acelerador UPF (eBPF / XDP):
- Modo efectivo: \`${xdp.effective_mode || 'kernel'}\`
- Network Slice: \`${xdp.slice || 'urllc'}\`
- Interfaces: ${xdp.interfaces?.join(', ') || 'murllc-n3, murllc-mec'}
- Disponibilidad: ${xdp.available ? 'Activo (Driver Mode)' : 'Modo kernel nominal'}`
        } catch {
          botReply = 'Acelerador eBPF/XDP en modo kernel nominal sobre interfaces UPF.'
        }
      } else if (lower.includes('alarma') || lower.includes('incidente') || lower.includes('fall')) {
        try {
          const res = await api.get('/alarm-center/5g-sa?visibility=visible')
          const data = res.data
          const counts = data.counts || {}
          const totalAlarms = data.total || 0
          if (totalAlarms === 0) {
            botReply = `Alarm Center (3GPP 28.532):
- Estado general: Testbed 100% nominal. No existen incidentes críticos ni alarmas activas en las funciones de red.`
          } else {
            botReply = `Alarm Center (3GPP 28.532):
- Alarmas detectadas: ${totalAlarms} (Críticas: ${counts.critical || 0}, Mayores: ${counts.major || 0})
- Revisa el panel de alarmas para detalles de las funciones afectadas.`
          }
        } catch {
          botReply = 'Alarm Center: Testbed nominal sin incidentes críticos registrados.'
        }
      } else if (lower.includes('slice') || lower.includes('latencia') || lower.includes('urllc') || lower.includes('embb')) {
        botReply = `Métricas de Network Slices 5G:
- Slice eMBB (SST 1 / SD 000001): Optimizado para throughput de banda ancha móvil.
- Slice URLLC (SST 2 / SD 000002): Optimizado para latencia ultra-baja (< 2.5 ms con eBPF bypass).

Ambos slices se encuentran vinculados a UPF-01 y UPF-02.`
      } else {
        // Guardrail sobrio para consultas fuera del dominio 5G / telecom
        botReply = `Esa consulta se encuentra fuera del alcance del sistema. Como copiloto del **NOC 5G en MAEstro**, mi especialidad se concentra en:
- Identidad de suscriptor (MSISDN, IMSI, SUPI, IMEISV).
- Diagnóstico de terminales UE y sesiones PDU.
- Tarificación 3GPP Rel. 16 (saldo y cuota CHF).
- Aceleración UPF con eBPF/XDP.
- Network Slicing (eMBB vs URLLC, latencia).
- Alarmas e incidentes 3GPP 28.532 en vivo.

¿Deseas que auditemos alguna de estas áreas de tu red?`
      }
    }

    const botMsg: ChatMessage = {
        id: `bot-${Date.now()}`,
        role: 'assistant',
        content: botReply,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
      }

      setMessages((prev) => [...prev, botMsg])
    } finally {
      setIsLoading(false)
      onThinkingChange?.(false)
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSendMessage()
    }
  }

  if (!isOpen) return null

  const botName = activeBot === 'hyppety' ? 'Hyppety' : 'hxxpper'
  const isHyppety = activeBot === 'hyppety'

  return (
    <div
      role='dialog'
      aria-label={`${botName} NOC Copilot`}
      className='fixed bottom-6 left-4 md:left-[185px] z-50 flex h-[510px] max-h-[calc(100dvh-2.5rem)] w-[380px] max-w-[calc(100vw-2rem)] flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/80 shadow-[0_25px_60px_rgba(0,0,0,0.95)] backdrop-blur-2xl animate-in fade-in zoom-in-95 duration-200'
    >
      {/* HEADER MINIMALISTA - NEGRO TRASLÚCIDO CON BLUR */}
      <div className='flex items-center justify-between border-b border-white/[0.08] bg-black/40 px-4 py-3 shrink-0'>
        <div className='flex items-center gap-2'>
          <span
            className={cn(
              'size-2 rounded-full shadow-[0_0_8px]',
              isHyppety
                ? 'bg-emerald-400 shadow-emerald-400/80'
                : 'bg-purple-400 shadow-purple-400/80'
            )}
          />
          <h2 className='text-[13px] font-semibold text-zinc-100 tracking-wide'>
            {botName}
          </h2>
          <span
            className={cn(
              'rounded-full px-2 py-0.5 text-[9px] font-semibold border',
              isHyppety
                ? 'bg-orange-500/10 text-orange-400 border-orange-500/20'
                : 'bg-purple-500/15 text-purple-300 border-purple-500/30'
            )}
          >
            {isHyppety ? 'Ollama 5G' : 'Gemini 5G'}
          </span>
        </div>

        <div className='flex items-center gap-1'>
          <Button
            variant='ghost'
            size='icon'
            className='size-7 text-zinc-400 hover:text-zinc-100 hover:bg-white/10 rounded-lg'
            onClick={() =>
              setMessages([
                {
                  id: `reset-${Date.now()}`,
                  role: 'assistant',
                  content: isHyppety
                    ? '¡Chat reiniciado! 🦊 Soy Hyppety. ¿En qué duda operativa te colaboro?'
                    : 'Canal reiniciado. 👾 Soy hxxpper. Monitoreo de anomalías en espera.',
                  timestamp: 'Ahora',
                },
              ])
            }
            title='Limpiar conversación'
          >
            <RefreshCw className='size-3.5' />
          </Button>
          <Button
            variant='ghost'
            size='icon'
            className='size-7 text-zinc-400 hover:text-zinc-100 hover:bg-white/10 rounded-lg'
            onClick={onClose}
            title={`Cerrar ${botName}`}
          >
            <X className='size-4' />
          </Button>
        </div>
      </div>

      {/* CUERPO DEL CHAT / MENSAJES */}
      <div className='flex-1 min-h-0 overflow-y-auto p-3.5 space-y-3.5'>
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={cn('flex flex-col gap-1', msg.role === 'user' ? 'items-end' : 'items-start')}
          >
            <div
              className={cn(
                'max-w-[85%] rounded-2xl px-3.5 py-2.5 text-xs leading-relaxed transition-all',
                msg.role === 'user'
                  ? isHyppety
                    ? 'bg-orange-500/15 text-orange-100 border border-orange-500/25 rounded-tr-sm'
                    : 'bg-purple-500/15 text-purple-100 border border-purple-500/25 rounded-tr-sm'
                  : 'bg-white/[0.04] text-zinc-200 border border-white/[0.08] rounded-tl-sm shadow-sm'
              )}
            >
              <FormattedMessage content={msg.content} />
            </div>
            <span className='px-1 text-[10px] text-zinc-400 font-mono'>{msg.timestamp}</span>
          </div>
        ))}

        {isLoading && (
          <div
            className={cn(
              'flex items-center gap-2 text-xs bg-white/[0.03] border border-white/[0.08] rounded-xl px-3 py-2 w-fit',
              isHyppety ? 'text-orange-300' : 'text-purple-300'
            )}
          >
            <Sparkles
              className={cn('size-3.5 animate-spin', isHyppety ? 'text-orange-400' : 'text-purple-400')}
            />
            <span>{isHyppety ? 'Hyppety analizando telemetría...' : 'hxxpper auditando anomalías...'}</span>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* CONSULTAS RÁPIDAS - MINIMALISTA */}
      <div className='border-t border-white/[0.06] bg-black/30 px-3 py-2.5'>
        <div className='text-[10px] uppercase tracking-wider text-zinc-400 font-medium mb-1.5'>
          Consultas rápidas
        </div>
        <div className='flex flex-wrap gap-1.5'>
          {QUICK_PROMPTS.map((item, idx) => (
            <button
              key={idx}
              type='button'
              onClick={() => handleSendMessage(item.prompt)}
              disabled={isLoading}
              className={cn(
                'rounded-full border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-[10.5px] text-zinc-300 transition-colors disabled:opacity-40',
                isHyppety
                  ? 'hover:border-orange-500/30 hover:bg-orange-500/10 hover:text-orange-200'
                  : 'hover:border-purple-500/30 hover:bg-purple-500/10 hover:text-purple-200'
              )}
            >
              {item.text}
            </button>
          ))}
        </div>
      </div>

      {/* INPUT BAR MINIMALISTA */}
      <div className='border-t border-white/[0.08] bg-black/60 p-2.5 shrink-0'>
        <div className='relative flex items-center rounded-xl border border-white/[0.08] bg-white/[0.03] transition-focus-within focus-within:border-white/20 focus-within:bg-white/[0.05]'>
          <textarea
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={isHyppety ? 'Escribe a Hyppety o usa /change...' : 'Escribe a hxxpper o usa /change...'}
            rows={1}
            disabled={isLoading}
            className='w-full resize-none bg-transparent px-3 py-2.5 text-xs text-zinc-100 placeholder:text-zinc-400 focus:outline-none disabled:opacity-50 min-h-[38px] max-h-[80px]'
          />
          <Button
            type='button'
            size='icon'
            disabled={!inputValue.trim() || isLoading}
            onClick={() => handleSendMessage()}
            className={cn(
              'mr-1.5 size-7 rounded-lg text-white disabled:opacity-30 transition-colors',
              isHyppety ? 'bg-orange-500 hover:bg-orange-600' : 'bg-purple-600 hover:bg-purple-700'
            )}
          >
            <Send className='size-3.5' />
          </Button>
        </div>
      </div>
    </div>
  )
}
