import type { MissionEvent } from '../types/events'
import { eventTypes } from '../types/events'
import type { Mission } from '../types/mission'

export function parseEvent(raw: string): MissionEvent {
  const event = JSON.parse(raw) as MissionEvent
  if (
    !event.event_id ||
    !event.mission_id ||
    !Number.isSafeInteger(event.sequence) ||
    event.sequence < 1 ||
    !eventTypes.includes(event.type) ||
    !event.data?.snapshot ||
    event.data.snapshot.id !== event.mission_id ||
    event.data.snapshot.sequence !== event.sequence
  )
    throw new Error('事件协议无效')
  return event
}
export function reduceMissionEvent(
  current: Mission | undefined,
  event: MissionEvent,
): Mission | undefined {
  if (current && (current.id !== event.mission_id || event.sequence <= current.sequence))
    return current
  // Public snapshot replacement is atomic, including plan version changes.
  return event.data.snapshot
}
