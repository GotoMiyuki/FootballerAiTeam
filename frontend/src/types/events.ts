import type { Mission } from './mission'
export const eventTypes = [
  'mission.created',
  'mission.started',
  'mission.status_changed',
  'mission.blocked',
  'mission.completed',
  'mission.failed',
  'plan.created',
  'plan.updated',
  'subtask.failed',
  'subtask.invalidated',
  'agent.failed',
  'review.unavailable',
  'review.invalidated',
  'subtask.started',
  'subtask.completed',
  'subtask.revision_required',
  'agent.started',
  'agent.completed',
  'review.started',
  'review.completed',
  'revision.started',
  'revision.completed',
  'replan.started',
  'replan.completed',
  'result.created',
  'report.created',
  'message.created',
] as const
export interface MissionEvent {
  event_id: string
  type: (typeof eventTypes)[number]
  mission_id: string
  timestamp: string
  sequence: number
  data: { snapshot: Mission; [key: string]: unknown }
}
