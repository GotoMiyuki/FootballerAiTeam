import { FileText, ArrowUpRight, Download } from 'lucide-react'
import { apiUrl } from '../../api/client'
import type { Mission } from '../../types/mission'
export function MissionResult({ mission, onView }: { mission: Mission; onView: () => void }) {
  if (!mission.report) return null
  return (
    <section className="result-card">
      <span className="result-icon">
        <FileText size={25} />
      </span>
      <div>
        <span className="eyebrow">YOUR NEXT MOVE</span>
        <h2>{mission.report.title}</h2>
        <p>独立任务报告 · Plan v{mission.report.plan_version}</p>
      </div>
      <div className="result-actions">
        <button className="primary-button" onClick={onView}>
          查看报告
          <ArrowUpRight size={16} />
        </button>
        <a
          className="icon-button"
          aria-label="导出 Markdown 报告"
          title="导出 Markdown 报告"
          href={apiUrl(`/missions/${encodeURIComponent(mission.id)}/report/download`)}
          download
        >
          <Download size={19} />
        </a>
      </div>
    </section>
  )
}
