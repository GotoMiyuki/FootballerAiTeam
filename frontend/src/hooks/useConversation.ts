import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getMessages, sendMessage } from '../api/missions'
export function useConversation(conversationId: string, onMission: (id: string) => void) {
  const cache = useQueryClient()
  const messages = useQuery({
    queryKey: ['messages', conversationId],
    queryFn: () => getMessages(conversationId),
  })
  const send = useMutation({
    mutationFn: sendMessage,
    onSuccess: (response) => {
      onMission(response.mission_id)
      void cache.invalidateQueries({ queryKey: ['missions'] })
      void cache.invalidateQueries({ queryKey: ['messages', conversationId] })
    },
  })
  return { messages, send }
}
