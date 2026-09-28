import { request } from './client'
import type { Mission } from '../types/mission'
import type { ConversationMessage, DemoScenario } from '../types/conversation'
export const getMissions = () => request<Mission[]>('/missions')
export const getMission = (id: string) => request<Mission>(`/missions/${encodeURIComponent(id)}`)
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
  intent: 'new_mission' | 'followup'
  demo_scenario: DemoScenario
}) =>
  request<{ message_id: string; mission_id: string }>('/messages', {
    method: 'POST',
    body: JSON.stringify(body),
  })
