import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest'
import { pendingRequest, readPending, clearPending, inputRequestKey } from './pendingRequests'

beforeEach(() => {
  const cache = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => cache.get(key) ?? null,
    setItem: (key: string, value: string) => cache.set(key, value),
    removeItem: (key: string) => cache.delete(key),
  })
})
afterEach(() => vi.unstubAllGlobals())

describe('pending user requests', () => {
  it('reuses the exact request identity after a lost response and cache reload', () => {
    const body = { content: 'same question', conversation_id: 'conversation' }
    const first = pendingRequest('pending', body)
    expect(readPending('pending')).toEqual(first)
    expect(pendingRequest('pending', body)).toEqual(first)
  })
  it('edits create a new request and success clears the previous pending request', () => {
    const first = pendingRequest('pending', { content: 'original' })
    const edited = pendingRequest('pending', { content: 'changed' })
    expect(edited.request_id).not.toBe(first.request_id)
    clearPending('pending')
    expect(readPending('pending')).toBeNull()
    expect(pendingRequest('pending', { content: 'changed' }).request_id).not.toBe(edited.request_id)
  })
  it('a later pause never restores the previous pause answer or identity', () => {
    const key = inputRequestKey('mission', 'pause-one')
    const original = pendingRequest(key, {
      pause_id: 'pause-one',
      values: { information: 'old answer' },
    })
    expect(readPending(inputRequestKey('mission', 'pause-two'))).toBeNull()
    const current = pendingRequest(inputRequestKey('mission', 'pause-two'), {
      pause_id: 'pause-two',
      values: { information: 'new answer' },
    })
    expect(current.request_id).not.toBe(original.request_id)
    expect(readPending(key)).toEqual(original)
  })
})
