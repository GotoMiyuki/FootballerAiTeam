export type MissionStatus =
  | 'CREATED'
  | 'PLANNING'
  | 'RUNNING'
  | 'REVIEWING'
  | 'REVISING'
  | 'REPLANNING'
  | 'BLOCKED'
  | 'COMPLETED'
  | 'FAILED'
export type SubtaskStatus =
  'PENDING' | 'RUNNING' | 'COMPLETED' | 'REVISION_REQUIRED' | 'BLOCKED' | 'SKIPPED'
export interface Subtask {
  id: string
  title: string
  status: SubtaskStatus
  assigned_agent: string
  revision_count: number
}
export interface Plan {
  version: number
  objective: string
  reason: string
  subtasks: Subtask[]
}
export interface AgentActivity {
  name: string
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED'
  activity: string
}
export interface Review {
  decision: 'PASS' | 'REVISE' | 'REPLAN' | 'BLOCKED'
  summary: string
  affected_subtasks: string[]
  severity: string
}
export interface RequiredInput {
  key: string
  label: string
  input_type: 'text' | 'number' | 'scale' | 'boolean' | 'single_select'
  required: boolean
  min: number | null
  max: number | null
  options: string[]
}
export interface Blocked {
  reason: string
  message: string
  required_inputs: RequiredInput[]
}
export interface Mission {
  id: string
  conversation_id: string
  title: string
  objective: string
  status: MissionStatus
  created_at: string
  sequence: number
  plan: Plan | null
  agents: AgentActivity[]
  review: Review | null
  review_history: Review[]
  blocked: Blocked | null
  result: string | null
  report: { title: string; plan_version: number } | null
  error: string | null
  telemetry: Record<string, number>
}
export const statusLabels: Record<MissionStatus, string> = {
  CREATED: '已创建',
  PLANNING: '正在规划',
  RUNNING: '正在执行',
  REVIEWING: '正在审查',
  REVISING: '局部修订中',
  REPLANNING: '重新规划中',
  BLOCKED: '等待你的输入',
  COMPLETED: '已完成',
  FAILED: '任务失败',
}
export const isActive = (status?: MissionStatus) =>
  !!status && !['BLOCKED', 'COMPLETED', 'FAILED'].includes(status)
