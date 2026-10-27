import { useQuery, useQueryClient } from '@tanstack/react-query'
import { getRuntime, mergeRuntime } from '../../api/runtime'
import { ErrorNotice } from '../../components/ui'
import type { RuntimeView, UsageSummary, RuntimeOperation } from '../../types/runtime'

const labels: Record<string, string> = {
  QUEUED: '排队中', RUNNING: '运行中', PAUSED: '等待你的输入',
  COMPLETED: '已完成', FAILED: '失败', CANCELLED: '已取消',
}
const seconds = (value: number | null) => value === null ? '未知' : `${value.toFixed(2)} 秒`
export function RuntimePanel({ id }: { id: string }) {
  const cache = useQueryClient()
  const query = useQuery({
    queryKey: ['runtime', id],
    queryFn: async () => mergeRuntime(cache.getQueryData<RuntimeView>(['runtime', id]), await getRuntime(id)),
    refetchInterval: (query) => query.state.data?.operations?.some((op) => op.timing.live || op.cleanup_status === 'PENDING') ? 1000 : false,
  })
  const value = query.data
  return (
    <details className="telemetry">
      <summary>任务运行信息<span>生成与解释分别计量</span></summary>
      {query.isError ? <ErrorNotice message="运行信息读取失败" onRetry={() => void query.refetch()} />
        : !value?.operations ? <p>运行信息尚未提供。</p> : (
          <>
            <section aria-label="累计运行计量"><h3>各操作累计观察</h3><Usage value={value.totals} /></section>
            <p>记录模型响应 token，不估算金额。</p>
            {value.operations.map((op, index) => <OperationInfo key={op.id} operation={op}
              title={op.kind === 'workflow' ? '报告生成' : `报告解释 ${value.operations.slice(0, index).filter((row) => row.kind === 'explanation').length + 1}`} />)}
            {!value.operations.length && <p>旧任务没有操作计量，完整用量未知。</p>}
            {Object.keys(value.report_telemetry).length > 0 && (
              <section aria-label="报告节点记录"><h3>报告生成的节点记录</h3>
                <p>节点耗时累计：{seconds(value.report_telemetry.latency_ms == null ? null : value.report_telemetry.latency_ms / 1000)}</p>
                <p>审查 {value.report_telemetry.review_count ?? '未知'} 次 · 修订 {value.report_telemetry.revision_count ?? '未知'} 次 · 重规划 {value.report_telemetry.replan_count ?? '未知'} 次</p>
                <p>墙钟时间、排队和用户等待见各操作记录。</p>
              </section>
            )}
          </>
        )}
    </details>
  )
}

function Usage({ value }: { value: UsageSummary }) {
  return (
    <>
      {!value.available && <p>含未计量的历史，完整调用数与用量未知。</p>}
      {value.mode === 'demo' && <p>演示流程，无真实模型调用。</p>}
      <dl>
        <div><dt>已观测逻辑调用</dt><dd>{value.logical_calls ?? '未知'}</dd></div>
        <div><dt>已观测工具调用</dt><dd>{value.tool_calls ?? '未知'}</dd></div>
        <div><dt>模型 HTTP 尝试</dt><dd>{value.network_attempts === null ? `完整次数未知；已观测 ${value.known_network_attempts}` : value.network_attempts}</dd></div>
        <div><dt>完整输入用量</dt><dd>{value.input_tokens === null ? '未知' : value.input_tokens}</dd></div>
        <div><dt>完整输出用量</dt><dd>{value.output_tokens === null ? '未知' : value.output_tokens}</dd></div>
        <div><dt>已知响应输入 token</dt><dd>{value.known_input_tokens}</dd></div>
        <div><dt>已知响应输出 token</dt><dd>{value.known_output_tokens}</dd></div>
      </dl>
      <p>{value.complete ? '已执行调用的用量已知。' : '完整用量未知；已知响应合计仅覆盖取得 usage 的部分。'}</p>
      <p>取得 usage 的逻辑响应：{value.usage_known_calls} / {value.logical_calls ?? '未知'}。</p>
      {!!value.unknown_attempts && <p>有失败或中断的传输尝试，其用量未取得。</p>}
      {value.counts_complete === false && <p>执行记录尚未收口或有中断缺口，调用计数为已观测部分。</p>}
    </>
  )
}

function OperationInfo({ operation: op, title }: { operation: RuntimeOperation; title: string }) {
  return (
    <section aria-label={`${title}运行记录`}>
      <h3>{title} · {labels[op.status]}</h3>
      <Usage value={op.usage} />
      <dl>
        <div><dt>排队</dt><dd>{seconds(op.timing.queue_seconds)}</dd></div>
        <div><dt>实际执行（含启动/清理）</dt><dd>{seconds(op.timing.execution_seconds)}</dd></div>
        <div><dt>用户等待</dt><dd>{seconds(op.timing.user_wait_seconds)}</dd></div>
        <div><dt>总体墙钟</dt><dd>{seconds(op.timing.wall_seconds)}</dd></div>
      </dl>
      {!op.timing.history_complete && <p>旧历史或中断段时间不完整，保留的执行下界 {seconds(op.timing.known_execution_seconds)}。</p>}
      {op.status === 'FAILED' && <p>{op.error || '执行失败'}。{op.kind === 'explanation'
        ? '原报告保留；重新提问使用新请求。' : '原请求重试会返回这次记录；重新执行请创建关联新任务。'}</p>}
    </section>
  )
}
