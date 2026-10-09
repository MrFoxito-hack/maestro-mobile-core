import { createFileRoute } from '@tanstack/react-router'
import { FaultLabPage } from '@/features/diagnostics/fault-lab'

export const Route = createFileRoute('/_authenticated/diagnostico/fallas')({
  component: FaultLabPage,
})
