import { createFileRoute } from '@tanstack/react-router'
import { TracesPage } from '@/features/traces'

export const Route = createFileRoute('/_authenticated/traces/e2e')({
  component: () => <TracesPage view='e2e' />,
})
