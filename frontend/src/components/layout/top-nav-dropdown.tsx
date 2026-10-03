import * as React from 'react'
import { Link, useRouterState } from '@tanstack/react-router'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { type NavItem } from './types'

type TopNavDropdownProps = {
  title: string
  items: NavItem[]
  className?: string
}

export function TopNavDropdown({ title, items, className }: TopNavDropdownProps) {
  const [open, setOpen] = React.useState(false)
  const timeoutRef = React.useRef<ReturnType<typeof setTimeout> | null>(null)

  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })

  // Check if any subitem matches current route
  const isGroupActive = items.some((item) => {
    if (item.url) {
      return item.url === '/'
        ? pathname === '/'
        : pathname === item.url || pathname.startsWith(`${item.url}/`)
    }
    return item.items?.some((sub) =>
      sub.url === '/'
        ? pathname === '/'
        : pathname === sub.url || pathname.startsWith(`${sub.url}/`)
    )
  })

  const handleMouseEnter = () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current)
      timeoutRef.current = null
    }
    setOpen(true)
  }

  const handleMouseLeave = () => {
    timeoutRef.current = setTimeout(() => {
      setOpen(false)
    }, 130)
  }

  React.useEffect(() => {
    return () => {
      if (timeoutRef.current) clearTimeout(timeoutRef.current)
    }
  }, [])

  return (
    <div
      className='relative inline-block'
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <DropdownMenu open={open} onOpenChange={setOpen} modal={false}>
        <DropdownMenuTrigger asChild>
          <button
            type='button'
            className={cn(
              'group flex shrink-0 items-center gap-1.5 rounded-md px-3 py-1.5 text-[13px] font-medium transition-all focus-visible:outline-none focus-visible:ring-2 cursor-pointer',
              isGroupActive
                ? 'bg-white/20 text-white shadow-xs dark:bg-accent dark:text-accent-foreground font-semibold'
                : 'text-white/75 hover:bg-white/10 hover:text-white dark:text-muted-foreground dark:hover:bg-muted dark:hover:text-foreground',
              'focus-visible:ring-white/40 dark:focus-visible:ring-ring',
              className
            )}
            aria-expanded={open}
          >
            <span>{title}</span>
            <ChevronDown
              className={cn(
                'size-3.5 opacity-70 transition-transform duration-200 group-hover:opacity-100',
                open && 'rotate-180 opacity-100'
              )}
            />
          </button>
        </DropdownMenuTrigger>

        <DropdownMenuContent
          side='bottom'
          align='start'
          sideOffset={8}
          className='z-50 min-w-[230px] rounded-xl border border-border/80 bg-popover/95 p-1.5 shadow-2xl backdrop-blur-md animate-in fade-in-0 zoom-in-95'
          onMouseEnter={handleMouseEnter}
          onMouseLeave={handleMouseLeave}
        >
          <DropdownMenuLabel className='px-2.5 pt-1.5 pb-1 text-[11px] font-semibold tracking-wider text-muted-foreground uppercase'>
            {title}
          </DropdownMenuLabel>
          <DropdownMenuSeparator className='mb-1 opacity-60' />

          <div className='flex flex-col gap-0.5'>
            {items.map((item) => {
              if (!item.url) return null
              const isItemActive =
                item.url === '/'
                  ? pathname === '/'
                  : pathname === item.url || pathname.startsWith(`${item.url}/`)

              const Icon = item.icon

              return (
                <DropdownMenuItem key={item.url} asChild className='p-0 focus:bg-transparent'>
                  <Link
                    to={item.url}
                    onClick={() => setOpen(false)}
                    className={cn(
                      'group relative flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors outline-none select-none cursor-pointer',
                      isItemActive
                        ? 'bg-accent/80 text-accent-foreground font-medium'
                        : 'hover:bg-muted/60 text-foreground'
                    )}
                  >
                    {Icon && (
                      <div
                        className={cn(
                          'flex size-7 shrink-0 items-center justify-center rounded-md border transition-transform duration-150 group-hover:scale-105',
                          isItemActive
                            ? 'border-primary/40 bg-primary/15 text-primary'
                            : 'border-border/50 bg-background/80 shadow-2xs',
                          item.color
                        )}
                      >
                        <Icon className='size-3.5' />
                      </div>
                    )}
                    <span
                      className={cn(
                        'flex-1 truncate text-[13px]',
                        isItemActive ? 'font-semibold text-primary' : 'font-medium'
                      )}
                    >
                      {item.title}
                    </span>
                    {item.badge && (
                      <span className='rounded-full bg-primary/15 px-1.5 py-0.5 text-[10px] font-bold leading-none text-primary uppercase tracking-wide'>
                        {item.badge}
                      </span>
                    )}
                    {isItemActive && (
                      <div className='size-1.5 shrink-0 rounded-full bg-primary' />
                    )}
                  </Link>
                </DropdownMenuItem>
              )
            })}
          </div>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
