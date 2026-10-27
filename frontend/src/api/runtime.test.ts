import { describe, it, expect } from 'vitest'
import { mergeOperations, mergeRuntime } from './runtime'
import type { RuntimeView, RuntimeOperation } from '../types/runtime'

function snapshot(revision: number, read = '2026-10-26T00:00:02Z'): RuntimeView {
  return { mission_id: 'mission', mission_sequence: 4, read_at: read, workflow_operation_id: 'workflow',
    operations: [{ id: 'workflow', mission_id: 'mission', kind: 'workflow', status: 'PAUSED', run_id: 1,
      pause_id: 'pause', cleanup_status: 'COMPLETE', revision,
      usage: { available: true, logical_calls: revision, network_attempts: revision, known_network_attempts: revision,
        input_tokens: null, output_tokens: null, known_input_tokens: 5, known_output_tokens: 2, usage_known_calls: 1, complete: false },
      timing: { history_complete: true, queue_seconds: 1, execution_seconds: 2, user_wait_seconds: revision,
        wall_seconds: 4, known_queue_seconds: 1, known_execution_seconds: 2, known_user_wait_seconds: revision, live: true } }],
    totals: { available: true, logical_calls: revision, network_attempts: revision, known_network_attempts: revision,
      input_tokens: null, output_tokens: null, known_input_tokens: 5, known_output_tokens: 2, usage_known_calls: 1, complete: false }, report_telemetry: {} }
}

describe('runtime response ordering', () => {
  it('retains newer counters when an older HTTP response arrives', () => {
    const current = snapshot(3)
    expect(mergeRuntime(current, snapshot(2))).toBe(current)
    expect(mergeOperations(current.operations, snapshot(2).operations)).toBe(current.operations)
  })
  it('keeps live waiting time ordered when revisions are equal', () => {
    const current = snapshot(3)
    const old = snapshot(3, '2026-10-26T00:00:01Z')
    old.operations[0].timing.user_wait_seconds = 1
    expect(mergeRuntime(current, old)).toBe(current)
    const next = snapshot(3, '2026-10-26T00:00:03Z')
    expect(mergeRuntime(current, next)).toBe(next)
  })
  it('rejects an unrelated mission and an old response missing the new explanation', () => {
    const current = snapshot(3)
    const explanation = { ...current.operations[0], id: 'explanation', kind: 'explanation' } as RuntimeOperation
    current.operations.push(explanation)
    expect(mergeRuntime(current, snapshot(3))).toBe(current)
    expect(mergeRuntime(current, { ...snapshot(4), mission_id: 'other' })).toBe(current)
  })
  it('replaces cumulative values rather than adding replayed snapshots', () => {
    const current = snapshot(2)
    const next = snapshot(3)
    expect(mergeRuntime(current, next).totals.logical_calls).toBe(3)
    expect(mergeRuntime(next, next).totals.logical_calls).toBe(3)
    expect(mergeRuntime(current, snapshot(2)).totals.input_tokens).toBeNull()
    expect(mergeRuntime([] as unknown as RuntimeView, next)).toBe(next)
  })
})
