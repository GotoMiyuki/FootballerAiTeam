export interface ConversationMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  created_at: string
  mission_id: string
  kind?: 'task' | 'explanation' | 'resume' | 'cancellation'
  operation_status?: 'COMPLETED' | 'FAILED' | 'CANCELLED'
  in_reply_to?: string | null
}
export type DemoScenario = 'pass' | 'revision' | 'blocked' | 'replan'
