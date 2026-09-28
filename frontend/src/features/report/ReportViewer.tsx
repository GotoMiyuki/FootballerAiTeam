import { useQuery } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Download } from 'lucide-react'
import { Dialog } from '../../components/Dialog'
import { Loading, ErrorNotice } from '../../components/ui'
import { getReport } from '../../api/missions'
import { apiUrl } from '../../api/client'
export function ReportViewer({ id, onClose }: { id: string; onClose: () => void }) {
  const query = useQuery({ queryKey: ['report', id], queryFn: () => getReport(id) })
  return (
    <Dialog title="任务报告" onClose={onClose} wide>
      <div className="report-toolbar">
        <span>完整报告 · Markdown</span>
        <a
          className="secondary-button"
          href={apiUrl(`/missions/${encodeURIComponent(id)}/report/download`)}
          download
        >
          <Download size={16} />
          导出 .md
        </a>
      </div>
      {query.isPending ? (
        <Loading label="读取完整报告…" />
      ) : query.isError ? (
        <ErrorNotice message={query.error.message} onRetry={() => void query.refetch()} />
      ) : (
        <article className="markdown-report">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{query.data.markdown}</ReactMarkdown>
        </article>
      )}
    </Dialog>
  )
}
