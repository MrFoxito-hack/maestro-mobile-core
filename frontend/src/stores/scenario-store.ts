import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type ScenarioId = '5g-sa' | '4g-epc'
export const useScenarioStore = create<{
  scenario: ScenarioId
  setScenario: (scenario: ScenarioId) => void
}>()(
  persist(
    (set) => ({
      scenario: '5g-sa',
      setScenario: (scenario) => set({ scenario }),
    }),
    {
      name: 'ems-scenario',
      partialize: ({ scenario }) => ({ scenario }),
      onRehydrateStorage: () => (state) => {
        if (state && state.scenario !== '5g-sa') {
          state.scenario = '5g-sa'
        }
      },
    }
  )
)
