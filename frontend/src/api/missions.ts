import { request } from './client'
import type { Mission } from '../types/mission'
import type { RecommendationList } from '../types/recommendation'
import type { ConversationMessage, DemoScenario } from '../types/conversation'
export const getMissions = () => request<Mission[]>('/missions')
export const getMission = (id: string) => request<Mission>(`/missions/${encodeURIComponent(id)}`)
export const getRecommendations = (id: string) =>
  request<RecommendationList>(`/missions/${encodeURIComponent(id)}/recommendations`)
export const getMessages = (id: string) =>
  request<ConversationMessage[]>(`/conversations/${encodeURIComponent(id)}/messages`)
export const getReport = (id: string) =>
  request<{ title: string; markdown: string }>(`/missions/${encodeURIComponent(id)}/report`)
export const submitInput = (id: string, values: Record<string, string | number | boolean>) =>
  request(`/missions/${encodeURIComponent(id)}/input`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
export const sendMessage = (body: {
  conversation_id: string
  content: string
  mission_id?: string
  intent: 'new_mission' | 'followup' | 'explain'
  demo_scenario: DemoScenario
}) =>
  request<{ message_id: string; mission_id: string }>('/messages', {
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
}
export const createContinuation = (body: ContinuationRequest) =>
  request<{ message_id: string; mission_id: string; created: boolean }>('/mission-continuations', {
    method: 'POST',
    body: JSON.stringify(body),
  })
