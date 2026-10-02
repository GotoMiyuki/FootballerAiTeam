import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowRight, MessageCircleQuestion } from 'lucide-react'
import { submitInput } from '../../api/missions'
import { ErrorNotice } from '../../components/ui'
import type { Mission } from '../../types/mission'
export function BlockedInputPanel({ mission }: { mission: Mission }) {
  const blocked = mission.blocked!
  const canResume =
    mission.available_operations?.includes(
      blocked.reason === 'report_approval' ? 'approve_report' : 'supply_input',
    ) ?? false
  const [values, setValues] = useState<Record<string, string | number | boolean>>(() =>
    Object.fromEntries(
      blocked.required_inputs
        .filter((field) => field.input_type === 'scale')
        .map((field) => [field.key, field.min ?? 0]),
    ),
  )
  const cache = useQueryClient()
  const mutation = useMutation({
    mutationFn: () => submitInput(mission.id, values),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ['mission', mission.id] })
      void cache.invalidateQueries({ queryKey: ['messages', mission.conversation_id] })
    },
  })
  function set(key: string, value: string | number | boolean) {
    setValues((current) => ({ ...current, [key]: value }))
  }
  return (
    <section className="blocked-panel" aria-labelledby="blocked-title">
      <div className="blocked-heading">
        <MessageCircleQuestion size={23} />
        <div>
          <span className="eyebrow">YOUR INPUT MATTERS</span>
          <h2 id="blocked-title">
            {blocked.reason === 'report_approval' ? '确认并生成报告' : '补充信息，继续任务'}
          </h2>
        </div>
        <span className="waiting-tag">等待你</span>
      </div>
      <p className="blocked-description">{blocked.message}</p>
      <form
        onSubmit={(event) => {
          event.preventDefault()
          if (canResume) mutation.mutate()
        }}
      >
        <div className="input-grid">
          {blocked.required_inputs.map((field) => (
            <div className={`input-field input-${field.input_type}`} key={field.key}>
              <label htmlFor={`input-${field.key}`}>
                {field.label}
                {!field.required && <span>（选填）</span>}
                {field.input_type === 'scale' && (
                  <b>
                    {values[field.key] ?? field.min ?? 0}
                    <small> / {field.max}</small>
                  </b>
                )}
              </label>
              {field.input_type === 'text' ? (
                <textarea
                  id={`input-${field.key}`}
                  required={field.required}
                  rows={3}
                  maxLength={8000}
                  value={String(values[field.key] ?? '')}
                  onChange={(event) => set(field.key, event.target.value)}
                  placeholder="填写你的当前情况…"
                />
              ) : field.input_type === 'number' ? (
                <input
                  id={`input-${field.key}`}
                  type="number"
                  required={field.required}
                  min={field.min ?? undefined}
                  max={field.max ?? undefined}
                  step="any"
                  value={String(values[field.key] ?? '')}
                  onChange={(event) => {
                    if (event.target.value === '') {
                      setValues((current) => {
                        const next = { ...current }
                        delete next[field.key]
                        return next
                      })
                    } else set(field.key, Number(event.target.value))
                  }}
                  placeholder="请输入数值"
                />
              ) : field.input_type === 'scale' ? (
                <>
                  <input
                    id={`input-${field.key}`}
                    type="range"
                    min={field.min ?? 0}
                    max={field.max ?? 10}
                    value={Number(values[field.key] ?? field.min ?? 0)}
                    onChange={(event) => set(field.key, Number(event.target.value))}
                  />
                  <div className="scale-labels">
                    <span>{field.min} · 无 / 最低</span>
                    <span>{field.max} · 最高</span>
                  </div>
                </>
              ) : (
                <select
                  id={`input-${field.key}`}
                  required={field.required}
                  value={values[field.key] === undefined ? '' : String(values[field.key])}
                  onChange={(event) => {
                    if (event.target.value === '') {
                      setValues((current) => {
                        const next = { ...current }
                        delete next[field.key]
                        return next
                      })
                    } else
                      set(
                        field.key,
                        field.input_type === 'boolean'
                          ? event.target.value === 'true'
                          : event.target.value,
                      )
                  }}
                >
                  <option value="">请选择</option>
                  {field.input_type === 'boolean' ? (
                    <>
                      <option value="true">是</option>
                      <option value="false">否</option>
                    </>
                  ) : (
                    field.options.map((option) => <option key={option}>{option}</option>)
                  )}
                </select>
              )}
            </div>
          ))}
        </div>
        {mutation.isError && <ErrorNotice message={mutation.error.message} />}
        <div className="blocked-footer">
          <span>
            {canResume
              ? blocked.reason === 'report_approval'
                ? '确认只续跑报告生成；不更新球员能力，也不代表正文已审核。'
                : '提交后从原任务暂停处继续，保留原数据快照。'
              : '当前无法恢复，请查看提示并新建任务。'}
          </span>
          <button className="primary-button" disabled={mutation.isPending || !canResume}>
            {mutation.isPending ? '正在提交…' : '提交并继续'}
            <ArrowRight size={16} />
          </button>
        </div>
      </form>
    </section>
  )
}
