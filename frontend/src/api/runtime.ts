import { request } from './client'
import type { Operation } from '../types/mission'
import type { RuntimeView } from '../types/runtime'

export const getRuntime = (id: string) => request<RuntimeView>(`/missions/${encodeURIComponent(id)}/runtime`)

export function mergeOperations<T extends Operation>(current: T[] | undefined, incoming: T[]): T[] {
  if (!current) return incoming
  const next = new Map(incoming.map((op) => [op.id, op]))
  return current.some((old) => !next.has(old.id) ||
    (next.get(old.id)!.revision ?? 0) < (old.revision ?? 0) || next.get(old.id)!.run_id < old.run_id)
    ? current : incoming
}

export function mergeRuntime(current: RuntimeView | undefined, incoming: RuntimeView): RuntimeView {
  if (!current?.operations) return incoming
  if (!incoming?.operations) return current
  if (incoming.mission_id !== current.mission_id || incoming.mission_sequence < current.mission_sequence)
    return current
  if (mergeOperations(current.operations, incoming.operations) === current.operations && current.operations !== incoming.operations)
    return current
  if (incoming.mission_sequence === current.mission_sequence && Date.parse(incoming.read_at) < Date.parse(current.read_at))
    return current
  return incoming
}
