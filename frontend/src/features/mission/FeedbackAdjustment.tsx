import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createContinuation, type ContinuationRequest } from '../../api/missions'
import { Dialog } from '../../components/Dialog'
import { ErrorNotice } from '../../components/ui'
import type { Assessment } from '../../types/assessment'
import type { Recommendation } from '../../types/recommendation'
import type { Mission } from '../../types/mission'

export function FeedbackAdjustment({
  mission,
  item,
  assessment,
  onMission,
}: {
  mission: Mission
  item: Recommendation
  assessment: Assessment
  onMission: (id: string) => void
}) {
  const key = `fait.feedback-adjustment.${assessment.assessment_id}`
  const initial = (): ContinuationRequest => ({
    parent_mission_id: mission.id,
    conversation_id: mission.conversation_id,
    operation: 'reevaluate',
    request_id: `adjust_${crypto.randomUUID()}`,
    reason: '',
    content: '',
    include_report: false,
    selected_results: [],
    use_career_memory: true,
    recommendation_id: item.recommendation_id,
    recommendation_revision: item.revision,
    assessment_id: assessment.assessment_id,
    assessment_revision: assessment.revision,
    execution_ids: assessment.result.execution.execution_id
      ? [assessment.result.execution.execution_id]
      : [],
  })
  const [draft, setDraft] = useState<ContinuationRequest>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(key) || 'null') as ContinuationRequest | null
      if (
        saved?.parent_mission_id === mission.id &&
        saved.assessment_id === assessment.assessment_id &&
        saved.request_id
      )
        return saved
    } catch {
      /* Damaged drafts do not create tasks. */
    }
    return initial()
  })
  const [open, setOpen] = useState(false)
  const cache = useQueryClient()
  const mutation = useMutation({
    mutationFn: createContinuation,
    retry: false,
    onSuccess: (response) => {
      localStorage.removeItem(key)
      setOpen(false)
      void cache.invalidateQueries({ queryKey: ['missions'] })
      void cache.invalidateQueries({ queryKey: ['recommendation-followups', mission.id] })
      onMission(response.mission_id)
    },
  })
  const allowed =
    assessment.validity === 'current' &&
    ['current', 'needs_reassessment'].includes(item.validity) &&
    mission.available_operations?.includes('reevaluate')
  function change(fields: Partial<ContinuationRequest>) {
    const next = { ...draft, ...fields, request_id: `adjust_${crypto.randomUUID()}` }
    setDraft(next)
    localStorage.setItem(key, JSON.stringify(next))
    mutation.reset()
  }
  return (
    <>
      <button
        type="button"
        className="secondary-button"
        disabled={!allowed}
        onClick={() => setOpen(true)}
      >
        根据结果调整方案
      </button>
      {!allowed && <p>仅当前有效的比较可以发起调整；来源变化时先刷新比较。</p>}
      {open && (
        <Dialog title="根据结果调整方案" onClose={() => setOpen(false)}>
          <form
            className="reevaluation-form"
            onSubmit={(event) => {
              event.preventDefault()
              localStorage.setItem(key, JSON.stringify(draft))
              mutation.mutate(draft)
            }}
          >
            <p>
              原建议 v{draft.recommendation_revision} · 评估 v{draft.assessment_revision}
              。提交时核验来源并冻结最新球员快照、选定反馈及相关记忆；原记录保留，新建议需要重新选择。
            </p>
            <p>
              {assessment.result.evidence_status !== 'COMPLETE'
                ? '当前比较证据不足或不可比，只能按已知信息重新分析。'
                : '观测变化不代表建议效果或因果。'}
            </p>
            <label>
              发起原因
              <input
                aria-label="结果调整原因"
                required
                maxLength={1000}
                value={draft.reason}
                disabled={mutation.isPending}
                onChange={(event) => change({ reason: event.target.value })}
              />
            </label>
            <label>
              新的要求和约束
              <textarea
                aria-label="结果调整要求"
                required
                maxLength={8000}
                rows={4}
                value={draft.content}
                disabled={mutation.isPending}
                onChange={(event) => change({ content: event.target.value })}
              />
            </label>
            <label className="history-choice">
              <input
                type="checkbox"
                checked={draft.use_career_memory !== false}
                disabled={mutation.isPending}
                onChange={(event) => change({ use_career_memory: event.target.checked })}
              />
              引用相关生涯记忆（提交时固定）
            </label>
            <p>关联执行反馈 {draft.execution_ids?.length || 0} 条。未关联时执行保持未知。</p>
            {mutation.isError && (
              <>
                <ErrorNotice message={mutation.error.message} />
                <button
                  type="button"
                  onClick={() => {
                    const next = {
                      ...initial(),
                      reason: draft.reason,
                      content: draft.content,
                      use_career_memory: draft.use_career_memory,
                    }
                    setDraft(next)
                    localStorage.setItem(key, JSON.stringify(next))
                    mutation.reset()
                  }}
                >
                  使用当前版本重新填写
                </button>
              </>
            )}
            <button
              className="primary-button"
              type="submit"
              disabled={mutation.isPending || !draft.reason.trim() || !draft.content.trim()}
            >
              {mutation.isPending
                ? '正在创建…'
                : mutation.isError
                  ? '原样重试调整'
                  : '创建调整任务'}
            </button>
          </form>
        </Dialog>
      )}
    </>
  )
}
