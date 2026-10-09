import { useState, useEffect } from 'react'
import { FoxiChatPanel } from './foxi-chat-panel'
import { useMascotStore } from '@/stores/mascot-store'

type HyppetyAnim = 'idle' | 'salsa' | 'moonwalk' | 'waving' | 'looking_around'
type HxxpperAnim = 'idle' | 'jog' | 'death' | 'thriller' | 'gangnam' | 'sad'

const HYPPETY_MODELS: Record<HyppetyAnim, { model: string; label: string }> = {
  idle: {
    model: '/models/fox_idle.glb',
    label: 'Vigilancia 5G',
  },
  salsa: {
    model: '/models/fox_salsa.glb',
    label: 'Salsa',
  },
  moonwalk: {
    model: '/models/fox_moonwalk.glb',
    label: 'Moonwalk',
  },
  waving: {
    model: '/models/fox_waving.glb',
    label: 'Saludo',
  },
  looking_around: {
    model: '/models/fox_looking_around.glb',
    label: 'Pensando / Analizando',
  },
}

const HXXPPER_MODELS: Record<HxxpperAnim, { model: string; label: string }> = {
  idle: {
    model: '/models/hxxpper_idle.glb',
    label: 'Vigilancia Oscura',
  },
  jog: {
    model: '/models/hxxpper_jog.glb',
    label: 'Patrullaje / Trote',
  },
  death: {
    model: '/models/hxxpper_death.glb',
    label: 'Falla Crítica',
  },
  thriller: {
    model: '/models/hxxpper_thriller.glb',
    label: 'Thriller Dance',
  },
  gangnam: {
    model: '/models/hxxpper_gangnam.glb',
    label: 'Gangnam Style Dance',
  },
  sad: {
    model: '/models/hxxpper_sad.glb',
    label: 'Auditoría / Diagnóstico',
  },
}

export function FoxiMascot() {
  const [isChatOpen, setIsChatOpen] = useState(false)
  const { activeBot } = useMascotStore()

  // Estados de animación por mascota
  const [hyppetyAnim, setHyppetyAnim] = useState<HyppetyAnim>('waving')
  const [hxxpperAnim, setHxxpperAnim] = useState<HxxpperAnim>('idle')

  // Sincronizar animación base cuando cambia la mascota activa o el estado del chat
  useEffect(() => {
    if (activeBot === 'hyppety') {
      setHyppetyAnim(isChatOpen ? 'salsa' : 'waving')
    } else {
      setHxxpperAnim(isChatOpen ? 'gangnam' : 'idle')
    }
  }, [activeBot, isChatOpen])

  const handleClick = () => {
    setIsChatOpen((prev) => {
      const nextState = !prev
      if (activeBot === 'hyppety') {
        setHyppetyAnim(nextState ? 'salsa' : 'waving')
      } else {
        setHxxpperAnim(nextState ? 'gangnam' : 'idle')
      }
      return nextState
    })
  }

  const handleCloseChat = () => {
    setIsChatOpen(false)
    if (activeBot === 'hyppety') {
      setHyppetyAnim('waving')
    } else {
      setHxxpperAnim('idle')
    }
  }

  const handleThinkingChange = (isThinking: boolean) => {
    if (activeBot === 'hyppety') {
      setHyppetyAnim(isThinking ? 'looking_around' : 'salsa')
    } else {
      setHxxpperAnim(isThinking ? 'sad' : 'gangnam')
    }
  }

  const currentModel =
    activeBot === 'hyppety'
      ? HYPPETY_MODELS[hyppetyAnim].model
      : HXXPPER_MODELS[hxxpperAnim].model

  const botDisplayName = activeBot === 'hyppety' ? 'Hyppety' : 'hxxpper'
  const botEngine =
    activeBot === 'hyppety' ? 'Ollama Local RTX 5070' : 'Gemini Cloud Engine'

  return (
    <>
      {/* PANEL FLOTANTE DE CHAT (MINIMALISTA) */}
      <FoxiChatPanel
        isOpen={isChatOpen}
        onClose={handleCloseChat}
        onThinkingChange={handleThinkingChange}
      />

      {/* CONTENEDOR 3D DE LA MASCOTA EN ESQUINA INFERIOR IZQUIERDA */}
      <div
        aria-label={`${botDisplayName} Mascota MAEstro 5G`}
        className='fixed bottom-0 left-1 z-40 pointer-events-none select-none flex flex-col items-center'
        style={{
          width: '175px',
          height: '255px',
        }}
      >
        {/* Visor 3D de la Mascota - Totalmente transparente sin fondo */}
        <div
          className='pointer-events-auto relative w-full h-full cursor-pointer transition-transform duration-200 hover:scale-105 active:scale-95'
          onClick={handleClick}
          title={
            isChatOpen
              ? `Cerrar chat de ${botDisplayName}`
              : `Abrir ${botDisplayName} Copilot (${botEngine})`
          }
        >
          <model-viewer
            key={`${activeBot}-${currentModel}`}
            src={currentModel}
            autoplay
            exposure='1.25'
            shadow-intensity='0.8'
            shadow-softness='0.8'
            camera-orbit='0deg 80deg 75%'
            min-camera-orbit='auto auto 50%'
            max-camera-orbit='auto auto 160%'
            camera-target='0m 0.72m 0m'
            interaction-prompt='none'
            alt={`${botDisplayName} Mascota 3D`}
            style={{
              width: '100%',
              height: '100%',
              background: 'transparent',
              outline: 'none',
            }}
          />
        </div>
      </div>
    </>
  )
}
