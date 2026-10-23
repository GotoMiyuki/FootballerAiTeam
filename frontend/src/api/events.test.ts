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
  it('accepts explanation messages while preserving the completed report and input version', () => {
    const changed = event()
    changed.type = 'message.created'
    changed.data.snapshot = {
      ...changed.data.snapshot,
      status: 'COMPLETED',
      report: { title: '原报告', plan_version: 1 },
      delivery_status: 'PUBLISHABLE',
      input_reference: {
        verification: 'VERIFIED',
        context: null,
        state_version: 'v1',
        snapshot_id: 's1',
        source_types: [],
      },
    }
    const parsed = parseEvent(JSON.stringify(changed))
    expect(reduceMissionEvent(mission, parsed)?.report?.title).toBe('原报告')
    expect(reduceMissionEvent(mission, parsed)?.input_reference?.state_version).toBe('v1')
  })
  it('accepts failure, unavailable review and invalidation snapshots without success residue', () => {
    for (const type of [
      'subtask.failed',
      'review.unavailable',
      'review.invalidated',
      'subtask.invalidated',
      'agent.failed',
    ] as const) {
      const changed = event()
      changed.type = type
      changed.data.snapshot = {
        ...changed.data.snapshot,
        status: 'FAILED',
        report: null,
        result: null,
        review: {
          availability: 'UNAVAILABLE',
          decision: null,
          summary: '审查未完成',
          affected_subtasks: [],
          severity: 'INFO',
        },
      }
      const parsed = parseEvent(JSON.stringify(changed))
      expect(reduceMissionEvent(mission, parsed)?.report).toBeNull()
      expect(reduceMissionEvent(mission, parsed)?.review?.decision).toBeNull()
    }
  })
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
