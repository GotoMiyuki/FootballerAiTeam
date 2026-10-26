import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { getCareerMemory, syncCareerMemory, writeCareerMemory } from '../../api/career-memory'
import { ErrorNotice, Loading } from '../../components/ui'
import type { Mission } from '../../types/mission'
import type { CareerMemory, MemoryWriteRequest, MemoryTime } from '../../types/career-memory'

const kindLabels: Record<CareerMemory['kind'], string> = {
  goal: '明确目标',
  preference: '明确偏好',
  recommendation: '建议引用',
  decision: '选择引用',
  execution: '执行反馈引用',
  assessment: '观测比较引用',
}
const statusLabels: Record<CareerMemory['status'], string> = {
  active: '保留',
  candidate: '待用户确认的候选',
  superseded: '已被更正或替代',
  retracted: '已撤回或来源不可用',
}
const actionLabels = {
  create: '确认保存目标/偏好',
  correct: '确认更正',
  retract: '确认撤回',
  confirm_candidate: '明确确认候选',
}
const eStatusLabels: Record<string, string> = {
  ADOPTED: '采纳',
  REJECTED: '拒绝',
  DEFERRED: '暂缓',
  UNKNOWN: '未知',
  CONFIRMED: '自报已执行',
  PARTIAL: '自报部分执行',
  NOT_EXECUTED: '自报未执行',
}
const domainLabels = {
  unknown: '未知时间域',
  real: '现实时间',
  game: '游戏时间',
  match: '比赛范围',
}
const precisionLabels: Record<MemoryTime['precision'], string> = {
  unknown: '未知',
  unspecified: '未指定',
  instant: '时刻',
  day: '日',
  month: '月',
  week: '周',
}
const unknownTime = (): MemoryTime => ({
  time_domain: 'unknown',
  precision: 'unknown',
  effective_at: null,
  valid_window: null,
})

export function CareerMemoryPanel({ mission }: { mission: Mission }) {
  const context = mission.input_reference?.context
  const allowed = !!context && mission.input_reference?.verification !== 'UNVERIFIED'
  const query = useQuery({
    queryKey: ['career-memory', mission.id],
    queryFn: () => getCareerMemory(mission.id),
    enabled: allowed,
    staleTime: 0,
  })
  const draftKey = `fait.memory.${mission.id}`
  const blank = (): MemoryWriteRequest => ({
    context: context!,
    request_id: `memory_${crypto.randomUUID()}`,
    operation: 'create',
    confirmed: true,
    kind: 'preference',
    content: '',
    effective_time: unknownTime(),
    memory_id: null,
    expected_revision: null,
    reason: '',
  })
  const [draft, setDraft] = useState<MemoryWriteRequest>(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(draftKey) || 'null')
      if (
        saved?.request_id &&
        saved.effective_time &&
        JSON.stringify(saved.context) === JSON.stringify(context)
      )
        return saved
    } catch {
      /* A damaged draft cannot change durable memory. */
    }
    return blank()
  })
  const [notice, setNotice] = useState('')
  const [editing, setEditing] = useState(false)
  const sync = useMutation({
    mutationFn: () => syncCareerMemory(mission.id),
    retry: false,
    onSuccess: async (result) => {
      setNotice(
        `已同步 ${result.consumed_events} 条E事件，新增 ${result.created_memories} 条来源记忆。`,
      )
      await query.refetch()
    },
  })
  const write = useMutation({
    mutationFn: (body: MemoryWriteRequest) => writeCareerMemory(mission.id, body),
    retry: false,
    onSuccess: async () => {
      localStorage.removeItem(draftKey)
      setDraft(blank())
      setNotice('记忆已保存，原版本与来源保留。')
      await query.refetch()
    },
  })
  function change(fields: Partial<MemoryWriteRequest>) {
    const next = { ...draft, ...fields, request_id: `memory_${crypto.randomUUID()}` }
    localStorage.setItem(draftKey, JSON.stringify(next))
    setDraft(next)
    write.reset()
  }
  function select(record: CareerMemory, operation: MemoryWriteRequest['operation']) {
    setEditing(true)
    change({
      operation,
      memory_id: record.memory_id,
      expected_revision: record.revision,
      kind: record.kind as 'goal' | 'preference',
      content: record.content,
      effective_time: record.effective_time,
      reason: '',
    })
  }
  if (!allowed) return null
  const items = query.data?.items || []
  const busy = write.isPending || sync.isPending || query.isFetching
  return (
    <section className="recommendations-panel career-memory-panel" aria-label="生涯记忆">
      <div className="recommendations-heading">
        <h2>生涯记忆</h2>
        <div className="memory-actions">
          <button className="secondary-button" disabled={busy} onClick={() => void query.refetch()}>
            刷新记忆状态
          </button>
          <button className="secondary-button" disabled={busy} onClick={() => sync.mutate()}>
            同步E来源记录
          </button>
        </div>
      </div>
      <p>
        范围：{context.career_id} / {context.branch_id} / {context.player_id}
        。目标/偏好由你明确确认；建议、行动与观测比较从E读取。新任务按相关性引用，提交后固定；此处更正不改变旧任务输入。
      </p>
      {query.isPending && <Loading label="读取生涯记忆…" />}
      {query.isError && (
        <ErrorNotice message={query.error.message} onRetry={() => void query.refetch()} />
      )}
      {sync.isError && <ErrorNotice message={sync.error.message} />}
      {notice && <p role="status">{notice}</p>}
      <details open={editing} onToggle={(event) => setEditing(event.currentTarget.open)}>
        <summary>设置目标或偏好</summary>
        <form
          className="action-form"
          aria-label="确认生涯记忆"
          onSubmit={(event) => {
            event.preventDefault()
            localStorage.setItem(draftKey, JSON.stringify(draft))
            write.mutate(draft)
          }}
        >
          <fieldset disabled={busy}>
            <legend>{actionLabels[draft.operation]}</legend>
            {draft.memory_id && (
              <p>
                原记录：{draft.memory_id} · v{draft.expected_revision}
              </p>
            )}
            <label>
              类别
              <select
                aria-label="记忆类别"
                value={draft.kind}
                disabled={draft.operation !== 'create'}
                onChange={(event) => change({ kind: event.target.value as 'goal' | 'preference' })}
              >
                <option value="goal">明确目标</option>
                <option value="preference">明确偏好</option>
              </select>
            </label>
            <label>
              确认内容
              <textarea
                aria-label="记忆确认内容"
                required={draft.operation !== 'retract'}
                maxLength={2000}
                disabled={draft.operation === 'retract'}
                value={draft.content}
                onChange={(event) => change({ content: event.target.value })}
              />
            </label>
            {draft.operation !== 'retract' && (
              <>
                <label>
                  生效范围时间域
                  <select
                    aria-label="记忆时间域"
                    value={draft.effective_time.time_domain}
                    onChange={(event) => {
                      const domain = event.target.value as MemoryTime['time_domain']
                      change({
                        effective_time:
                          domain === 'unknown'
                            ? unknownTime()
                            : {
                                time_domain: domain,
                                precision: 'unspecified',
                                effective_at: null,
                                valid_window: '',
                              },
                      })
                    }}
                  >
                    {Object.entries(domainLabels).map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                {draft.effective_time.time_domain !== 'unknown' && (
                  <label>
                    生效窗口描述
                    <input
                      aria-label="记忆生效窗口"
                      required
                      maxLength={500}
                      value={draft.effective_time.valid_window || ''}
                      onChange={(event) =>
                        change({
                          effective_time: {
                            ...draft.effective_time,
                            valid_window: event.target.value,
                          },
                        })
                      }
                    />
                  </label>
                )}
              </>
            )}
            {['correct', 'retract'].includes(draft.operation) && (
              <label>
                变更原因
                <textarea
                  aria-label="记忆变更原因"
                  required
                  maxLength={1000}
                  value={draft.reason}
                  onChange={(event) => change({ reason: event.target.value })}
                />
              </label>
            )}
            <div className="memory-actions">
              <button className="secondary-button" type="submit">
                {write.isPending ? '保存中…' : actionLabels[draft.operation]}
              </button>
              <button
                className="secondary-button"
                type="button"
                onClick={() => {
                  localStorage.removeItem(draftKey)
                  setDraft(blank())
                  write.reset()
                  void query.refetch()
                }}
              >
                重新填写新记录
              </button>
            </div>
            {write.isError && <ErrorNotice message={write.error.message} />}
          </fieldset>
        </form>
      </details>
      <details>
        <summary>查看记忆与来源历史（{items.length}）</summary>
        {items.length === 0 && (
          <p>暂无记忆。同步只消费已有E记录，不从聊天或档案目标推断行动、偏好。</p>
        )}
        {items.map((item) => (
          <article className="recommendation-item" key={`${item.memory_id}.${item.revision}`}>
            <h3>
              {kindLabels[item.kind]} · {statusLabels[item.status]} ·{' '}
              {item.usage === 'current'
                ? '来源当前可用'
                : item.usage === 'unconfirmed'
                  ? '未确认'
                  : '仅作历史参考'}
            </h3>
            <p>
              {item.kind === 'decision' && item.projection.status
                ? `玩家选择：${eStatusLabels[item.projection.status] || item.projection.status}`
                : item.content}
            </p>
            {item.projection.status && (
              <p>原记录状态：{eStatusLabels[item.projection.status] || item.projection.status}</p>
            )}
            {item.kind === 'execution' && <p>来源：用户自报；不等于游戏核验或能力变化。</p>}
            {item.projection.reason && <p>原理由：{item.projection.reason}</p>}
            <p>{item.status_reason}</p>
            <p>
              时间域：{domainLabels[item.effective_time.time_domain]} · 精度：
              {precisionLabels[item.effective_time.precision]} · 生效时点：
              {item.effective_time.effective_at || '未知'} · 窗口：
              {item.effective_time.valid_window || '未知'}
            </p>
            <small>
              {item.memory_id} · v{item.revision} · 记录于 {item.recorded_at}
            </small>
            {item.corrects_revision && (
              <p>
                更正原版本：v{item.corrects_revision}；{item.change_reason}
              </p>
            )}
            {item.supersedes_memory_id && <p>替代原引用：{item.supersedes_memory_id}</p>}
            <p>
              来源任务：{item.source.mission_id} ·{' '}
              {item.source.type === 'e_record'
                ? 'E权威引用'
                : item.source.type === 'model_candidate'
                  ? '模型候选'
                  : '用户明确确认'}
            </p>
            {item.source.event_id && (
              <p>
                来源事件：{item.source.event_type} / {item.source.event_id} · 对象{' '}
                {item.source.object_id} v{item.source.object_revision}
              </p>
            )}
            {item.source.recommendation_source && (
              <p>
                成果来源：{item.source.recommendation_source.subtask_id} · v
                {item.source.recommendation_source.result_version} ·{' '}
                {item.source.recommendation_source.payload_position}
              </p>
            )}
            {item.source.confirmed_candidate_revision && (
              <p>明确确认候选原版本：v{item.source.confirmed_candidate_revision}</p>
            )}
            {item.projection.truncated && <p>短投影已截断，完整内容保留在来源记录。</p>}
            {item.source.type !== 'e_record' && ['active', 'candidate'].includes(item.status) && (
              <div className="memory-actions">
                {item.status === 'candidate' ? (
                  <button
                    className="secondary-button"
                    disabled={busy}
                    onClick={() => select(item, 'confirm_candidate')}
                  >
                    选择确认此候选
                  </button>
                ) : (
                  <button
                    className="secondary-button"
                    disabled={busy}
                    onClick={() => select(item, 'correct')}
                  >
                    更正此记忆
                  </button>
                )}
                <button
                  className="secondary-button"
                  disabled={busy}
                  onClick={() => select(item, 'retract')}
                >
                  撤回此记忆
                </button>
              </div>
            )}
          </article>
        ))}
      </details>
    </section>
  )
}
