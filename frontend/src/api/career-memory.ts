import { request } from './client'
import type {
  CareerMemory,
  CareerMemoryList,
  MemoryWriteRequest,
  FrozenMemory,
} from '../types/career-memory'

const path = (missionId: string) => `/missions/${encodeURIComponent(missionId)}/career-memory`
export const getCareerMemory = (id: string) => request<CareerMemoryList>(path(id))
export const getFrozenMemory = (id: string) =>
  request<FrozenMemory>(`/missions/${encodeURIComponent(id)}/memory-input`)
export const previewMemory = (id: string, content: string) =>
  request<FrozenMemory>(`${path(id)}/retrieval-preview`, {
    method: 'POST',
    body: JSON.stringify({ content }),
  })
export const syncCareerMemory = (id: string) =>
  request<{ consumed_events: number; created_memories: number }>(`${path(id)}/sync`, {
    method: 'POST',
  })
export const writeCareerMemory = (id: string, body: MemoryWriteRequest) =>
  request<{
    record: Omit<
      CareerMemory,
      | 'status'
      | 'usage'
      | 'status_reason'
      | 'source_checked'
      | 'recommendation_validity'
      | 'limitations'
    >
    created: boolean
  }>(`${path(id)}/user-records`, { method: 'POST', body: JSON.stringify(body) })
