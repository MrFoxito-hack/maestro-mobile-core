import { create } from 'zustand'

export type MascotBot = 'hyppety' | 'hxxpper'

interface MascotState {
  activeBot: MascotBot
  setActiveBot: (bot: MascotBot) => void
  toggleBot: () => MascotBot
}

export const useMascotStore = create<MascotState>((set, get) => ({
  activeBot:
    typeof window !== 'undefined'
      ? (localStorage.getItem('maestro_active_bot') as MascotBot) || 'hyppety'
      : 'hyppety',
  setActiveBot: (bot) => {
    if (typeof window !== 'undefined') {
      localStorage.setItem('maestro_active_bot', bot)
    }
    set({ activeBot: bot })
  },
  toggleBot: () => {
    const next = get().activeBot === 'hyppety' ? 'hxxpper' : 'hyppety'
    if (typeof window !== 'undefined') {
      localStorage.setItem('maestro_active_bot', next)
    }
    set({ activeBot: next })
    return next
  },
}))
