import { Outlet } from '@tanstack/react-router'
import { useScenarioStore } from '@/stores/scenario-store'
import { LayoutProvider } from '@/context/layout-provider'
import { SearchProvider } from '@/context/search-provider'
import { TopNavigation } from '@/components/layout/top-navigation'
import { SkipToMain } from '@/components/skip-to-main'
import { SmartphoneTerminal } from '@/features/terminal/smartphone-terminal'

type AuthenticatedLayoutProps = {
  children?: React.ReactNode
}

export function AuthenticatedLayout({ children }: AuthenticatedLayoutProps) {
  const scenario = useScenarioStore((state) => state.scenario)
  return (
    <SearchProvider>
      <LayoutProvider>
        <div className='min-h-svh w-full min-w-0 bg-slate-50/60 dark:bg-background'>
          <SkipToMain />
          <TopNavigation />
          <div key={scenario} className='@container/content min-w-0'>
            {children ?? <Outlet />}
          </div>
          <SmartphoneTerminal key={scenario} />
        </div>
      </LayoutProvider>
    </SearchProvider>
  )
}
