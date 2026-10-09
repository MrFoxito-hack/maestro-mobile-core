import { createFileRoute } from '@tanstack/react-router'
import { VerticalsPage } from '@/features/verticals'

export const Route = createFileRoute('/_authenticated/services/verticals')({
  component: VerticalsPage,
})
