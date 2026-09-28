import { Users, Check, LoaderCircle, AlertCircle } from 'lucide-react'
import { SectionTitle, Empty } from '../../components/ui'
import type { AgentActivity } from '../../types/mission'
const roles: Record<string, string> = {
  Manager: '总经理',
  Analyst: '表现分析师',
  Coach: '技能教练',
  Nutrition: '运动营养师',
  Career: '职业经纪人',
  Reviewer: '质量审查',
  Document: '报告生成',
}
export function AgentActivityPanel({ agents }: { agents: AgentActivity[] }) {
  const working = agents.filter((agent) => agent.status === 'RUNNING').length
  return (
    <section className="section-card agent-section">
      <SectionTitle
        eyebrow="YOUR AI TEAM"
        title="团队动态"
        aside={
          <span className="small-label">
            {working ? `${working} 位正在工作` : <Users size={19} />}
          </span>
        }
      />
      {agents.length ? (
        <div className="agent-list">
          {agents.map((agent) => (
            <div className={`agent-row agent-${agent.status.toLowerCase()}`} key={agent.name}>
              <span className="agent-avatar">{agent.name.slice(0, 2).toUpperCase()}</span>
              <div>
                <strong>
                  {agent.name}
                  <small>{roles[agent.name]}</small>
                </strong>
                <p>{agent.activity}</p>
              </div>
              <span className="agent-indicator" aria-label={agent.status}>
                {agent.status === 'RUNNING' ? (
                  <LoaderCircle size={16} className="spin" />
                ) : agent.status === 'COMPLETED' ? (
                  <Check size={16} />
                ) : agent.status === 'FAILED' ? (
                  <AlertCircle size={16} />
                ) : (
                  <span className="circle-small" />
                )}
              </span>
            </div>
          ))}
        </div>
      ) : (
        <Empty>团队准备就绪，等待你的第一个任务。</Empty>
      )}
    </section>
  )
}
