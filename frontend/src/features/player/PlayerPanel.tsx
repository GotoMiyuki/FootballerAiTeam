import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowUpRight, Shield, Footprints, CalendarDays, MapPin } from 'lucide-react'
import { getPlayer, getTrainingHistory, getMatchHistory } from '../../api/player'
import { ErrorNotice, Loading, Empty } from '../../components/ui'
import type { Player } from '../../types/player'

const tabs = [
  { id: 'profile', label: '球员档案' },
  { id: 'training', label: '训练记录' },
  { id: 'matches', label: '比赛记录' },
] as const
export function PlayerPanel() {
  const [tab, setTab] = useState<(typeof tabs)[number]['id']>('profile')
  const player = useQuery({ queryKey: ['player'], queryFn: getPlayer })
  const training = useQuery({
    queryKey: ['training'],
    queryFn: getTrainingHistory,
    enabled: tab === 'training',
  })
  const matches = useQuery({
    queryKey: ['matches'],
    queryFn: getMatchHistory,
    enabled: tab === 'matches',
  })
  return (
    <aside className="player-panel">
      <div className="panel-heading">
        <span className="eyebrow">PLAYER WORKSPACE</span>
        <span className="small-label">长期球员状态</span>
      </div>
      {player.isPending ? (
        <Loading label="读取球员档案…" />
      ) : player.isError ? (
        <ErrorNotice message="无法读取球员数据" onRetry={() => void player.refetch()} />
      ) : (
        <>
          <div className="player-card">
            <div className="pitch-lines" aria-hidden="true">
              <div />
              <i />
            </div>
            <div className="player-card-top">
              <span>
                <Shield size={15} /> FOOTBALLER PROFILE
              </span>
              <span className="position">{player.data.position}</span>
            </div>
            <div className="player-identity">
              <div className="avatar">
                {player.data.name.slice(0, 1).toUpperCase()}
                <span className="avatar-dot" />
              </div>
              <div>
                <h1>{player.data.name}</h1>
                <p>{player.data.club}</p>
              </div>
            </div>
            <div className="player-card-footer">
              <span>
                <MapPin size={13} />
                {player.data.nationality} · {player.data.age} 岁
              </span>
              <span>
                综合能力 <strong>{player.data.overall}</strong>
              </span>
            </div>
          </div>
          <div className="player-tabs" role="tablist" aria-label="球员资料">
            {tabs.map((item) => (
              <button
                key={item.id}
                role="tab"
                aria-selected={tab === item.id}
                aria-controls={`panel-${item.id}`}
                id={`tab-${item.id}`}
                onClick={() => setTab(item.id)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`}>
            {tab === 'profile' && <ProfileView player={player.data} />}
            {tab === 'training' && (
              <div className="record-list">
                {training.isPending ? (
                  <Loading />
                ) : training.isError ? (
                  <ErrorNotice message="训练记录加载失败" onRetry={() => void training.refetch()} />
                ) : training.data.length === 0 ? (
                  <Empty>暂无训练记录</Empty>
                ) : (
                  [...training.data].reverse().map((record, index) => (
                    <details className="record-card" key={`${record.week}-${index}`}>
                      <summary>
                        <span className="record-icon">
                          <Footprints size={17} />
                        </span>
                        <span>
                          <strong>{record.focus}</strong>
                          <small>
                            {record.week} · {record.date_range}
                          </small>
                        </span>
                        <ArrowUpRight size={16} />
                      </summary>
                      <div className="record-body">
                        <div className="record-stats">
                          <span>
                            周负荷 <b>{record.weekly_load}</b>
                          </span>
                          <span>
                            RPE <b>{record.avg_rpe}</b>
                          </span>
                        </div>
                        {record.training_sessions?.map((session, i) => (
                          <p key={i}>
                            <b>{session.day}</b> {session.type}
                            <small>
                              {session.duration_min} 分钟 · {session.intensity}
                            </small>
                          </p>
                        ))}
                        <p className="muted">{record.notes}</p>
                      </div>
                    </details>
                  ))
                )}
              </div>
            )}
            {tab === 'matches' && (
              <div className="record-list">
                {matches.isPending ? (
                  <Loading />
                ) : matches.isError ? (
                  <ErrorNotice message="比赛记录加载失败" onRetry={() => void matches.refetch()} />
                ) : matches.data.length === 0 ? (
                  <Empty>暂无比赛记录</Empty>
                ) : (
                  [...matches.data].reverse().map((record, index) => (
                    <details className="record-card" key={`${record.date}-${index}`}>
                      <summary>
                        <span className="record-icon">
                          <CalendarDays size={17} />
                        </span>
                        <span>
                          <strong>vs {record.opponent}</strong>
                          <small>
                            {record.date} · {record.competition}
                          </small>
                        </span>
                        <span className="match-score">{record.result}</span>
                      </summary>
                      <div className="record-body">
                        <div className="record-stats">
                          <span>
                            出场 <b>{record.minutes_played}′</b>
                          </span>
                          <span>
                            进球 <b>{record.goals}</b>
                          </span>
                          <span>
                            助攻 <b>{record.assists}</b>
                          </span>
                          <span>
                            评分 <b>{record.rating}</b>
                          </span>
                        </div>
                        <p>{record.notes}</p>
                      </div>
                    </details>
                  ))
                )}
              </div>
            )}
          </div>
          <div className="data-footnote">
            <span className="status-dot" />
            来自球员长期记忆<span>更新于 {player.data.last_updated || '未记录'}</span>
          </div>
        </>
      )}
    </aside>
  )
}
function ProfileView({ player }: { player: Player }) {
  const physical = player.attributes?.physical || {}
  const offense = player.attributes?.offense || {}
  const defense = player.attributes?.defense || {}
  const attributes = [
    ['速度', physical.speed],
    ['耐力', physical.stamina],
    ['力量', physical.strength],
    ['射门', offense.shooting],
    ['控球', offense.ball_control],
    ['传球', offense.passing],
    ['进攻意识', offense.attacking_awareness],
    ['防守意识', defense.defensive_awareness],
  ] as const
  const goals =
    typeof player.long_term_goals === 'string' ? [player.long_term_goals] : player.long_term_goals
  return (
    <div className="profile-view">
      <div className="physical-grid">
        <div>
          <span>身高</span>
          <strong>
            {player.height}
            <small>cm</small>
          </strong>
        </div>
        <div>
          <span>体重</span>
          <strong>
            {player.weight}
            <small>kg</small>
          </strong>
        </div>
        <div>
          <span>惯用脚</span>
          <strong className="text-value">{player.preferred_foot}</strong>
        </div>
      </div>
      <div className="subheading">
        <h3>能力档案</h3>
        <span>当前记录 / 100</span>
      </div>
      <div className="attributes">
        {attributes.map(([label, value]) => (
          <div className="attribute" key={label}>
            <span>{label}</span>
            <div className="attribute-track">
              <i style={{ width: `${Math.max(0, Math.min(100, value || 0))}%` }} />
            </div>
            <b>{value ?? '—'}</b>
          </div>
        ))}
      </div>
      <div className="profile-note">
        <Shield size={19} />
        <div>
          <strong>身体状态</strong>
          <p>{player.injury === 'None' ? '档案中未记录当前伤病' : player.injury || '暂无记录'}</p>
          <small>开始任务时，团队会按需确认当前反馈。</small>
        </div>
      </div>
      <div className="goal-card">
        <span className="eyebrow">LONG-TERM GOALS</span>
        <h3>长期目标</h3>
        {goals?.length ? (
          goals.map((goal, index) => <p key={index}>{goal}</p>)
        ) : (
          <p>尚未记录长期目标。你可以在新任务中向团队说明你的发展方向。</p>
        )}
      </div>
    </div>
  )
}
