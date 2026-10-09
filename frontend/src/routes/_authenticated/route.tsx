import { createFileRoute, redirect } from '@tanstack/react-router'
import { useAuthStore } from '@/stores/auth-store'
import { AuthenticatedLayout } from '@/components/layout/authenticated-layout'

export const Route = createFileRoute('/_authenticated')({
  beforeLoad: ({ location }) => {
    if (!useAuthStore.getState().auth.accessToken) {
      const redirectPath =
        location.pathname && !location.pathname.includes('/sign-in')
          ? location.pathname
          : '/'
      throw redirect({ to: '/sign-in', search: { redirect: redirectPath } })
    }
  },
  component: AuthenticatedLayout,
})
