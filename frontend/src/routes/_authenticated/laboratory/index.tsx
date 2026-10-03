import { createFileRoute } from '@tanstack/react-router'
import { LaboratoryPage } from '@/features/laboratory'

export const Route = createFileRoute('/_authenticated/laboratory/')({
  component: LaboratoryPage,
})
