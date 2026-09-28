export interface ConversationMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
  mission_id: string
}
export type DemoScenario = 'pass' | 'revision' | 'blocked' | 'replan'
