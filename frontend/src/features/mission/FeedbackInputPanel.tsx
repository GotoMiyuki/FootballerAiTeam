import { useQuery } from '@tanstack/react-query'
import { getFeedbackInput } from '../../api/missions'
import { ErrorNotice, Loading } from '../../components/ui'
import type { Mission } from '../../types/mission'
export function FeedbackInputPanel({ mission }: { mission: Mission }) {
  const reference = mission.lineage?.feedback_reference
  const query = useQuery({
    queryKey: ['feedback-input', mission.id],
    queryFn: () => getFeedbackInput(mission.id),
    enabled: !!reference,
    staleTime: Infinity,
  })
  if (!reference) return null
  const data = query.data
  return (
    <details className="section-card" aria-label="本次固定反馈来源">
      <summary>本次固定反馈来源 · 评估 v{reference.assessment_revision ?? '未选择'}</summary>
      <p>
        这是本次调整提交时选中的建议、评估与执行反馈。后续更正不改变本次输入；新建议不继承原采纳。
      </p>
      {query.isPending ? (
        <Loading label="读取固定反馈…" />
      ) : query.isError ? (
        <ErrorNotice message={query.error.message} onRetry={() => void query.refetch()} />
      ) : (
        data && (
          <>
            <p>
              原建议：{data.recommendation.content.text} · v{data.recommendation.revision} ·{' '}
              {data.recommendation.recommendation_id}
            </p>
            {data.assessment && (
              <p>
                原评估：{data.assessment.assessment_id} · v{data.assessment.revision} · 观测前值{' '}
                {data.assessment.result.before ?? '未知'} / 后值{' '}
                {data.assessment.result.after ?? '未知'} / 变化{' '}
                {data.assessment.result.delta ?? '未知'}。
                {data.assessment.latest_input_differs_from_assessment &&
                  '本次最新快照与原评估观察快照不同；保留所选评估版本和限制。'}
              </p>
            )}
            <p>原选择：{data.decision?.status || '未选择'}（历史选择，不应用到新建议）</p>
            {data.executions.map((item) => (
              <p key={item.execution_id}>
                执行反馈：{item.actual_action} · {item.status} · v{item.revision} ·{' '}
                {item.execution_id}
              </p>
            ))}
            {!data.executions.length && <p>执行未知：本次未关联反馈。</p>}
            <p>{data.limitations.join('；')}</p>
          </>
        )
      )}
    </details>
  )
}
