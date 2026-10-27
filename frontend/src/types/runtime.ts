import type { Operation } from './mission'

export interface UsageSummary {
  available: boolean
  complete: boolean
  counts_complete?: boolean
  mode?: 'demo' | 'live'
  logical_calls: number | null
  tool_calls?: number
  network_attempts: number | null
  known_network_attempts: number
  input_tokens: number | null
  output_tokens: number | null
  known_input_tokens: number
  known_output_tokens: number
  usage_known_calls: number
  usage_unknown_calls?: number | null
  unknown_attempts?: number | null
  failed_calls?: number
  interrupted_calls?: number
}
export interface TimingSummary {
  history_complete: boolean
  queue_seconds: number | null
  execution_seconds: number | null
  user_wait_seconds: number | null
  wall_seconds: number | null
  known_queue_seconds: number
  known_execution_seconds: number
  known_user_wait_seconds: number
  live: boolean
}
export interface RuntimeOperation extends Operation {
  usage: UsageSummary
  timing: TimingSummary
  error?: string | null
}
export interface RuntimeView {
  mission_id: string
  mission_sequence: number
  read_at: string
  workflow_operation_id: string | null
  operations: RuntimeOperation[]
  totals: UsageSummary
  report_telemetry: Record<string, number | null>
}
