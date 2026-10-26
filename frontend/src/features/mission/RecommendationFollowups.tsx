import { useQuery } from '@tanstack/react-query'
import { getRecommendationFollowups } from '../../api/missions'
import { ErrorNotice } from '../../components/ui'
import type { Recommendation } from '../../types/recommendation'
export function RecommendationFollowups({
  item,
  onMission,
}: {
  item: Recommendation
  onMission: (id: string) => void
}) {
  const query = useQuery({
    queryKey: ['recommendation-followups', item.source.mission_id, item.recommendation_id],
    queryFn: () => getRecommendationFollowups(item),
  })
  return (
    <details>
      <summary>查看结果调整任务</summary>
      {query.isError && (
        <ErrorNotice message={query.error.message} onRetry={() => void query.refetch()} />
      )}
      {query.data?.items?.map((child) => (
        <p key={child.mission_id}>
          <button type="button" onClick={() => onMission(child.mission_id)}>
            查看调整任务
          </button>{' '}
          · {child.status} · {child.reason} · 新建议 {child.recommendation_ids.length} 条
        </p>
      ))}
      {!query.isPending && !query.isError && !query.data?.items?.length && (
        <p>尚未从此建议发起结果调整。</p>
      )}
    </details>
  )
}
