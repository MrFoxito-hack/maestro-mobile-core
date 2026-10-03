import { createFileRoute } from '@tanstack/react-router'
import { TracesPage } from '@/features/traces'

export const Route = createFileRoute('/_authenticated/traces/node')({
  component: () => <TracesPage view='node' />,
})
