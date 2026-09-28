import type { ReactNode } from 'react'
import { AlertCircle, Check, LoaderCircle } from 'lucide-react'
import { statusLabels, type MissionStatus } from '../types/mission'
export function StatusBadge({ status }: { status: MissionStatus }) {
  return (
    <span className={`status-badge status-${status.toLowerCase()}`}>
      <span className="status-dot" />
      {statusLabels[status]}
    </span>
  )
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}
export function Loading({ label = '正在加载…' }: { label?: string }) {
  return (
    <div className="loading">
      <LoaderCircle size={18} className="spin" />
      {label}
    </div>
  )
}
export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="error-notice" role="alert">
      <AlertCircle size={18} />
      <span>{message}</span>
      {onRetry && <button onClick={onRetry}>重试</button>}
    </div>
  )
}
export function SectionTitle({
  eyebrow,
  title,
  aside,
}: {
  eyebrow: string
  title: string
  aside?: ReactNode
}) {
  return (
    <div className="section-title">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
      </div>
      {aside}
    </div>
  )
}
export function TaskIcon({ status }: { status: string }) {
  return status === 'COMPLETED' ? (
    <Check size={15} />
  ) : status === 'RUNNING' ? (
    <LoaderCircle size={16} className="spin" />
  ) : (
    <span className="circle-small" />
  )
}
