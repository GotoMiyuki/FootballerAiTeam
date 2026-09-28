import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Search, ArrowUpRight, History } from 'lucide-react'
import { getMissions } from '../../api/missions'
import { Dialog } from '../../components/Dialog'
import { StatusBadge, Loading, ErrorNotice, Empty } from '../../components/ui'
export function MissionHistory({
  onSelect,
  onClose,
}: {
  onSelect: (id: string) => void
  onClose: () => void
}) {
  const [search, setSearch] = useState('')
  const query = useQuery({ queryKey: ['missions'], queryFn: getMissions })
  const missions =
    query.data?.filter((mission) => mission.title.toLowerCase().includes(search.toLowerCase())) ||
    []
  return (
    <Dialog title="任务历史" onClose={onClose} wide>
      <div className="history-search">
        <Search size={17} />
        <input
          aria-label="搜索任务"
          placeholder="搜索过去的任务…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <span>{missions.length} 个任务</span>
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.isError ? (
        <ErrorNotice message="任务历史加载失败" onRetry={() => void query.refetch()} />
      ) : missions.length ? (
        <div className="history-list">
          {missions.map((mission) => (
            <button
              className="history-item"
              key={mission.id}
              onClick={() => {
                onSelect(mission.id)
                onClose()
              }}
            >
              <span className="history-icon">
                <History size={19} />
              </span>
              <span className="history-text">
                <strong>{mission.title}</strong>
                <small>
                  {new Date(mission.created_at).toLocaleString('zh-CN')} ·{' '}
                  {mission.plan ? `Plan v${mission.plan.version}` : '计划待创建'}
                </small>
                <p>{mission.result || mission.objective}</p>
              </span>
              <StatusBadge status={mission.status} />
              <ArrowUpRight size={17} />
            </button>
          ))}
        </div>
      ) : (
        <Empty>{search ? '没有匹配的任务' : '还没有任务。创建你的第一个 Mission 吧。'}</Empty>
      )}
    </Dialog>
  )
}
