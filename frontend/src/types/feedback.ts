import type { Assessment } from './assessment'
import type { Recommendation } from './recommendation'
export interface CareerFeedback {
  content_hash: string
  parent_mission_id: string
  recommendation: {
    recommendation_id: string
    revision: number
    content: Recommendation['content']
    validity_at_submission: string
  }
  assessment: {
    assessment_id: string
    revision: number
    result: Assessment['result']
    latest_input_differs_from_assessment: boolean
  } | null
  decision: { decision_id: string; revision: number; status: string } | null
  executions: { execution_id: string; revision: number; status: string; actual_action: string }[]
  limitations: string[]
}
