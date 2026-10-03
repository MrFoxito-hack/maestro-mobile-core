import { createFileRoute } from '@tanstack/react-router'
import { NwdafPage } from '@/features/nwdaf'

export const Route = createFileRoute('/_authenticated/nwdaf/')({
  component: NwdafPage,
})
