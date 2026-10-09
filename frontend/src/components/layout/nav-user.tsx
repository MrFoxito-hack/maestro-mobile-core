import { ChevronsUpDown, LogOut } from 'lucide-react'
import { useAuthStore } from '@/stores/auth-store'
import useDialogState from '@/hooks/use-dialog-state'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from '@/components/ui/sidebar'
import { SignOutDialog } from '@/components/sign-out-dialog'

type NavUserProps = { user: { name: string; email: string; avatar: string } }
export function NavUser({ user: defaultUser }: NavUserProps) {
  const { isMobile } = useSidebar()
  const [open, setOpen] = useDialogState()
  const user = useAuthStore((state) => state.auth.user)
  const name = user?.username ?? defaultUser.name
  const detail = user
    ? `${user.role} · ${user.testbed ?? 'compartido'}`
    : defaultUser.email
  const initials = name.slice(0, 2).toUpperCase()

  return (
    <>
      <SidebarMenu>
        <SidebarMenuItem>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <SidebarMenuButton
                size='lg'
                className='data-[state=open]:bg-sidebar-accent'
              >
                <Avatar className='size-8 rounded-lg'>
                  <AvatarFallback className='rounded-lg'>
                    {initials}
                  </AvatarFallback>
                </Avatar>
                <div className='grid flex-1 text-start text-sm leading-tight'>
                  <span className='truncate font-semibold'>{name}</span>
                  <span className='truncate text-xs'>{detail}</span>
                </div>
                <ChevronsUpDown className='ms-auto size-4' />
              </SidebarMenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              className='min-w-56'
              side={isMobile ? 'bottom' : 'right'}
              align='end'
              sideOffset={4}
            >
              <DropdownMenuLabel>
                <div className='font-medium'>{name}</div>
                <div className='text-xs font-normal text-muted-foreground'>
                  {detail}
                </div>
              </DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                variant='destructive'
                onClick={() => setOpen(true)}
              >
                <LogOut />
                Cerrar sesión
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </SidebarMenuItem>
      </SidebarMenu>
      <SignOutDialog open={!!open} onOpenChange={setOpen} />
    </>
  )
}
