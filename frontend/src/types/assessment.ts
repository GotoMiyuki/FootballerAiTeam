import type { Recommendation } from './recommendation'
export type Metric = 'speed' | 'finishing' | 'rating' | 'goals' | 'assists' | 'minutes_played'
export interface ComparisonRequest {
  context: Recommendation['context']
  request_id: string
  confirmed: true
  recommendation_revision: number
  metric: Metric
  baseline_count: number
  minimum_samples: number
  window: {
    kind: 'next_matches' | 'game_dates'
    count: number | null
    start: string | null
    end: string | null
  }
  target_value: number | null
  target_operator: 'gte' | 'lte'
}
export interface ComparisonPlan {
  plan_id: string
  recommendation_id: string
  recommendation_revision: number
  context: Recommendation['context']
  source: Recommendation['source']
  baseline_reference: Recommendation['applicability']['input_reference']
  anchor_at: string
  specification: ComparisonRequest
  metric_definition: { label: string; unit: string; scale: string; method: string }
  baseline_observations: ObservationReference[]
  created_at: string
  algorithm_version: string
}
export interface ObservationReference {
  key: string
  observation_id: string
  provider: string
  source_type: string
  source_record_id: string | null
  game_version: string | null
  adapter_version: string | null
  semantic_hash: string
  effective_at: string | null
  time_domain: string
  time_precision: string
  value: number | null
}
export interface Assessment {
  assessment_id: string
  revision: number
  plan_id: string
  recommendation_id: string
  created_at: string
  corrects_revision: number | null
  execution_requested_id: string | null
  signature: string
  validity: 'current' | 'superseded' | 'unavailable'
  validity_reason: string
  result: {
    evidence_status: 'COMPLETE' | 'INSUFFICIENT' | 'INCOMPARABLE'
    goal_status: 'MET' | 'NOT_MET' | 'UNDETERMINED'
    before: number | null
    after: number | null
    delta: number | null
    baseline_samples: number
    followup_samples: number
    baseline_missing: number
    followup_missing: number
    window_matches: number
    window_complete: boolean
    baseline_observations: ObservationReference[]
    followup_observations: ObservationReference[]
    missing: string[]
    exclusions: Record<string, number>
    unit: string
    scale: string
    method: string
    reason: string
    limitations: string[]
    execution: {
      requested_id: string | null
      execution_id: string | null
      revision: number | null
      status: string
      source_type: string | null
    }
  }
}
export interface AssessmentRequest {
  context: Recommendation['context']
  request_id: string
  expected_revision: number
  execution_id: string | null
}
export interface ComparisonView {
  supported: boolean
  reason: string
  plans: ComparisonPlan[]
  assessments: Assessment[]
}
