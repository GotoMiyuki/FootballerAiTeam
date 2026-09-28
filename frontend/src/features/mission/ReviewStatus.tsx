import { CheckCircle2, RefreshCw, GitBranch, MessageCircleQuestion } from 'lucide-react'
import type { Review, MissionStatus } from '../../types/mission'
const icons = {
  PASS: CheckCircle2,
  REVISE: RefreshCw,
  REPLAN: GitBranch,
  BLOCKED: MessageCircleQuestion,
}
const titles = {
  PASS: '方案已通过审查',
  REVISE: '审查要求局部修订',
  REPLAN: '团队正在重新规划',
  BLOCKED: '继续前，需要你的信息',
}
export function ReviewStatus({
  review,
  history,
  status,
}: {
  review: Review | null
  history: Review[]
  status: MissionStatus
}) {
  if (!review) return null
  const Icon = icons[review.decision]
  const resumed = review.decision === 'BLOCKED' && status !== 'BLOCKED'
  return (
    <section className={`review-card review-${review.decision.toLowerCase()}`}>
      <Icon size={21} />
      <div>
        <strong>{resumed ? '已收到补充信息，团队继续处理中' : titles[review.decision]}</strong>
        <p>{resumed ? `上一轮审查：${review.summary}` : review.summary}</p>
        {review.affected_subtasks.length > 0 && (
          <small>受影响步骤：{review.affected_subtasks.join('、')}</small>
        )}
        {review.decision === 'REVISE' && (
          <small>团队会保留其他步骤的结果，继续修订受影响部分。</small>
        )}
        {review.decision === 'REPLAN' && (
          <small>原计划假设需要调整，新的计划版本将在上方显示。</small>
        )}
        {history.length > 1 && (
          <details className="review-history">
            <summary>查看 {history.length} 次审查记录</summary>
            {history.map((item, index) => (
              <p key={index}>
                <b>{item.decision}</b> · {item.summary}
              </p>
            ))}
          </details>
        )}
      </div>
    </section>
  )
}
