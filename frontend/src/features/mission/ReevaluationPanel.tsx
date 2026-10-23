import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowUpRight } from 'lucide-react'
import { createContinuation, type ContinuationRequest } from '../../api/missions'
import { Dialog } from '../../components/Dialog'
import { ErrorNotice } from '../../components/ui'
import type { Mission } from '../../types/mission'

export function ReevaluationPanel({
  mission,
  onMission,
}: {
  mission: Mission
  onMission: (id: string) => void
}) {
  const operation = mission.status === 'FAILED' ? 'retry' : 'reevaluate'
  const allowed = mission.available_operations?.includes(operation) ?? false
  const [open, setOpen] = useState(false)
  const draftKey = `fait.continuation.${mission.id}`
  const [draft, setDraft] = useState<ContinuationRequest>(() => {
    try {
      const saved = JSON.parse(
        localStorage.getItem(draftKey) || 'null',
      ) as ContinuationRequest | null
      if (
        saved?.parent_mission_id === mission.id &&
        saved.operation === operation &&
        saved.request_id
      )
        return saved
    } catch {
      /* A damaged draft can be recreated without executing a task. */
    }
    return {
      parent_mission_id: mission.id,
      conversation_id: mission.conversation_id,
      request_id: `request_${crypto.randomUUID()}`,
      operation,
      reason: '',
      content: '',
      include_report: operation === 'reevaluate',
      selected_results: [],
    }
  })
  const cache = useQueryClient()
  const create = useMutation({
    mutationFn: createContinuation,
    retry: false,
    onSuccess: (response) => {
      localStorage.removeItem(draftKey)
      setOpen(false)
      void cache.invalidateQueries({ queryKey: ['missions'] })
      void cache.invalidateQueries({ queryKey: ['messages', mission.conversation_id] })
      onMission(response.mission_id)
    },
  })
  const [submitted, setSubmitted] = useState<ContinuationRequest | null>(null)
  function change(fields: Partial<ContinuationRequest>) {
    // An edited submission is a different request; network retries keep the previous id/body.
    const next = {
      ...draft,
      ...fields,
      request_id: `request_${crypto.randomUUID()}`,
    }
    setSubmitted(null)
    setDraft(next)
    localStorage.setItem(draftKey, JSON.stringify(next))
    create.reset()
  }
  const results =
    mission.input_reference?.verification === 'VERIFIED'
      ? mission.plan?.subtasks.filter(
          (task) => task.status === 'COMPLETED' && task.result_version && task.result_summary,
        ) || []
      : []
  if (!allowed) return null
  return (
    <>
      <div className="suggestions">
        <span>基于新情况创建关联任务，原报告保留。</span>
        <button type="button" onClick={() => setOpen(true)}>
          {operation === 'retry' ? '创建关联重试任务' : '根据新情况重新评估'}
          <ArrowUpRight size={14} />
        </button>
      </div>
      {open && (
        <Dialog
          title={operation === 'retry' ? '创建关联重试任务' : '根据新情况重新评估'}
          onClose={() => setOpen(false)}
        >
          <form
            className="reevaluation-form"
            onSubmit={(event) => {
              event.preventDefault()
              const body = submitted || draft
              localStorage.setItem(draftKey, JSON.stringify(body))
              setSubmitted(body)
              create.mutate(body)
            }}
          >
            <p>
              来源任务：{mission.title}
              。新任务在提交时使用同一生涯的最新数据，并保存选定历史。普通文字补充仍需后续核实。
            </p>
            <label>
              发起原因
              <input
                aria-label="发起原因"
                required
                maxLength={1000}
                value={draft.reason}
                disabled={create.isPending}
                onChange={(event) => change({ reason: event.target.value })}
                placeholder="例如：新增比赛记录或训练时间改变"
              />
            </label>
            <label>
              新的评估要求
              <textarea
                aria-label="新的评估要求"
                required
                rows={4}
                maxLength={8000}
                value={draft.content}
                disabled={create.isPending}
                onChange={(event) => change({ content: event.target.value })}
                placeholder="说明新目标、约束和需要重新判断的问题…"
              />
            </label>
            {operation === 'reevaluate' && (
              <label className="history-choice">
                <input
                  type="checkbox"
                  checked={draft.include_report}
                  disabled={create.isPending}
                  onChange={(event) => change({ include_report: event.target.checked })}
                />
                引用原报告（用于历史参考）
              </label>
            )}
            {results.length > 0 && (
              <fieldset>
                <legend>选择相关的专业结果</legend>
                {results.map((task) => (
                  <label className="history-choice" key={task.id}>
                    <input
                      type="checkbox"
                      checked={draft.selected_results.some(
                        (result) => result.subtask_id === task.id,
                      )}
                      disabled={create.isPending}
                      onChange={(event) =>
                        change({
                          selected_results: event.target.checked
                            ? [
                                ...draft.selected_results,
                                { subtask_id: task.id, version: task.result_version! },
                              ]
                            : draft.selected_results.filter(
                                (result) => result.subtask_id !== task.id,
                              ),
                        })
                      }
                    />
                    {task.title} · 版本 {task.result_version}
                  </label>
                ))}
              </fieldset>
            )}
            {create.isError && <ErrorNotice message={create.error.message} />}
            <button
              className="primary-button"
              type="submit"
              disabled={create.isPending || !draft.reason.trim() || !draft.content.trim()}
            >
              {create.isPending ? '正在创建…' : create.isError ? '重试创建' : '创建关联任务'}
            </button>
          </form>
        </Dialog>
      )}
    </>
  )
}
