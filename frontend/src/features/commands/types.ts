export type ParameterOption = {
  value: string | number
  label: string
}

export type OperationParameter = {
  id: string
  label: string
  type: 'select' | 'number' | 'text'
  required?: boolean
  default?: string | number
  description?: string
  options?: ParameterOption[]
  minimum?: number
  maximum?: number
  integer?: boolean
  pattern?: string
  secret?: boolean
}

export type OperationDefinition = {
  id: string
  label: string
  description: string
  category: string
  mutating: boolean
  allowed: boolean
  parameters: OperationParameter[]
}

export type ComponentOperations = {
  id: string
  label: string
  kind?: string
  node_id?: string
  unit: string
  status?: string
  interfaces?: string[]
  targets?: string[]
  operations: OperationDefinition[]
}

export type OperationsCatalog = {
  scenario_id: string
  scenario_name: string
  execution_mode: 'simulated' | 'local' | 'remote'
  components: ComponentOperations[]
  native_discovery: {
    available: boolean
    message: string | null
  }
}

export type OperationExecutePayload = {
  scenario_id: string
  component_id: string
  operation_id: string
  parameters: Record<string, unknown>
  original_command?: string
}

export type OperationResult = {
  id: string
  scenario_id: string
  testbed_id: string
  component_id: string
  component_label: string
  operation_id: string
  operation_label: string
  category: string
  mutating: boolean
  username: string
  role: string
  parameters: Record<string, unknown>
  status: 'success' | 'failed'
  source: string
  output: string
  data: unknown
  error: string | null
  started_at: string
  completed_at: string
  duration_ms: number
}

export type OperationRun = OperationResult
