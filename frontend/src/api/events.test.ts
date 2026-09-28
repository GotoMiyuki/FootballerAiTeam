import { describe, expect, it } from 'vitest'
import { parseEvent, reduceMissionEvent } from './events'
import type { MissionEvent } from '../types/events'
import type { Mission } from '../types/mission'
const mission = {
  id: 'mission_a',
  sequence: 3,
  plan: { version: 1, subtasks: [{ id: 'old' }] },
} as Mission
function event(sequence = 4): MissionEvent {
  return {
    event_id: 'evt_1',
    type: 'plan.updated',
    mission_id: 'mission_a',
    timestamp: '2026-09-28',
    sequence,
    data: {
      snapshot: {
        ...mission,
        sequence,
        plan: { version: 2, objective: '', reason: 'replan', subtasks: [] },
      },
    },
  }
}
describe('ordered public mission events', () => {
  it('replaces the plan atomically when replan removes old tasks', () => {
    expect(reduceMissionEvent(mission, event())?.plan).toEqual(event().data.snapshot.plan)
  })
  it('ignores duplicate and out-of-order events', () => {
    expect(reduceMissionEvent(mission, event(3))).toBe(mission)
    expect(reduceMissionEvent(mission, event(1))).toBe(mission)
  })
  it('does not contaminate another mission cache', () => {
    expect(reduceMissionEvent({ ...mission, id: 'mission_b' }, event())?.id).toBe('mission_b')
  })
  it('can recover a missing cache with a full snapshot', () => {
    expect(reduceMissionEvent(undefined, event())?.sequence).toBe(4)
  })
  it('rejects malformed protocol and mismatched snapshot', () => {
    expect(() => parseEvent('{}')).toThrow()
    expect(() => parseEvent(JSON.stringify({ ...event(), data: { snapshot: mission } }))).toThrow()
    expect(parseEvent(JSON.stringify(event())).sequence).toBe(4)
  })
})
