import { useState, useEffect, useRef } from 'react'
import { Send, ArrowUpRight, MessageSquare, Sparkles } from 'lucide-react'
import { useConversation } from '../../hooks/useConversation'
import { ErrorNotice, Loading } from '../../components/ui'
import { isActive, type Mission } from '../../types/mission'
import type { DemoScenario } from '../../types/conversation'
const suggestions = [
  '分析我最近的训练效果',
  '为我制定赛前一周准备方案',
  '昨天踢完球，今天怎么训练？',
]
export function ConversationPanel({
  mission,
  conversationId,
  demo,
  onMission,
  onNew,
}: {
  mission?: Mission
  conversationId: string
  demo: boolean
  onMission: (id: string) => void
  onNew: () => void
}) {
  const [content, setContent] = useState('')
  const [scenario, setScenario] = useState<DemoScenario>('pass')
  const { messages, send } = useConversation(conversationId, onMission)
  const messageList = useRef<HTMLDivElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const followup = !!mission
  const explain = mission?.available_operations?.includes('explain') ?? false
  const supplyText =
    !!mission?.available_operations?.includes('supply_input') &&
    mission.blocked?.required_inputs.length === 1 &&
    mission.blocked.required_inputs[0].key === 'information' &&
    mission.blocked.required_inputs[0].input_type === 'text'
  const disabled =
    send.isPending || isActive(mission?.status) || (!!mission && !explain && !supplyText)
  useEffect(() => {
    const list = messageList.current
    if (list) list.scrollTop = list.scrollHeight
  }, [messages.data?.length])
  function submit() {
    if (disabled || !content.trim()) return
    send.mutate(
      {
        conversation_id: conversationId,
        content: content.trim(),
        mission_id: mission?.id,
        intent: explain ? 'explain' : followup ? 'followup' : 'new_mission',
        demo_scenario: scenario,
      },
      { onSuccess: () => setContent('') },
    )
  }
  return (
    <section className="section-card conversation-section">
      <div className="conversation-heading">
        <h2>
          <MessageSquare size={18} />
          {explain ? '解释原报告' : followup ? '任务对话' : '和团队说说你的目标'}
        </h2>
        <span>
          {explain
            ? '使用原任务材料与原数据版本'
            : followup
              ? '补充信息请使用任务表单'
              : '一个目标，一次团队协作'}
        </span>
      </div>
      {messages.isError && (
        <ErrorNotice message="对话加载失败" onRetry={() => void messages.refetch()} />
      )}
      {messages.isPending ? (
        <Loading label="正在读取对话…" />
      ) : messages.data?.length ? (
        <div className="message-list" aria-live="polite" ref={messageList}>
          {messages.data
            .filter((message) => !mission || message.mission_id === mission.id)
            .map((message) => (
              <div className={`message message-${message.role}`} key={message.id}>
                <span className="message-avatar">
                  {message.role === 'user' ? '你' : <Sparkles size={15} />}
                </span>
                <div>
                  <span className="message-meta">
                    {message.role === 'user'
                      ? '你'
                      : message.role === 'system'
                        ? '系统提示'
                        : 'FootballerAiTeam'}
                    {message.kind === 'explanation' && (
                      <span>{message.operation_status === 'FAILED' ? '解释失败' : '报告解释'}</span>
                    )}
                    <time>
                      {new Date(message.created_at).toLocaleTimeString('zh-CN', {
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </time>
                  </span>
                  <p>{message.content}</p>
                </div>
              </div>
            ))}
        </div>
      ) : (
        <p className="conversation-intro">
          训练、比赛、营养，或是职业发展。告诉我们你想解决什么，团队会一起制定计划。
        </p>
      )}
      {!followup && (
        <div className="suggestions">
          {suggestions.map((text) => (
            <button
              key={text}
              onClick={() => {
                setContent(text)
                textarea.current?.focus()
              }}
            >
              {text}
              <ArrowUpRight size={14} />
            </button>
          ))}
        </div>
      )}
      {mission && ['COMPLETED', 'FAILED'].includes(mission.status) && (
        <div className="suggestions">
          <span>新比赛或新约束需要新任务；原报告与审查结果保留。</span>
          <button type="button" onClick={onNew}>
            根据新情况新建任务
            <ArrowUpRight size={14} />
          </button>
        </div>
      )}
      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault()
          submit()
        }}
      >
        <textarea
          ref={textarea}
          aria-label={explain ? '报告解释问题' : followup ? '补充任务信息' : '任务需求'}
          placeholder={
            explain
              ? '例如：请解释原报告中这样安排的依据…'
              : followup
                ? '请使用上方表单补充信息或确认生成报告…'
                : '例如：新赛季还有一周，我该怎么准备？'
          }
          value={content}
          onChange={(event) => setContent(event.target.value)}
          rows={3}
          maxLength={8000}
          disabled={disabled}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
              event.preventDefault()
              submit()
            }
          }}
        />
        <div className="composer-footer">
          <span>
            {isActive(mission?.status)
              ? '团队正在工作，任务暂停或完成后可继续对话'
              : 'Ctrl + Enter 发送'}
          </span>
          {demo && !followup && (
            <select
              aria-label="演示场景"
              value={scenario}
              onChange={(event) => setScenario(event.target.value as DemoScenario)}
            >
              <option value="pass">演示：一次通过</option>
              <option value="revision">演示：局部修订</option>
              <option value="blocked">演示：补充信息</option>
              <option value="replan">演示：重新规划</option>
            </select>
          )}
          <button type="submit" className="primary-button" disabled={disabled || !content.trim()}>
            {send.isPending
              ? '正在发送…'
              : explain
                ? '解释报告'
                : followup
                  ? '补充信息'
                  : '创建任务'}
            <Send size={15} />
          </button>
        </div>
      </form>
      {send.isError && <ErrorNotice message={send.error.message} />}
    </section>
  )
}
