import { request } from './client'
import type { Mission, Operation } from '../types/mission'
import type { CareerFeedback } from '../types/feedback'
import type {
  RecommendationList,
  Recommendation,
  RecommendationActivity,
  DecisionRequest,
  ExecutionRequest,
  DecisionRecord,
  ExecutionRecord,
} from '../types/recommendation'
import type { ConversationMessage, DemoScenario } from '../types/conversation'
export const getMissions = () => request<Mission[]>('/missions')
export const getMission = (id: string) => request<Mission>(`/missions/${encodeURIComponent(id)}`)
export const getOperations = (id: string) => request<Operation[]>(`/missions/${encodeURIComponent(id)}/operations`)
export interface CancelReceipt {
  request_id: string
  mission_id: string
  operation_id: string
  run_id: number
  accepted: boolean
  outcome: 'CANCELLED' | 'ALREADY_TERMINAL'
  status: Operation['status']
  cleanup_status: Operation['cleanup_status']
  replayed: boolean
}
export const cancelOperation = (mission: string, operation: string, body: {
  request_id: string
  expected_run_id: number
  expected_pause_id: string | null
}) => request<CancelReceipt>(`/missions/${encodeURIComponent(mission)}/operations/${encodeURIComponent(operation)}/cancel`, {
  method: 'POST', body: JSON.stringify(body),
})
export const getRecommendations = (id: string) =>
  request<RecommendationList>(`/missions/${encodeURIComponent(id)}/recommendations`)
const actionPath = (item: Recommendation) =>
  `/missions/${encodeURIComponent(item.source.mission_id)}/recommendations/${encodeURIComponent(item.recommendation_id)}`
export const getRecommendationActivity = (item: Recommendation) =>
  request<RecommendationActivity>(
    `${actionPath(item)}/activity?${new URLSearchParams(item.context)}`,
  )
export const recordDecision = (item: Recommendation, body: DecisionRequest) =>
  request<{ record: DecisionRecord; created: boolean }>(`${actionPath(item)}/decisions`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
export const recordExecution = (item: Recommendation, body: ExecutionRequest) =>
  request<{ record: ExecutionRecord; created: boolean }>(`${actionPath(item)}/executions`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
export const getMessages = (id: string) =>
  request<ConversationMessage[]>(`/conversations/${encodeURIComponent(id)}/messages`)
export const getReport = (id: string) =>
  request<{ title: string; markdown: string }>(`/missions/${encodeURIComponent(id)}/report`)
export interface AcceptedRequest {
  message_id: string
  mission_id: string
  operation_id: string
  accepted: boolean
  replayed: boolean
}
export const submitInput = (
  id: string,
  body: {
    request_id: string
    pause_id: string
    values: Record<string, string | number | boolean>
  },
) =>
  request(`/missions/${encodeURIComponent(id)}/input`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
export interface MessageDraft {
  conversation_id: string
  content: string
  mission_id?: string
  intent: 'new_mission' | 'followup' | 'explain'
  demo_scenario: DemoScenario
  use_career_memory?: boolean
  pause_id?: string
}
export const sendMessage = (body: MessageDraft & { request_id: string }) =>
  request<AcceptedRequest>('/messages', {
    method: 'POST',
    body: JSON.stringify(body),
  })

export interface ContinuationRequest {
  parent_mission_id: string
  conversation_id: string
  request_id: string
  operation: 'reevaluate' | 'retry'
  reason: string
  content: string
  include_report: boolean
  selected_results: { subtask_id: string; version: number }[]
  use_career_memory?: boolean
  recommendation_id?: string
  recommendation_revision?: number
  assessment_id?: string
  assessment_revision?: number
  execution_ids?: string[]
}
export const createContinuation = (body: ContinuationRequest) =>
  request<{ message_id: string; mission_id: string; created: boolean }>('/mission-continuations', {
    method: 'POST',
    body: JSON.stringify(body),
  })
export const getFeedbackInput = (id: string) =>
  request<CareerFeedback>(`/missions/${encodeURIComponent(id)}/feedback-input`)
export const getRecommendationFollowups = (item: Recommendation) =>
  request<{
    items: { mission_id: string; status: string; reason: string; recommendation_ids: string[] }[]
  }>(`${actionPath(item)}/followups?${new URLSearchParams(item.context)}`)
