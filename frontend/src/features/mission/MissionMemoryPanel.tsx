import { useQuery } from '@tanstack/react-query'
import { getFrozenMemory } from '../../api/career-memory'
import { ErrorNotice, Loading } from '../../components/ui'
import type { FrozenMemory } from '../../types/career-memory'
import type { Mission } from '../../types/mission'

const omittedLabels: Record<string, string> = {
  budget: '超出引用预算',
  irrelevant: '与需求不相关',
  inactive_or_unverified: '已更正、撤回或未确认',
  not_yet_effective: '尚未生效',
  unverified_window: '时间窗口无法核验',
  unknown_game_time: '游戏时间不足',
  different_data_mode: '数据模式不同',
  source_withdrawn: '来源已撤回',
  recommendation_not_current: '建议需要重新评估',
  invalid_context: '生涯上下文不同',
  unverifiable_source_events: '来源无法核验',
}
export function MemorySelection({ packet }: { packet: FrozenMemory }) {
  return (
    <>
      <p>
        {packet.items?.length || 0} 条引用
        {packet.as_of && ` · 选择时间 ${new Date(packet.as_of).toLocaleString('zh-CN')}`}
      </p>
      {packet.items?.map((item) => (
        <details key={`${item.memory_id}:${item.revision}`}>
          <summary>
            {item.content} · 版本 {item.revision}
          </summary>
          <p>
            记录：{item.memory_id} · {item.kind} ·{' '}
            {item.usage === 'historical_reference' ? '历史参考' : '明确目标/偏好或当前建议'}
          </p>
          <p>
            来源任务：{item.source.mission_id} ·{' '}
            {item.source.type === 'e_record' ? 'E权威记录' : '用户确认'} · 记录时间{' '}
            {item.recorded_at}
          </p>
          {item.source.object_id && (
            <p>
              来源记录：{item.source.object_id} · 版本 {item.source.object_revision}
            </p>
          )}
          {item.kind === 'assessment' && (
            <p>
              观测前值：{item.projection.before ?? '未知'} · 后值：{item.projection.after ?? '未知'}{' '}
              · 变化：{item.projection.delta ?? '未知'} · 有效样本{' '}
              {item.projection.baseline_samples ?? 0}/{item.projection.followup_samples ?? 0}。
              {item.projection.reason}
            </p>
          )}
          <p>
            生效时间：
            {item.effective_time.effective_at ||
              item.effective_time.valid_window ||
              '未指定时间限制'}
          </p>
          <p>引用摘要：{item.content_hash}</p>
          {item.truncated && <p>内容已截取；来源完整摘要随输入保留。</p>}
          <p>{item.limitations.join('；')}</p>
        </details>
      ))}
      {!!packet.omitted?.length && (
        <p>
          未引用：
          {packet.omitted
            .map((item) => `${omittedLabels[item.reason] || item.reason} ${item.count} 条`)
            .join('；')}
        </p>
      )}
      <p>{packet.limitations?.join('；')}</p>
    </>
  )
}
export function MissionMemoryPanel({ mission }: { mission: Mission }) {
  const query = useQuery({
    queryKey: ['memory-input', mission.id],
    queryFn: () => getFrozenMemory(mission.id),
    enabled: !!mission.memory_reference && mission.memory_reference.verification !== 'LEGACY_NONE',
    staleTime: Infinity,
  })
  return (
    <details className="section-card" aria-label="本次固定记忆输入">
      <summary>本次固定记忆输入 · {mission.memory_reference?.items.length || 0} 条</summary>
      <p>
        此处展示原任务提交时实际选中的记录。解释原报告继续引用本集合；根据新情况重新评估会重新选择并固定。
      </p>
      {!mission.memory_reference || mission.memory_reference.verification === 'LEGACY_NONE' ? (
        <p>旧任务没有固定记忆输入记录。</p>
      ) : query.isPending ? (
        <Loading label="正在读取固定记忆…" />
      ) : query.isError ? (
        <ErrorNotice message={query.error.message} onRetry={() => void query.refetch()} />
      ) : (
        <MemorySelection packet={query.data} />
      )}
    </details>
  )
}
