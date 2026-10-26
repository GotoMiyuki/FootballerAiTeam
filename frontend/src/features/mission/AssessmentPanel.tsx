import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getComparison, createComparison, evaluateComparison } from '../../api/assessments'
import { getRecommendationActivity } from '../../api/missions'
import { FeedbackAdjustment } from './FeedbackAdjustment'
import type { Mission } from '../../types/mission'
import { ErrorNotice, Loading } from '../../components/ui'
import type { Recommendation } from '../../types/recommendation'
import type {
  Assessment,
  AssessmentRequest,
  ComparisonPlan,
  ComparisonRequest,
  Metric,
} from '../../types/assessment'

const labels: Record<Metric, string> = {
  speed: '速度（能力点）',
  finishing: '射门终结（能力点）',
  rating: '比赛评分（0–10）',
  goals: '个人进球（球/场）',
  assists: '个人助攻（次/场）',
  minutes_played: '出场时间（分钟/场）',
}
const evidence = {
  COMPLETE: '证据满足比较规范',
  INSUFFICIENT: '待观察 · 证据不足',
  INCOMPARABLE: '不可比',
}
const goals = { MET: '观测目标达到', NOT_MET: '观测目标未达到', UNDETERMINED: '目标未判定' }
const executionLabels: Record<string, string> = {
  UNKNOWN: '执行未知',
  CONFIRMED: '自报已执行',
  PARTIAL: '自报部分执行',
  NOT_EXECUTED: '自报未执行',
}
const display = (value: number | null) =>
  value === null ? '未知' : Number(value.toFixed(4)).toString()

function AssessmentControl({
  item,
  plan,
  records,
  refresh,
}: {
  item: Recommendation
  plan: ComparisonPlan
  records: Assessment[]
  refresh: () => Promise<unknown>
}) {
  const key = `fait.assessment.${plan.plan_id}`
  const [pending, setPending] = useState<AssessmentRequest | null>(() => {
    try {
      return JSON.parse(localStorage.getItem(key) || 'null')
    } catch {
      return null
    }
  })
  const [executionId, setExecutionId] = useState(pending?.execution_id || '')
  const latest = records.at(-1)
  const activity = useQuery({
    queryKey: [
      'recommendation-activity',
      item.source.mission_id,
      item.recommendation_id,
      item.context,
    ],
    queryFn: () => getRecommendationActivity(item),
  })
  const mutation = useMutation({
    mutationFn: (body: AssessmentRequest) => evaluateComparison(item, plan.plan_id, body),
    retry: false,
    onSuccess: async () => {
      localStorage.removeItem(key)
      setPending(null)
      await refresh()
    },
  })
  return (
    <div className="assessment-controls">
      <label>
        关联执行反馈（可选）
        <select
          aria-label={`关联执行反馈 ${plan.plan_id}`}
          value={executionId}
          disabled={mutation.isPending}
          onChange={(event) => {
            setExecutionId(event.target.value)
            setPending(null)
            localStorage.removeItem(key)
            mutation.reset()
          }}
        >
          <option value="">不关联，保持执行未知</option>
          {(activity.data?.executions || []).map((entry) => (
            <option key={entry.execution_id} value={entry.execution_id}>
              {entry.execution_id} · {executionLabels[entry.status]}
              {activity.data?.current_execution_ids.includes(entry.execution_id)
                ? ''
                : '（更正链原记录）'}
            </option>
          ))}
        </select>
      </label>
      <button
        className="secondary-button"
        disabled={mutation.isPending}
        onClick={() => {
          const body = pending || {
            context: item.context,
            request_id: `assess_${crypto.randomUUID()}`,
            expected_revision: latest?.revision || 0,
            execution_id: executionId || null,
          }
          setPending(body)
          localStorage.setItem(key, JSON.stringify(body))
          mutation.mutate(body)
        }}
      >
        {mutation.isPending ? '比较中…' : pending ? '重试生成/刷新比较' : '生成/刷新比较'}
      </button>
      {mutation.isError && (
        <>
          <ErrorNotice message={mutation.error.message} />
          <button
            className="secondary-button"
            onClick={() => {
              localStorage.removeItem(key)
              setPending(null)
              mutation.reset()
              void refresh()
            }}
          >
            刷新后重新提交比较
          </button>
        </>
      )}
      {activity.isError && (
        <ErrorNotice message={activity.error.message} onRetry={() => void activity.refetch()} />
      )}
    </div>
  )
}

export function AssessmentPanel({
  item,
  mission,
  onMission,
}: {
  item: Recommendation
  mission?: Mission
  onMission?: (id: string) => void
}) {
  const cache = useQueryClient()
  const query = useQuery({
    queryKey: ['comparisons', item.source.mission_id, item.recommendation_id],
    queryFn: () => getComparison(item),
    staleTime: 0,
  })
  const key = `fait.comparison.${item.recommendation_id}`
  const initial = (): ComparisonRequest => ({
    context: item.context,
    request_id: `comparison_${crypto.randomUUID()}`,
    confirmed: true,
    recommendation_revision: item.revision,
    metric: 'rating',
    baseline_count: 1,
    minimum_samples: 1,
    window: { kind: 'next_matches', count: 3, start: null, end: null },
    target_value: null,
    target_operator: 'gte',
  })
  const [draft, setDraft] = useState<ComparisonRequest>(() => {
    try {
      return JSON.parse(localStorage.getItem(key) || 'null') || initial()
    } catch {
      return initial()
    }
  })
  const [notice, setNotice] = useState('')
  async function refresh() {
    await Promise.all([
      query.refetch(),
      cache.invalidateQueries({ queryKey: ['recommendations', item.source.mission_id] }),
    ])
  }
  const create = useMutation({
    mutationFn: (body: ComparisonRequest) => createComparison(item, body),
    retry: false,
    onSuccess: async () => {
      localStorage.removeItem(key)
      setDraft(initial())
      setNotice('比较规范已冻结；后续只读取观察，不调整原基线或目标。')
      await refresh()
    },
  })
  function change(fields: Partial<ComparisonRequest>) {
    const next = { ...draft, ...fields, request_id: `comparison_${crypto.randomUUID()}` }
    setDraft(next)
    localStorage.setItem(key, JSON.stringify(next))
    create.reset()
  }
  const data = query.data
  return (
    <section className="assessment-panel" aria-label="后续观察与比较">
      <details>
        <summary>后续观察与比较</summary>
        <p>
          先确认指标和窗口，再等待后续观察。比较使用原建议基线；只报告观测变化，不证明建议效果或执行。
        </p>
        <button
          className="secondary-button"
          disabled={query.isFetching}
          onClick={() => void refresh()}
        >
          刷新观察状态
        </button>
        {query.isPending && <Loading label="读取比较规范…" />}
        {query.isError && (
          <ErrorNotice message={query.error.message} onRetry={() => void refresh()} />
        )}
        {data && !data.supported && (
          <p>{data.reason || '当前没有可核验的实际观察，不能生成比较。'}</p>
        )}
        {notice && <p role="status">{notice}</p>}
        {data?.supported && (
          <details>
            <summary>确认新的比较规范</summary>
            <form
              className="action-form"
              aria-label="比较规范"
              onSubmit={(event) => {
                event.preventDefault()
                localStorage.setItem(key, JSON.stringify(draft))
                create.mutate(draft)
              }}
            >
              <fieldset disabled={create.isPending}>
                <legend>在新事实出现前确认</legend>
                <p>当前适用性变化后不能追加挑选目标或有利基线；历史规范可继续比较其原窗口。</p>
                <label>
                  比较指标
                  <select
                    aria-label="比较指标"
                    value={draft.metric}
                    onChange={(event) => {
                      const metric = event.target.value as Metric
                      change({
                        metric,
                        ...(['speed', 'finishing'].includes(metric)
                          ? {
                              baseline_count: 1,
                              minimum_samples: 1,
                              window: { kind: 'game_dates', count: null, start: '', end: '' },
                            }
                          : {}),
                      })
                    }}
                  >
                    {Object.entries(labels).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                {!['speed', 'finishing'].includes(draft.metric) && (
                  <label>
                    基线比赛场数
                    <input
                      aria-label="基线比赛场数"
                      type="number"
                      min={1}
                      max={20}
                      required
                      value={draft.baseline_count}
                      onChange={(event) => change({ baseline_count: Number(event.target.value) })}
                    />
                  </label>
                )}
                <label>
                  观察窗口
                  <select
                    aria-label="观察窗口"
                    value={draft.window.kind}
                    onChange={(event) =>
                      change({
                        window:
                          event.target.value === 'next_matches'
                            ? { kind: 'next_matches', count: 3, start: null, end: null }
                            : { kind: 'game_dates', count: null, start: '', end: '' },
                      })
                    }
                  >
                    <option
                      value="next_matches"
                      disabled={['speed', 'finishing'].includes(draft.metric)}
                    >
                      基线游戏日期之后N场
                    </option>
                    <option value="game_dates">明确游戏日期范围</option>
                  </select>
                </label>
                {draft.window.kind === 'next_matches' ? (
                  <label>
                    后续场数
                    <input
                      aria-label="后续场数"
                      required
                      type="number"
                      min={1}
                      max={20}
                      value={draft.window.count || 1}
                      onChange={(event) =>
                        change({ window: { ...draft.window, count: Number(event.target.value) } })
                      }
                    />
                  </label>
                ) : (
                  <>
                    <label>
                      游戏开始日期
                      <input
                        aria-label="游戏开始日期"
                        type="date"
                        required
                        value={draft.window.start || ''}
                        onChange={(event) =>
                          change({ window: { ...draft.window, start: event.target.value } })
                        }
                      />
                    </label>
                    <label>
                      游戏结束日期
                      <input
                        aria-label="游戏结束日期"
                        type="date"
                        required
                        value={draft.window.end || ''}
                        onChange={(event) =>
                          change({ window: { ...draft.window, end: event.target.value } })
                        }
                      />
                    </label>
                  </>
                )}
                <label>
                  每侧最低有效样本
                  <input
                    aria-label="每侧最低有效样本"
                    type="number"
                    required
                    min={1}
                    max={20}
                    disabled={['speed', 'finishing'].includes(draft.metric)}
                    value={draft.minimum_samples}
                    onChange={(event) => change({ minimum_samples: Number(event.target.value) })}
                  />
                </label>
                <label>
                  后续值目标（可选）
                  <input
                    aria-label="后续值目标"
                    type="number"
                    min={0}
                    step="any"
                    value={draft.target_value ?? ''}
                    onChange={(event) =>
                      change({
                        target_value: event.target.value === '' ? null : Number(event.target.value),
                      })
                    }
                  />
                </label>
                <label>
                  目标方向
                  <select
                    aria-label="目标方向"
                    value={draft.target_operator}
                    onChange={(event) =>
                      change({ target_operator: event.target.value as 'gte' | 'lte' })
                    }
                  >
                    <option value="gte">后续值不低于目标</option>
                    <option value="lte">后续值不高于目标</option>
                  </select>
                </label>
                <button className="secondary-button" type="submit">
                  {create.isPending ? '冻结中…' : '确认并冻结比较规范'}
                </button>
                {create.isError && <ErrorNotice message={create.error.message} />}
              </fieldset>
            </form>
          </details>
        )}
        {(data?.plans || []).map((plan) => {
          const records = (data?.assessments || []).filter(
            (record) => record.plan_id === plan.plan_id,
          )
          return (
            <article className="recommendation-item" key={plan.plan_id}>
              <h4>{labels[plan.specification.metric]} · 规范已冻结</h4>
              <p>
                计算规则：
                {plan.metric_definition.method === 'mean'
                  ? '分别计算前后有效样本均值，排除缺值、计入明确零值，再计算后值减前值'
                  : '窗口内最后一次有效能力观察减原基线值'}
                。
              </p>
              <p>
                基线游戏日期边界：{plan.anchor_at}；
                {plan.specification.window.kind === 'next_matches'
                  ? `之后 ${plan.specification.window.count} 场`
                  : `${plan.specification.window.start} 至 ${plan.specification.window.end}`}
              </p>
              <p>
                基线：{plan.baseline_reference.state_version} · 最低有效样本：每侧{' '}
                {plan.specification.minimum_samples} · 量表：{plan.metric_definition.scale}
              </p>
              <p>
                目标：
                {plan.specification.target_value === null
                  ? '未指定，保持未判定'
                  : `${plan.specification.target_operator === 'gte' ? '≥' : '≤'} ${plan.specification.target_value}（后续值）`}
              </p>
              <AssessmentControl item={item} plan={plan} records={records} refresh={refresh} />
              {records.map((record) => (
                <details key={record.revision} open={record === records.at(-1)}>
                  <summary>
                    比较 v{record.revision} · {evidence[record.result.evidence_status]} ·{' '}
                    {record.validity === 'current' ? '来源当前一致' : '历史版本需更新'}
                  </summary>
                  <p>{record.validity_reason}</p>
                  <p>{record.result.reason}</p>
                  <table>
                    <thead>
                      <tr>
                        <th>基线值</th>
                        <th>后续值</th>
                        <th>观测变化</th>
                        <th>有效样本（前/后）</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>{display(record.result.before)}</td>
                        <td>{display(record.result.after)}</td>
                        <td>{display(record.result.delta)}</td>
                        <td>
                          {record.result.baseline_samples} / {record.result.followup_samples}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                  <p>
                    {goals[record.result.goal_status]}；缺失指标：基线{' '}
                    {record.result.baseline_missing} / 后续 {record.result.followup_missing}
                    ；窗口内比赛：{record.result.window_matches}；
                    {record.result.window_complete ? '窗口完成' : '等待窗口完成'}
                  </p>
                  <p>
                    执行关联：{executionLabels[record.result.execution.status]}
                    {record.result.execution.source_type === 'user_reported' && '（用户自报）'}
                  </p>
                  <p>{record.result.missing.join('；')}</p>
                  <p>{record.result.limitations.join('；')}</p>
                  <p>
                    评估编号：{record.assessment_id} · v{record.revision}
                    {record.corrects_revision && ` · 更正原版本 v${record.corrects_revision}`}
                  </p>
                  {mission && onMission && (
                    <FeedbackAdjustment
                      key={`${record.assessment_id}:${record.revision}`}
                      mission={mission}
                      item={item}
                      assessment={record}
                      onMission={onMission}
                    />
                  )}
                  <details>
                    <summary>查看观察来源</summary>
                    {[
                      ...record.result.baseline_observations,
                      ...record.result.followup_observations,
                    ].map((ref, index) => (
                      <p key={`${ref.key}.${index}`}>
                        {ref.key} · 来源记录 {ref.source_record_id || '未知'} ·{' '}
                        {ref.effective_at || '未知日期'} · {ref.game_version || '未知游戏版本'} · 值{' '}
                        {display(ref.value)}
                      </p>
                    ))}
                  </details>
                </details>
              ))}
            </article>
          )
        })}
      </details>
    </section>
  )
}
