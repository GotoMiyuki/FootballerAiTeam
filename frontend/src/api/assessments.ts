import { request } from './client'
import type { Recommendation } from '../types/recommendation'
import type {
  ComparisonView,
  ComparisonRequest,
  ComparisonPlan,
  Assessment,
  AssessmentRequest,
} from '../types/assessment'
const path = (item: Recommendation) =>
  `/missions/${encodeURIComponent(item.source.mission_id)}/recommendations/${encodeURIComponent(item.recommendation_id)}`
export const getComparison = (item: Recommendation) =>
  request<ComparisonView>(`${path(item)}/comparison`)
export const createComparison = (item: Recommendation, body: ComparisonRequest) =>
  request<{ record: ComparisonPlan; created: boolean }>(`${path(item)}/comparison-plans`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
export const evaluateComparison = (item: Recommendation, planId: string, body: AssessmentRequest) =>
  request<{ record: Assessment; created: boolean }>(
    `${path(item)}/comparison-plans/${encodeURIComponent(planId)}/assessments`,
    { method: 'POST', body: JSON.stringify(body) },
  )
