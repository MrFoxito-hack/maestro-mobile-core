import { Link, useRouterState } from '@tanstack/react-router'
import { LayoutDashboard } from 'lucide-react'
import { useAuthStore } from '@/stores/auth-store'
import { cn } from '@/lib/utils'
import { ProfileDropdown } from '@/components/profile-dropdown'
import { sidebarData } from './data/sidebar-data'
import { TopNavDropdown } from './top-nav-dropdown'

export function TopNavigation() {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const user = useAuthStore((state) => state.auth.user)
  const isStudent = user?.role === 'student'

  const groups = sidebarData.navGroups.map((group) => ({
    ...group,
    items: group.items.filter((item) => {
      if (
        isStudent &&
        (item.url === '/audit' ||
          item.url === '/charging' ||
          item.url === '/nwdaf')
      )
        return false
      if (group.title === 'Operación' && item.url === '/') return false
      return true
    }),
  }))

  const isResumenActive = pathname === '/'

  return (
    <header className='sticky top-0 z-40 flex flex-wrap items-center gap-x-4 border-b border-white/[0.08] bg-black/95 backdrop-blur-md px-3 md:px-4 xl:flex-nowrap'>
      <Link
        to='/'
        aria-label='MAEstro · Inicio'
        className='flex h-14 shrink-0 items-center gap-[3.8px] transition-opacity hover:opacity-90'
      >
        <img
          src='/images/logo_final.png'
          alt='MAEstro'
          className='h-[15px] w-auto object-contain shrink-0'
        />
        <span className='hidden text-[19.3px] font-bold tracking-tight text-white leading-none sm:inline-flex sm:items-center dark:text-foreground'>
          <span>MAE</span>
          <span className='font-semibold'>stro</span>
        </span>
      </Link>
      <nav
        aria-label='Navegación principal'
        className='order-3 -mx-1 flex w-full min-w-0 items-center overflow-x-auto px-1 pb-2 xl:order-none xl:mx-0 xl:h-14 xl:flex-1 xl:pb-0'
      >
        <div className='flex shrink-0 items-center gap-1 xl:mx-auto'>
          {/* Direct link to Resumen */}
          <Link
            to='/'
            aria-current={isResumenActive ? 'page' : undefined}
            className={cn(
              'group flex shrink-0 cursor-pointer items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] font-medium transition-all focus-visible:ring-2 focus-visible:outline-none',
              isResumenActive
                ? 'bg-white/20 font-semibold text-white shadow-xs dark:bg-accent dark:text-accent-foreground'
                : 'text-white/75 hover:bg-white/10 hover:text-white dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground',
              'focus-visible:ring-white/40 dark:focus-visible:ring-ring'
            )}
          >
            <LayoutDashboard className='size-3.5 opacity-80 group-hover:opacity-100' />
            <span>Resumen</span>
          </Link>

          {/* Professional hover+click dropdowns for each domain */}
          {groups.map((group) => (
            <TopNavDropdown
              key={group.title}
              title={group.title}
              items={group.items}
            />
          ))}
        </div>
      </nav>
      <div className='ml-auto flex h-14 shrink-0 items-center gap-2'>
        <ProfileDropdown />
      </div>
    </header>
  )
}
