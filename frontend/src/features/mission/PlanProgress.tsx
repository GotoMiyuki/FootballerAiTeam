import { ListChecks, AlertCircle } from 'lucide-react'
import { SectionTitle, TaskIcon, Empty } from '../../components/ui'
import type { Plan, Subtask, MissionStatus } from '../../types/mission'
const labels = {
  PENDING: '等待执行',
  RUNNING: '正在执行',
  COMPLETED: '已完成',
  REVISION_REQUIRED: '需要修订',
  BLOCKED: '等待信息',
  SKIPPED: '已跳过',
  FAILED: '未输出结果',
  INVALIDATED: '结果已失效',
}
export function PlanProgress({
  plan,
  missionStatus,
}: {
  plan: Plan | null
  missionStatus?: MissionStatus
}) {
  const complete =
    plan?.subtasks.filter((task) => task.status === 'COMPLETED' || task.status === 'SKIPPED')
      .length || 0
  const total = plan?.subtasks.length || 0
  return (
    <section className="section-card plan-section">
      <SectionTitle
        eyebrow="THE GAME PLAN"
        title="计划与进度"
        aside={
          plan ? (
            <span className="version-tag">PLAN v{plan.version}</span>
          ) : (
            <ListChecks size={20} className="muted" />
          )
        }
      />
      {plan && total > 0 ? (
        <>
          <div className="progress-caption">
            <span>
              {missionStatus === 'FAILED'
                ? '任务执行中断，已保留计划'
                : complete === total
                  ? '计划步骤已完成'
                  : '团队正按计划推进'}
            </span>
            <strong>
              {complete}
              <small> / {total} 项</small>
            </strong>
          </div>
          <div className="progress-track">
            <i style={{ width: `${(complete / total) * 100}%` }} />
          </div>
          <ol className="subtasks">
            {plan.subtasks.map((task, index) => (
              <SubtaskItem
                key={task.id}
                task={task}
                index={index}
                interrupted={missionStatus === 'FAILED' && task.status === 'RUNNING'}
              />
            ))}
          </ol>
          {plan.reason && <p className="plan-reason">计划更新：{plan.reason}</p>}
        </>
      ) : (
        <Empty>任务开始后，Manager 会在这里建立执行计划。</Empty>
      )}
    </section>
  )
}
function SubtaskItem({
  task,
  index,
  interrupted,
}: {
  task: Subtask
  index: number
  interrupted: boolean
}) {
  return (
    <li className={`subtask subtask-${interrupted ? 'interrupted' : task.status.toLowerCase()}`}>
      <span className="task-state">
        {interrupted ? <AlertCircle size={16} /> : <TaskIcon status={task.status} />}
      </span>
      <div>
        <div className="task-title">
          <span className="step-number">{String(index + 1).padStart(2, '0')}</span>
          <strong>{task.title}</strong>
        </div>
        <p>
          {task.assigned_agent}
          <span>·</span>
          {interrupted ? '执行中断' : labels[task.status]}
          {task.revision_count > 0 && (
            <span className="revision-count">修订 {task.revision_count} 次</span>
          )}
        </p>
        {task.reason && <small>{task.reason}</small>}
        {task.result_summary && <details><summary>查看已保留的专业结果</summary><p>{task.result_summary}</p></details>}
      </div>
    </li>
  )
}
