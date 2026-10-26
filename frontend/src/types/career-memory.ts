import type { Recommendation } from './recommendation'

export interface MemoryTime {
  time_domain: 'unknown' | 'real' | 'game' | 'match'
  precision: 'unknown' | 'unspecified' | 'instant' | 'day' | 'month' | 'week'
  effective_at: string | null
  valid_window: string | null
}
export interface CareerMemory {
  memory_id: string
  revision: number
  schema_version: number
  context: Recommendation['context']
  kind: 'goal' | 'preference' | 'recommendation' | 'decision' | 'execution' | 'assessment'
  content: string
  projection: { status?: string; reason?: string; source_type?: string; truncated?: boolean }
  source: {
    type: 'user_confirmed' | 'model_candidate' | 'e_record'
    mission_id: string
    request_id: string | null
    candidate_id: string | null
    confirmed_candidate_revision: number | null
    event_id: string | null
    event_type: string | null
    object_id: string | null
    object_revision: number | null
    object_hash: string | null
    recommendation_id: string | null
    recommendation_revision: number | null
    recommendation_source: Recommendation['source'] | null
  }
  recorded_at: string
  effective_time: MemoryTime
  corrects_revision: number | null
  supersedes_memory_id: string | null
  change_reason: string
  status: 'active' | 'candidate' | 'superseded' | 'retracted'
  usage: 'current' | 'historical_only' | 'unconfirmed'
  status_reason: string
  source_checked: boolean
  recommendation_validity: Recommendation['validity'] | null
  limitations: string[]
}
export interface CareerMemoryList {
  context: Recommendation['context']
  items: CareerMemory[]
}
export interface FrozenMemory {
  policy_version?: string
  content_hash?: string
  as_of?: string
  enabled?: boolean
  context?: Recommendation['context']
  items?: (Pick<
    CareerMemory,
    | 'memory_id'
    | 'revision'
    | 'kind'
    | 'content'
    | 'source'
    | 'recorded_at'
    | 'effective_time'
    | 'limitations'
  > & {
    content_hash: string
    projection: {
      before?: number | null
      after?: number | null
      delta?: number | null
      baseline_samples?: number
      followup_samples?: number
      reason?: string
    }
    usage: string
    capabilities: string[]
    truncated: boolean
  })[]
  omitted?: { reason: string; count: number }[]
  limitations?: string[]
}
export interface MemoryWriteRequest {
  context: Recommendation['context']
  request_id: string
  operation: 'create' | 'correct' | 'retract' | 'confirm_candidate'
  confirmed: true
  kind: 'goal' | 'preference'
  content: string
  effective_time: MemoryTime
  memory_id: string | null
  expected_revision: number | null
  reason: string
}
