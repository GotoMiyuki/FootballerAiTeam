import { useQuery } from '@tanstack/react-query'
import { ListChecks, RefreshCw } from 'lucide-react'
import { getRecommendations } from '../../api/missions'
import { ErrorNotice, Loading } from '../../components/ui'
import type { Mission } from '../../types/mission'
import type { Recommendation } from '../../types/recommendation'

const validityLabels: Record<Recommendation['validity'], string> = {
  current: '来源有效 · 数据一致',
  needs_reassessment: '当前适用性待重评',
  superseded: '已被替代 · 历史记录',
  withdrawn: '来源不可用 · 历史记录',
}

export function RecommendationsPanel({
  mission,
  onReport,
}: {
  mission: Mission
  onReport: () => void
}) {
  const query = useQuery({
    queryKey: ['recommendations', mission.id, mission.sequence],
    queryFn: () => getRecommendations(mission.id),
    enabled: ['COMPLETED', 'FAILED'].includes(mission.status),
    staleTime: 0,
  })
  if (!['COMPLETED', 'FAILED'].includes(mission.status)) return null
  const items = query.data?.items || []
  return (
    <section className="recommendations-panel" aria-label="训练焦点建议">
      <div className="recommendations-heading">
        <h2>
          <ListChecks size={19} />
          训练焦点建议
        </h2>
        <button
          className="secondary-button"
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
        >
          <RefreshCw size={13} />
          刷新适用性
        </button>
      </div>
      <p className="recommendations-intro">
        每条建议保留原成果和数据版本。游戏操作支持仍待验证；建议不表示已采纳或执行。
      </p>
      {query.isPending && <Loading label="读取建议来源…" />}
      {query.isError && (
        <ErrorNotice message={query.error.message} onRetry={() => void query.refetch()} />
      )}
      {query.data && items.length === 0 && (
        <p className="recommendations-empty">{query.data.reason || '暂无可验证的训练焦点记录。'}</p>
      )}
      {items.map((item) => (
        <article
          className={`recommendation-item recommendation-${item.validity}`}
          key={item.recommendation_id}
        >
          <div className="recommendation-title">
            <h3>{item.content.title}</h3>
            <span>{validityLabels[item.validity]}</span>
          </div>
          <p>{item.validity_reason}</p>
          <p>
            <strong>预期目标：</strong>
            {item.content.expected_goal}
          </p>
          <p>
            <strong>执行支持：</strong>待验证。{item.execution_support.reason}
          </p>
          <details>
            <summary>查看来源与适用范围</summary>
            <dl>
              <div>
                <dt>来源任务 / 专业结果</dt>
                <dd>
                  {item.source.mission_id} / {item.source.subtask_id} · v
                  {item.source.result_version}
                </dd>
              </div>
              <div>
                <dt>条目与审查</dt>
                <dd>
                  {item.source.payload_position} · Plan v{item.source.review_plan_version} ·
                  已审查结果 v{item.source.reviewed_result_version}
                </dd>
              </div>
              <div>
                <dt>生涯 / 分支 / 球员</dt>
                <dd>
                  {item.context.career_id} / {item.context.branch_id} / {item.context.player_id}
                </dd>
              </div>
              <div>
                <dt>固定基线</dt>
                <dd>
                  {item.applicability.input_reference.state_version}（
                  {item.applicability.input_reference.source_types.includes('demo_fixture')
                    ? '测试样本'
                    : '有来源观察'}
                  ）
                </dd>
              </div>
              <div>
                <dt>依据</dt>
                <dd>{item.content.basis}</dd>
              </div>
              <div>
                <dt>适用条件</dt>
                <dd>{item.applicability.conditions.join('；') || '来源未指定额外条件'}</dd>
              </div>
              <div>
                <dt>游戏版本 / 模式</dt>
                <dd>
                  {item.applicability.game_versions.join('、') || '未知'} /{' '}
                  {item.applicability.game_mode || '未知'}
                </dd>
              </div>
              <div>
                <dt>有效窗口</dt>
                <dd>{item.applicability.valid_window || item.applicability.window_reason}</dd>
              </div>
              <div>
                <dt>比较指标与观察窗口</dt>
                <dd>{item.evaluation_spec.reason}</dd>
              </div>
              <div>
                <dt>限制</dt>
                <dd>{item.content.limitations.join('；')}</dd>
              </div>
              <div>
                <dt>建议编号</dt>
                <dd>
                  {item.recommendation_id} · 修订 {item.revision}
                </dd>
              </div>
            </dl>
            {mission.status === 'COMPLETED' &&
              mission.report &&
              mission.delivery_status === 'PUBLISHABLE' && (
                <button className="secondary-button" onClick={onReport}>
                  查看来源报告
                </button>
              )}
          </details>
        </article>
      ))}
    </section>
  )
}
