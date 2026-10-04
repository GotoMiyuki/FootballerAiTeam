import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { CircleDot, History, SlidersHorizontal, Plus, ArrowUpRight } from 'lucide-react'
import { apiBase, request } from '../api/client'
import { Dialog } from '../components/Dialog'
import { ErrorNotice } from '../components/ui'
import { PlayerPanel } from '../features/player/PlayerPanel'
import { MissionWorkspace } from '../features/mission/MissionWorkspace'
import { MissionHistory } from '../features/history/MissionHistory'

export default function App() {
  const [missionId, setMissionId] = useState<string | undefined>(
    () => localStorage.getItem('fait.mission') || undefined,
  )
  const [draftId, setDraftId] = useState(() => `conv_${crypto.randomUUID()}`)
  const [history, setHistory] = useState(false)
  const [settings, setSettings] = useState(false)
  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => request<{ status: string; mode: 'live' | 'demo' }>('/health'),
    retry: 1,
  })
  function select(id: string) {
    setMissionId(id)
    localStorage.setItem('fait.mission', id)
  }
  function newMission() {
    setMissionId(undefined)
    localStorage.removeItem('fait.mission')
    setDraftId(`conv_${crypto.randomUUID()}`)
  }
  return (
    <>
      <header className="app-header">
        <a className="brand" href="/" aria-label="FootballerAiTeam 首页">
          <span className="brand-mark">
            <CircleDot size={24} />
          </span>
          <span>
            Footballer<span className="brand-ai">Ai</span>Team
            <small>PLAYER DEVELOPMENT WORKSPACE</small>
          </span>
        </a>
        <div className="header-divider" />
        <span className="workspace-name">
          球员工作台<span>V0.1</span>
        </span>
        <nav aria-label="全局导航">
          <button className="nav-button" onClick={() => setHistory(true)}>
            <History size={17} />
            任务历史
          </button>
          <button className="icon-button" aria-label="设置" onClick={() => setSettings(true)}>
            <SlidersHorizontal size={18} />
          </button>
          <button className="header-new" onClick={newMission}>
            <Plus size={17} />
            新建任务
          </button>
        </nav>
      </header>
      {health.data?.mode === 'demo' && (
        <div className="demo-banner">
          <span>DEMO</span>当前为演示模式 · 任务流程使用示例数据，球员资料来自配置的数据源
        </div>
      )}
      {health.isError && (
        <div className="global-error">
          <ErrorNotice
            message="无法连接后端，请确认 API 服务已在 8000 端口启动。"
            onRetry={() => void health.refetch()}
          />
        </div>
      )}
      <div className="workspace-layout">
        <PlayerPanel />
        <MissionWorkspace
          key={missionId || draftId}
          id={missionId}
          draftConversationId={draftId}
          demo={health.data?.mode === 'demo'}
          onMission={select}
          onNew={newMission}
        />
      </div>
      {history && <MissionHistory onSelect={select} onClose={() => setHistory(false)} />}
      {settings && (
        <Dialog title="工作台设置" onClose={() => setSettings(false)}>
          <div className="settings-content">
            <span className="eyebrow">CONNECTION</span>
            <h3>
              {health.data?.mode === 'demo'
                ? '演示模式'
                : health.data
                  ? '真实 Agent 模式'
                  : '后端未连接'}
            </h3>
            <p>
              {health.data?.mode === 'demo'
                ? '创建任务时可以选择一次通过、局部修订、补充信息或重新规划，验证完整工作流程。'
                : '任务由仓库中的现有 Agent 执行，使用后端配置的模型与工具。'}
            </p>
            <div className="settings-row">
              <span>界面语言</span>
              <strong>简体中文</strong>
            </div>
            <div className="settings-row">
              <span>实时任务更新</span>
              <strong>SSE</strong>
            </div>
            <div className="settings-row">
              <span>报告格式</span>
              <strong>Markdown</strong>
            </div>
            <a
              className="secondary-button"
              href={`${apiBase || 'http://127.0.0.1:8000'}/docs`}
              target="_blank"
              rel="noreferrer"
            >
              查看 API 文档
              <ArrowUpRight size={16} />
            </a>
          </div>
        </Dialog>
      )}
    </>
  )
}
