import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getRecommendationActivity, recordDecision, recordExecution } from '../../api/missions'
import { ErrorNotice, Loading } from '../../components/ui'
import type {
  Recommendation,
  DecisionStatus,
  ExecutionStatus,
  ExecutionWindow,
  DecisionRequest,
  ExecutionRequest,
} from '../../types/recommendation'

const decisionLabels: Record<DecisionStatus, string> = {
  ADOPTED: '采纳',
  REJECTED: '拒绝',
  DEFERRED: '暂缓',
}
const executionLabels: Record<ExecutionStatus, string> = {
  UNKNOWN: '未知',
  CONFIRMED: '自报已执行',
  PARTIAL: '自报部分执行',
  NOT_EXECUTED: '自报未执行',
}
const domainLabels: Record<ExecutionWindow['time_domain'], string> = {
  unknown: '未知窗口',
  real: '现实时间',
  game: '游戏时间',
  match: '比赛范围',
}

// Preserve the exact submitted body across lost responses and reloads. Editing creates a new request.
function useActionDraft<T, R>(key: string, initial: T) {
  const [draft, setDraft] = useState<{ values: T; submitted: R | null }>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(key) || 'null')
      if (saved?.values && 'submitted' in saved) return saved
    } catch {
      /* Damaged local drafts do not affect saved server history. */
    }
    return { values: initial, submitted: null }
  })
  function save(next: typeof draft) {
    localStorage.setItem(key, JSON.stringify(next))
    setDraft(next)
  }
  return {
    ...draft,
    change: (fields: Partial<T>) =>
      save({ values: { ...draft.values, ...fields }, submitted: null }),
    submit: (build: () => R) => {
      const body = draft.submitted || build()
      save({ ...draft, submitted: body })
      return body
    },
    clear: () => {
      localStorage.removeItem(key)
      setDraft({ values: initial, submitted: null })
    },
  }
}

export function RecommendationActivity({ item }: { item: Recommendation }) {
  const cache = useQueryClient()
  const key = [
    'recommendation-activity',
    item.source.mission_id,
    item.recommendation_id,
    item.context,
  ]
  const query = useQuery({
    queryKey: key,
    queryFn: () => getRecommendationActivity(item),
    staleTime: 0,
  })
  const prefix = `fait.action.${item.source.mission_id}.${item.recommendation_id}.${item.revision}`
  const choice = useActionDraft<{ status: DecisionStatus; reason: string }, DecisionRequest>(
    `${prefix}.decision`,
    { status: 'DEFERRED', reason: '' },
  )
  const feedback = useActionDraft<
    {
      status: ExecutionStatus
      actual_action: string
      window: ExecutionWindow
      corrects_execution_id: string | null
      correction_reason: string
    },
    ExecutionRequest
  >(`${prefix}.execution`, {
    status: 'UNKNOWN',
    actual_action: '',
    window: { time_domain: 'unknown', label: null },
    corrects_execution_id: null,
    correction_reason: '',
  })
  const [notice, setNotice] = useState('')
  async function refresh() {
    await Promise.all([
      cache.invalidateQueries({ queryKey: key }),
      cache.invalidateQueries({ queryKey: ['recommendations', item.source.mission_id] }),
      cache.invalidateQueries({ queryKey: ['career-memory'] }),
      cache.invalidateQueries({ queryKey: ['comparisons'] }),
    ])
  }
  const decision = useMutation({
    mutationFn: (body: DecisionRequest) => recordDecision(item, body),
    retry: false,
    onSuccess: async () => {
      choice.clear()
      setNotice('选择已保存；执行情况独立记录。')
      await refresh()
    },
  })
  const execution = useMutation({
    mutationFn: (body: ExecutionRequest) => recordExecution(item, body),
    retry: false,
    onSuccess: async () => {
      feedback.clear()
      setNotice('执行反馈已保存，来源为用户自报。')
      await refresh()
    },
  })
  const data = query.data
  const currentChoice = data?.decisions.find(
    (entry) => entry.decision_id === data.current_decision_id,
  )
  const currentExecutions =
    data?.executions.filter((entry) => data.current_execution_ids.includes(entry.execution_id)) ||
    []
  const common = () => ({
    request_id: `action_${crypto.randomUUID()}`,
    recommendation_revision: item.revision,
    context: item.context,
  })
  const busy = decision.isPending || execution.isPending || query.isFetching
  return (
    <div className="recommendation-activity" aria-label="选择与执行">
      {query.isPending && <Loading label="读取选择与执行…" />}
      {query.isError && (
        <ErrorNotice message={query.error.message} onRetry={() => void query.refetch()} />
      )}
      {data && (
        <>
          <p>
            <strong>选择：</strong>
            {currentChoice ? decisionLabels[currentChoice.status] : '尚未选择'}
          </p>
          <p>
            <strong>执行：</strong>
            {currentExecutions.length
              ? currentExecutions
                  .map(
                    (entry) =>
                      `${executionLabels[entry.status]}（${domainLabels[entry.window.time_domain]}${entry.window.label ? '：' + entry.window.label : ''}）`,
                  )
                  .join('；')
              : '未知 · 尚无执行反馈'}
          </p>
          <p>
            <strong>等待观察：</strong>尚未关联后续观察，不判断效果或能力变化。
          </p>
          {notice && <p role="status">{notice}</p>}
          <details>
            <summary>记录选择与执行反馈</summary>
            <p>
              采纳不代表执行。更改选择可撤回采纳，过去的执行反馈仍保留。自报数值不会写入球员事实。
            </p>
            <form
              className="action-form"
              aria-label="玩家选择"
              onSubmit={(event) => {
                event.preventDefault()
                decision.mutate(
                  choice.submit(() => ({
                    ...common(),
                    ...choice.values,
                    previous_decision_id: data.current_decision_id,
                  })),
                )
              }}
            >
              <fieldset disabled={busy}>
                <legend>玩家选择</legend>
                <label>
                  选择
                  <select
                    aria-label="玩家选择"
                    value={choice.values.status}
                    onChange={(event) => {
                      choice.change({ status: event.target.value as DecisionStatus })
                      decision.reset()
                    }}
                  >
                    <option value="ADOPTED" disabled={item.validity !== 'current'}>
                      采纳
                    </option>
                    <option value="REJECTED">拒绝</option>
                    <option value="DEFERRED">暂缓</option>
                  </select>
                </label>
                <label>
                  选择理由（可选）
                  <textarea
                    aria-label="选择理由"
                    maxLength={2000}
                    value={choice.values.reason}
                    onChange={(event) => {
                      choice.change({ reason: event.target.value })
                      decision.reset()
                    }}
                  />
                </label>
                {item.validity !== 'current' && (
                  <p>当前适用性不足，需重新评估后采纳；仍可拒绝或暂缓原建议。</p>
                )}
                <button
                  className="secondary-button"
                  type="submit"
                  disabled={
                    !choice.submitted &&
                    choice.values.status === 'ADOPTED' &&
                    item.validity !== 'current'
                  }
                >
                  {decision.isPending ? '保存中…' : choice.submitted ? '重试保存选择' : '保存选择'}
                </button>
                {decision.isError && (
                  <>
                    <ErrorNotice message={decision.error.message} />
                    <button
                      className="secondary-button"
                      type="button"
                      onClick={() => {
                        choice.change({})
                        decision.reset()
                        void query.refetch()
                      }}
                    >
                      刷新并重新填写选择
                    </button>
                  </>
                )}
              </fieldset>
            </form>
            <form
              className="action-form"
              aria-label="执行反馈"
              onSubmit={(event) => {
                event.preventDefault()
                execution.mutate(feedback.submit(() => ({ ...common(), ...feedback.values })))
              }}
            >
              <fieldset disabled={busy}>
                <legend>独立执行反馈</legend>
                <p>来源：用户自报。无需先采纳；“自报已执行”只记录明确反馈，游戏操作仍待验证。</p>
                <label>
                  记录方式
                  <select
                    aria-label="执行记录方式"
                    value={feedback.values.corrects_execution_id || ''}
                    onChange={(event) => {
                      feedback.change({
                        corrects_execution_id: event.target.value || null,
                        correction_reason: '',
                      })
                      execution.reset()
                    }}
                  >
                    <option value="">新增一次反馈</option>
                    {currentExecutions.map((entry) => (
                      <option key={entry.execution_id} value={entry.execution_id}>
                        更正 {entry.execution_id} · {executionLabels[entry.status]}
                      </option>
                    ))}
                    {feedback.values.corrects_execution_id &&
                      !data.current_execution_ids.includes(
                        feedback.values.corrects_execution_id,
                      ) && (
                        <option value={feedback.values.corrects_execution_id}>
                          原提交的更正目标（可原样重试）
                        </option>
                      )}
                  </select>
                </label>
                <label>
                  执行情况
                  <select
                    aria-label="执行情况"
                    value={feedback.values.status}
                    onChange={(event) => {
                      feedback.change({ status: event.target.value as ExecutionStatus })
                      execution.reset()
                    }}
                  >
                    {Object.entries(executionLabels).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  实际行动或未知说明
                  <textarea
                    aria-label="实际行动或未知说明"
                    required
                    maxLength={4000}
                    value={feedback.values.actual_action}
                    onChange={(event) => {
                      feedback.change({ actual_action: event.target.value })
                      execution.reset()
                    }}
                  />
                </label>
                <label>
                  时间域
                  <select
                    aria-label="执行时间域"
                    value={feedback.values.window.time_domain}
                    onChange={(event) => {
                      const domain = event.target.value as ExecutionWindow['time_domain']
                      feedback.change({
                        window: { time_domain: domain, label: domain === 'unknown' ? null : '' },
                      })
                      execution.reset()
                    }}
                  >
                    {Object.entries(domainLabels).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                {feedback.values.window.time_domain !== 'unknown' && (
                  <label>
                    实际时间或比赛范围
                    <input
                      aria-label="实际时间或比赛范围"
                      required
                      maxLength={500}
                      value={feedback.values.window.label || ''}
                      onChange={(event) => {
                        feedback.change({
                          window: { ...feedback.values.window, label: event.target.value },
                        })
                        execution.reset()
                      }}
                    />
                  </label>
                )}
                {feedback.values.corrects_execution_id && (
                  <label>
                    更正原因
                    <textarea
                      aria-label="更正原因"
                      required
                      maxLength={2000}
                      value={feedback.values.correction_reason}
                      onChange={(event) => {
                        feedback.change({ correction_reason: event.target.value })
                        execution.reset()
                      }}
                      placeholder="撤回原反馈时，将执行情况改为未知并说明原因"
                    />
                  </label>
                )}
                <button className="secondary-button" type="submit">
                  {execution.isPending
                    ? '保存中…'
                    : feedback.submitted
                      ? '重试保存执行反馈'
                      : '保存执行反馈'}
                </button>
                {execution.isError && <ErrorNotice message={execution.error.message} />}
              </fieldset>
            </form>
          </details>
          <details>
            <summary>
              查看选择与执行历史（{data.decisions.length + data.executions.length}）
            </summary>
            <p>下列时间为现实提交时间；各执行窗口保持原时间域，不据此推断游戏先后。</p>
            <ul className="action-history">
              {data.decisions.map((entry) => (
                <li key={entry.decision_id}>
                  <strong>选择：{decisionLabels[entry.status]}</strong> · {entry.created_at}
                  <p>{entry.reason || '未填写理由'}</p>
                  <small>
                    {entry.decision_id} · v{entry.revision}
                    {entry.previous_decision_id && ` · 前次选择 ${entry.previous_decision_id}`}
                    {entry.decision_id === data.current_decision_id ? ' · 当前选择' : ' · 历史选择'}
                  </small>
                </li>
              ))}
              {data.executions.map((entry) => (
                <li key={entry.execution_id}>
                  <strong>执行：{executionLabels[entry.status]}</strong> · 用户自报 ·{' '}
                  {entry.created_at}
                  <p>{entry.actual_action}</p>
                  <p>
                    {domainLabels[entry.window.time_domain]}
                    {entry.window.label && `：${entry.window.label}`}
                  </p>
                  <small>
                    {entry.execution_id} · v{entry.revision}
                    {entry.corrects_execution_id &&
                      ` · 更正 ${entry.corrects_execution_id}：${entry.correction_reason}`}
                    {data.current_execution_ids.includes(entry.execution_id)
                      ? ' · 当前反馈'
                      : ' · 已被更正'}
                  </small>
                </li>
              ))}
            </ul>
          </details>
        </>
      )}
    </div>
  )
}
