import { createFileRoute } from '@tanstack/react-router'
import { ChargingPage } from '@/features/charging'

export const Route = createFileRoute('/_authenticated/charging/')({
  component: ChargingPage,
})
