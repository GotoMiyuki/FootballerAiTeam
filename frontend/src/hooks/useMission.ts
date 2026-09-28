import { useQuery, useQueryClient } from '@tanstack/react-query'
import type { Mission } from '../types/mission'
import { getMission } from '../api/missions'
import { useMissionEvents } from './useMissionEvents'
export function useMission(id?: string) {
  const cache = useQueryClient()
  const query = useQuery({
    queryKey: ['mission', id],
    queryFn: async () => {
      const snapshot = await getMission(id!)
      const current = cache.getQueryData<Mission>(['mission', id])
      return current && current.sequence > snapshot.sequence ? current : snapshot
    },
    enabled: !!id,
    staleTime: Infinity,
  })
  const connection = useMissionEvents(id)
  return { ...query, connection }
}
