import type { Mission } from './mission'

export interface Recommendation {
  schema_version: number
  recommendation_id: string
  revision: number
  context: { career_id: string; branch_id: string; player_id: string }
  source: {
    mission_id: string
    subtask_id: string
    capability: 'skill_training'
    result_version: number
    payload_position: string
    payload_hash: string
    input_fingerprint: string
    review_plan_version: number
    reviewed_result_version: number
    mapping_version: string
  }
  content: {
    title: string
    text: string
    expected_goal: string
    basis: string
    limitations: string[]
  }
  applicability: {
    input_reference: NonNullable<Mission['input_reference']>
    conditions: string[]
    game_versions: string[]
    game_mode: string | null
    valid_window: string | null
    window_reason: string
  }
  execution_support: {
    status: 'pending_verification'
    reason: string
    evidence_references: string[]
  }
  evaluation_spec: {
    status: 'undefined'
    baseline_reference: NonNullable<Mission['input_reference']>
    metrics: string[]
    observation_window: string | null
    reason: string
  }
  validity: 'current' | 'needs_reassessment' | 'superseded' | 'withdrawn'
  validity_reason: string
  checked_state_version: string | null
  created_at: string
}

export interface RecommendationList {
  mission_id: string
  availability: 'AVAILABLE' | 'UNAVAILABLE' | 'NOT_PROJECTED'
  reason: string
  items: Recommendation[]
}
