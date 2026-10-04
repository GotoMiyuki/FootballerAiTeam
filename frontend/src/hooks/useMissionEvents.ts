import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { apiUrl } from '../api/client'
import { getMission } from '../api/missions'
import { parseEvent, reduceMissionEvent } from '../api/events'
import type { Mission } from '../types/mission'

export function useMissionEvents(id?: string) {
  const cache = useQueryClient()
  const [connection, setConnection] = useState<'connecting' | 'connected' | 'disconnected'>(
    'connecting',
  )
  useEffect(() => {
    if (!id) return
    let disposed = false
    let recovering = false
    setConnection('connecting')
    const cursor = cache.getQueryData<Mission>(['mission', id])?.sequence || 0
    const stream = new EventSource(
      apiUrl(`/missions/${encodeURIComponent(id)}/events?after=${cursor}`),
    )
    async function recover() {
      if (recovering) return
      recovering = true
      try {
        const snapshot = await getMission(id!)
        if (!disposed) {
          cache.setQueryData<Mission>(['mission', id], (current) =>
            !current || snapshot.sequence >= current.sequence ? snapshot : current,
          )
          // A snapshot can supersede terminal events during a reconnect race.
          // Recover the conversation even when those older events are discarded.
          void cache.invalidateQueries({ queryKey: ['messages', snapshot.conversation_id] })
          void cache.invalidateQueries({ queryKey: ['missions'] })
        }
      } catch {
        /* Connection banner retains the recoverable network state. */
      } finally {
        recovering = false
      }
    }
    stream.onopen = () => {
      setConnection('connected')
      void recover()
    }
    stream.onerror = () => {
      setConnection('disconnected')
      void recover()
    }
    stream.onmessage = (message) => {
      try {
        const event = parseEvent(message.data)
        if (event.mission_id !== id) return
        const current = cache.getQueryData<Mission>(['mission', id])
        if (current && event.sequence <= current.sequence) return
        if (current && event.sequence > current.sequence + 1) void recover()
        cache.setQueryData<Mission>(['mission', id], (current) =>
          reduceMissionEvent(current, event),
        )
        if (
          [
            'mission.completed',
            'mission.failed',
            'result.created',
            'mission.blocked',
            'message.created',
          ].includes(event.type)
        ) {
          void cache.invalidateQueries({ queryKey: ['missions'] })
          void cache.invalidateQueries({
            queryKey: ['messages', event.data.snapshot.conversation_id],
          })
        }
        if (event.type === 'mission.completed') {
          for (const key of ['player', 'training', 'matches'])
            void cache.invalidateQueries({ queryKey: [key] })
        }
      } catch {
        void recover()
      }
    }
    return () => {
      disposed = true
      stream.close()
    }
  }, [id, cache])
  return connection
}
