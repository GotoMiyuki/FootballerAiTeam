import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { cancelOperation, getOperations } from '../../api/missions'
import { pendingRequest, clearPending } from '../../api/pendingRequests'
import { ErrorNotice } from '../../components/ui'
import type { Mission, Operation } from '../../types/mission'
import { mergeOperations } from '../../api/runtime'

const terminal = ['COMPLETED', 'FAILED', 'CANCELLED']
export function OperationControls({ mission }: { mission: Mission }) {
  const [notice, setNotice] = useState('')
  const cache = useQueryClient()
  const operations = useQuery({
    queryKey: ['operations', mission.id],
    queryFn: async () => mergeOperations(cache.getQueryData<Operation[]>(['operations', mission.id]), await getOperations(mission.id)),
    refetchInterval: (query) =>
      query.state.data?.some((op) => !terminal.includes(op.status) || op.cleanup_status === 'PENDING')
        ? 750
        : false,
  })
  const visible = operations.data?.filter(
    (op) => !terminal.includes(op.status) || op.status === 'CANCELLED',
  ) || []
  return (
    <div className="operation-controls" aria-label="执行操作">
      {notice && <p role="status">{notice}</p>}
      {operations.isError && <ErrorNotice message="操作状态暂时无法读取" onRetry={() => void operations.refetch()} />}
      {visible.map((op) => (
        <CancelControl key={`${op.id}-${op.run_id}-${op.pause_id || ''}`} operation={op} mission={mission} onNotice={setNotice} />
      ))}
    </div>
  )
}

function CancelControl({ operation: op, mission, onNotice }: {
  operation: Operation
  mission: Mission
  onNotice: (value: string) => void
}) {
  const cache = useQueryClient()
  const key = `fait.pending.cancel:${mission.id}:${op.id}:${op.run_id}:${op.pause_id || ''}`
  const refresh = () => {
    for (const queryKey of [['operations', mission.id], ['mission', mission.id], ['missions'], ['messages', mission.conversation_id]])
      void cache.invalidateQueries({ queryKey })
  }
  const cancel = useMutation({
    retry: false,
    mutationFn: () => cancelOperation(mission.id, op.id, pendingRequest(key, {
      expected_run_id: op.run_id,
      expected_pause_id: op.pause_id || null,
    })),
    onSuccess: (receipt) => {
      clearPending(key)
      onNotice(receipt.accepted ? '取消已记录。资源清理状态见下方。' : '该操作已经结束，无需取消。')
      refresh()
    },
    onError: refresh,
  })
  const explanation = op.kind === 'explanation'
  return (
    <div className="operation-control">
      {op.status === 'CANCELLED' ? (
        <span role="status">
          {explanation ? '本次解释已取消，原报告保留。' : '任务已取消，原计划保留。'}
          {op.cleanup_status === 'PENDING' ? '正在清理执行资源…' : '执行资源已清理。'}
        </span>
      ) : (
        <>
          <span>{explanation ? '本次报告解释' : '任务执行'}{op.status === 'QUEUED' ? '正在排队' : op.status === 'PAUSED' ? '正在等待你的输入' : '正在进行'}</span>
          <button type="button" disabled={cancel.isPending} onClick={() => cancel.mutate()}>
            {cancel.isPending ? '正在取消…' : explanation ? '取消本次解释' : '取消任务'}
          </button>
        </>
      )}
      {cancel.isError && <ErrorNotice message={cancel.error.message} />}
    </div>
  )
}
