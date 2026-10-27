type Identified<T> = T & { request_id: string }

export const messageRequestKey = (conversationId: string, missionId?: string) =>
  `fait.pending.message:${conversationId}:${missionId || 'new'}`
export const inputRequestKey = (missionId: string, pauseId: string) =>
  `fait.pending.input:${missionId}:${pauseId}`

export function readPending<T>(key: string): Identified<T> | null {
  const raw = localStorage.getItem(key)
  if (!raw) return null
  try {
    return JSON.parse(raw) as Identified<T>
  } catch {
    localStorage.removeItem(key)
    return null
  }
}

export function pendingRequest<T extends object>(key: string, body: T): Identified<T> {
  const previous = readPending<T>(key)
  if (previous) {
    const { request_id: _, ...payload } = previous
    if (JSON.stringify(payload) === JSON.stringify(body)) return previous
  }
  const request = { ...body, request_id: `request_${crypto.randomUUID()}` }
  localStorage.setItem(key, JSON.stringify(request))
  return request
}

export const clearPending = (key: string) => localStorage.removeItem(key)
