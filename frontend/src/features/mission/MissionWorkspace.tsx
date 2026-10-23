import { useState } from 'react'
import { Target, ArrowUpRight, Radio, WifiOff, Plus } from 'lucide-react'
import { StatusBadge, ErrorNotice, Loading } from '../../components/ui'
import { useMission } from '../../hooks/useMission'
import { PlanProgress } from './PlanProgress'
import { AgentActivityPanel } from './AgentActivityPanel'
import { ReviewStatus } from './ReviewStatus'
import { BlockedInputPanel } from './BlockedInputPanel'
import { ConversationPanel } from '../conversation/ConversationPanel'
import { MissionResult } from './MissionResult'
import { ReportViewer } from '../report/ReportViewer'
import { ReevaluationPanel } from './ReevaluationPanel'
import { RecommendationsPanel } from './RecommendationsPanel'
const sourceLabels: Record<string, string> = {
  demo_fixture: '测试样本',
  game_observation: '游戏观察',
  user_confirmed: '用户确认',
}
export function MissionWorkspace({
  id,
  draftConversationId,
  demo,
  onMission,
  onNew,
}: {
  id?: string
  draftConversationId: string
  demo: boolean
  onMission: (id: string) => void
  onNew: () => void
}) {
  const query = useMission(id)
  const mission = query.data
  const [report, setReport] = useState(false)
  if (id && query.isPending)
    return (
      <main className="mission-workspace">
        <Loading label="读取历史任务…" />
      </main>
    )
  if (id && query.isError)
    return (
      <main className="mission-workspace">
        <ErrorNotice message={query.error.message} onRetry={() => void query.refetch()} />
        <button className="secondary-button" onClick={onNew}>
          创建新任务
        </button>
      </main>
    )
  return (
    <main className="mission-workspace">
      <div className="workspace-label">
        <span className="eyebrow">MISSION WORKSPACE</span>
        <span className="live-label">
          {id ? (
            query.connection === 'connected' ? (
              <>
                <Radio size={13} />
                实时连接
              </>
            ) : query.connection === 'disconnected' ? (
              <>
                <WifiOff size={13} />
                连接中断
              </>
            ) : (
              '正在连接…'
            )
          ) : (
            <>
              <span className="status-dot" />
              团队已就绪
            </>
          )}
        </span>
      </div>
      <div className="mission-header">
        {mission ? (
          <>
            <div className="mission-header-top">
              <span className="mission-kicker">
                <Target size={14} />
                当前任务
              </span>
              <StatusBadge status={mission.status} />
            </div>
            <h1>{mission.title}</h1>
            <p>{mission.objective}</p>
            <div className="mission-meta">
              <span>{new Date(mission.created_at).toLocaleString('zh-CN')}</span>
              <span>Plan v{mission.plan?.version || '—'}</span>
              <button onClick={onNew}>
                <Plus size={13} />
                新建任务
              </button>
            </div>
            <div className="mission-input-reference">
              <span>任务编号：{mission.id}</span>
              {mission.lineage && (
                <>
                  <button
                    type="button"
                    onClick={() => onMission(mission.lineage!.parent_mission_id)}
                  >
                    查看来源任务
                  </button>
                  <span>发起原因：{mission.lineage.reason}</span>
                  <span>引用历史：{mission.lineage.history_references.length} 项</span>
                </>
              )}
              {mission.input_reference?.verification === 'VERIFIED' ? (
                <>
                  <span>
                    原数据上下文：{mission.input_reference.context?.career_id} /{' '}
                    {mission.input_reference.context?.branch_id} /{' '}
                    {mission.input_reference.context?.player_id}
                  </span>
                  <span>原数据版本：{mission.input_reference.state_version}</span>
                  <span>
                    来源：
                    {mission.input_reference.source_types
                      .map((source) => sourceLabels[source] || '未验证')
                      .join('、') || '未记录'}
                  </span>
                </>
              ) : (
                <span>
                  {mission.input_reference?.verification === 'DEMO'
                    ? '演示任务：确定性样本，不代表实际球员观察'
                    : '旧任务原数据来源与版本未验证'}
                </span>
              )}
              <span>查看历史不会重新执行。解释使用原报告；补信息和审批续跑原任务。</span>
            </div>
          </>
        ) : (
          <>
            <div className="welcome-kicker">
              <span /> YOUR TEAM. YOUR NEXT LEVEL.
            </div>
            <h1>
              让每一次训练，
              <br />
              都有清晰的下一步<span>。</span>
            </h1>
            <p>
              你的专属 AI 团队，从了解你开始。
              <br />
              提出一个目标，剩下的我们一起推进。
            </p>
            <div className="welcome-mark" aria-hidden="true">
              <Target size={102} strokeWidth={0.7} />
              <ArrowUpRight size={43} />
            </div>
          </>
        )}
      </div>
      {id && query.connection === 'disconnected' && (
        <div className="network-notice">
          <WifiOff size={17} />
          实时连接已中断，正在自动重连。当前计划已保留。
        </div>
      )}
      {mission?.error && <ErrorNotice message={mission.error} />}
      {mission?.resume_error && <ErrorNotice message={mission.resume_error} />}
      <div className="workflow-grid">
        <PlanProgress plan={mission?.plan || null} missionStatus={mission?.status} />
        <AgentActivityPanel agents={mission?.agents || []} />
      </div>
      {mission && (
        <ReviewStatus
          review={mission.review}
          history={mission.review_history}
          status={mission.status}
        />
      )}
      {mission?.blocked && mission.status === 'BLOCKED' && (
        <BlockedInputPanel key={`${mission.id}-${mission.blocked.reason}`} mission={mission} />
      )}
      {mission && <MissionResult mission={mission} onView={() => setReport(true)} />}
      {mission && <RecommendationsPanel mission={mission} onReport={() => setReport(true)} />}
      {mission && <ReevaluationPanel key={mission.id} mission={mission} onMission={onMission} />}
      <ConversationPanel
        key={mission?.conversation_id || draftConversationId}
        mission={mission}
        conversationId={mission?.conversation_id || draftConversationId}
        demo={demo}
        onMission={onMission}
        onNew={onNew}
      />
      {mission && (
        <details className="telemetry">
          <summary>
            任务运行信息
            <span>
              {mission.id.slice(-8)} · 事件序号 {mission.sequence}
            </span>
          </summary>
          <dl>
            {Object.entries(mission.telemetry).map(([key, value]) => (
              <div key={key}>
                <dt>{key}</dt>
                <dd>{Number.isInteger(value) ? value : value.toFixed(1)}</dd>
              </div>
            ))}
          </dl>
          {Object.keys(mission.telemetry).length === 0 && <p>本任务暂无模型调用统计。</p>}
        </details>
      )}
      <footer className="workspace-footer">
        <span>FOOTBALLER AI TEAM</span>
        <span>以球员为中心 · 以任务为单位</span>
      </footer>
      {report && mission && <ReportViewer id={mission.id} onClose={() => setReport(false)} />}
    </main>
  )
}
