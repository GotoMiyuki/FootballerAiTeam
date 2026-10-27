import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getMessages, sendMessage } from '../api/missions'
import type { MessageDraft } from '../api/missions'
import { messageRequestKey, pendingRequest, clearPending } from '../api/pendingRequests'
export function useConversation(conversationId: string, onMission: (id: string) => void) {
  const cache = useQueryClient()
  const messages = useQuery({
    queryKey: ['messages', conversationId],
    queryFn: () => getMessages(conversationId),
  })
  const send = useMutation({
    mutationFn: (body: MessageDraft) =>
      sendMessage(pendingRequest(messageRequestKey(body.conversation_id, body.mission_id), body)),
    onSuccess: (response, body) => {
      clearPending(messageRequestKey(body.conversation_id, body.mission_id))
      onMission(response.mission_id)
      void cache.invalidateQueries({ queryKey: ['missions'] })
      void cache.invalidateQueries({ queryKey: ['messages', conversationId] })
      void cache.invalidateQueries({ queryKey: ['operations', response.mission_id] })
      void cache.invalidateQueries({ queryKey: ['runtime', response.mission_id] })
    },
  })
  return { messages, send }
}
