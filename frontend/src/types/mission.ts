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
  | 'CANCELLED'
export type SubtaskStatus =
  | 'PENDING'
  | 'RUNNING'
  | 'COMPLETED'
  | 'REVISION_REQUIRED'
  | 'BLOCKED'
  | 'SKIPPED'
  | 'FAILED'
  | 'INVALIDATED'
  | 'CANCELLED'
export interface Subtask {
  id: string
  title: string
  status: SubtaskStatus
  assigned_agent: string
  revision_count: number
  result_summary?: string | null
  result_version?: number | null
  reason?: string
}
export interface Plan {
  version: number
  objective: string
  reason: string
  subtasks: Subtask[]
}
export interface AgentActivity {
  name: string
  status: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
  activity: string
}
export interface Review {
  availability?: 'NOT_RUN' | 'COMPLETED' | 'UNAVAILABLE'
  scope?: string
  reviewed_versions?: Record<string, number>
  decision: 'PASS' | 'REVISE' | 'REPLAN' | 'BLOCKED' | null
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
  pause_id?: string | null
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
  workflow_operation_id?: string | null
  plan: Plan | null
  agents: AgentActivity[]
  review: Review | null
  review_history: Review[]
  blocked: Blocked | null
  result: string | null
  report: { title: string; plan_version: number } | null
  error: string | null
  telemetry: Record<string, number | null>
  delivery_status?: string
  body_validation?: Record<string, unknown>
  input_reference?: {
    verification: 'VERIFIED' | 'DEMO' | 'UNVERIFIED'
    context: { career_id: string; branch_id: string; player_id: string } | null
    state_version: string | null
    snapshot_id: string | null
    source_types: string[]
  }
  available_operations?: (
    'view' | 'explain' | 'supply_input' | 'approve_report' | 'reevaluate' | 'retry'
  )[]
  resume_error?: string | null
  memory_reference?: {
    verification: 'FROZEN' | 'DISABLED' | 'LEGACY_NONE'
    content_hash: string | null
    items: { memory_id: string; revision: number; kind: string; content_hash: string }[]
  }
  lineage?: {
    parent_mission_id: string
    operation: 'reevaluate' | 'retry'
    reason: string
    request_id: string
    history_references: {
      kind: 'report' | 'result' | 'recommendation' | 'assessment' | 'decision' | 'execution'
      mission_id: string
      subtask_id: string | null
      version: number
      content_hash: string
      object_id?: string | null
    }[]
    feedback_reference?: {
      content_hash: string
      recommendation_id: string
      recommendation_revision: number
      assessment_id: string | null
      assessment_revision: number | null
      execution_ids: string[]
    } | null
  } | null
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
  CANCELLED: '已取消',
}
export const isActive = (status?: MissionStatus) =>
  !!status && !['BLOCKED', 'COMPLETED', 'FAILED', 'CANCELLED'].includes(status)

export interface Operation {
  revision?: number
  id: string
  mission_id: string
  kind: 'workflow' | 'explanation'
  status: 'QUEUED' | 'RUNNING' | 'PAUSED' | 'COMPLETED' | 'FAILED' | 'CANCELLED'
  run_id: number
  pause_id: string | null
  cleanup_status: 'NOT_NEEDED' | 'PENDING' | 'COMPLETE'
}
