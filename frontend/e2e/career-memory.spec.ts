import { test, expect, type Page } from '@playwright/test'
import type { Mission } from '../src/types/mission'
import type { CareerMemory, CareerMemoryList, MemoryWriteRequest } from '../src/types/career-memory'

const context = { career_id: 'career-memory-test', branch_id: 'main', player_id: 'p' }
const mission: Mission = {
  id: 'memory-mission',
  conversation_id: 'memory-conversation',
  title: '记忆来源任务',
  objective: '记忆来源任务',
  status: 'COMPLETED',
  created_at: '2026-10-24T10:00:00+08:00',
  sequence: 10,
  plan: null,
  agents: [],
  review: null,
  review_history: [],
  blocked: null,
  report: null,
  result: null,
  error: null,
  telemetry: {},
  delivery_status: 'NOT_GENERATED',
  input_reference: {
    verification: 'VERIFIED',
    context,
    state_version: 'v1',
    snapshot_id: 'v1',
    source_types: ['demo_fixture'],
  },
  available_operations: ['view'],
}
const emptySource: CareerMemory['source'] = {
  type: 'user_confirmed',
  mission_id: mission.id,
  request_id: null,
  candidate_id: null,
  confirmed_candidate_revision: null,
  event_id: null,
  event_type: null,
  object_id: null,
  object_revision: null,
  object_hash: null,
  recommendation_id: null,
  recommendation_revision: null,
  recommendation_source: null,
}
const unknownTime: CareerMemory['effective_time'] = {
  time_domain: 'unknown',
  precision: 'unknown',
  effective_at: null,
  valid_window: null,
}

function reference(kind: CareerMemory['kind'], id: string): CareerMemory {
  return {
    schema_version: 1,
    memory_id: id,
    revision: 1,
    context,
    kind,
    content: kind === 'execution' ? '自报尝试训练10分钟' : '训练焦点引用',
    projection: kind === 'execution' ? { status: 'UNKNOWN', source_type: 'user_reported' } : {},
    source: {
      ...emptySource,
      type: 'e_record',
      event_id: `event-${id}`,
      event_type: kind === 'execution' ? 'execution.recorded' : 'recommendation.created',
      object_id: `source-${id}`,
      object_revision: 1,
      recommendation_id: 'rec-1',
      recommendation_revision: 1,
      object_hash: 'record-hash',
    },
    recorded_at: '2026-10-24T10:00:00+08:00',
    effective_time: unknownTime,
    corrects_revision: null,
    supersedes_memory_id: null,
    change_reason: '',
    status: 'active',
    usage: 'current',
    status_reason: '来源已核验；执行只是用户自报',
    source_checked: true,
    recommendation_validity: 'current',
    limitations: [],
  }
}

async function memoryApi(page: Page, loseResponse = false) {
  const state: CareerMemoryList = { context, items: [] }
  const requests: MemoryWriteRequest[] = []
  const saved = new Map<string, CareerMemory>()
  let lost = false
  await page.addInitScript(() => localStorage.setItem('fait.mission', 'memory-mission'))
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const path = new URL(route.request().url()).pathname
      if (path.endsWith('/events'))
        return route.fulfill({ contentType: 'text/event-stream', body: '' })
      if (path.endsWith('/career-memory/sync')) {
        const created = state.items.some((item) => item.source.type === 'e_record') ? 0 : 2
        if (created)
          state.items.push(
            reference('recommendation', 'memory-rec'),
            reference('execution', 'memory-exec'),
          )
        return route.fulfill({ json: { consumed_events: created, created_memories: created } })
      }
      if (path.endsWith('/user-records')) {
        const body = route.request().postDataJSON() as MemoryWriteRequest
        requests.push(body)
        const duplicate = saved.get(body.request_id)
        if (duplicate) return route.fulfill({ json: { record: duplicate, created: false } })
        const prior = [...state.items].reverse().find((item) => item.memory_id === body.memory_id)
        if (prior && prior.revision !== body.expected_revision)
          return route.fulfill({ status: 409, json: { detail: '版本变化' } })
        const record: CareerMemory = {
          ...reference(body.kind, body.memory_id || `user-memory-${state.items.length}`),
          content: body.operation === 'retract' ? prior!.content : body.content,
          revision: prior ? prior.revision + 1 : 1,
          corrects_revision: prior?.revision || null,
          effective_time: body.effective_time,
          change_reason: body.reason,
          source: {
            ...emptySource,
            request_id: body.request_id,
            candidate_id: prior?.source.candidate_id || null,
            confirmed_candidate_revision:
              body.operation === 'confirm_candidate' ? prior!.revision : null,
          },
          status: body.operation === 'retract' ? 'retracted' : 'active',
          usage: body.operation === 'retract' ? 'historical_only' : 'current',
          status_reason: '用户明确确认',
          recommendation_validity: null,
        }
        if (prior) {
          prior.status = 'superseded'
          prior.usage = 'historical_only'
        }
        state.items.push(record)
        saved.set(body.request_id, record)
        if (loseResponse && !lost) {
          lost = true
          return route.abort('failed')
        }
        return route.fulfill({ json: { record, created: true } })
      }
      const data = path.endsWith('/career-memory')
        ? state
        : path === `/api/missions/${mission.id}`
          ? mission
          : path === '/api/health'
            ? { status: 'ok', mode: 'live' }
            : path === '/api/player'
              ? { name: 'Test', attributes: {}, long_term_goals: [] }
              : path.endsWith('/recommendations')
                ? { mission_id: mission.id, availability: 'AVAILABLE', reason: '', items: [] }
                : []
      return route.fulfill({ json: data })
    },
  )
  return { state, requests }
}

test('C2.1 explicit memory revisions and E references retain provenance on reload', async ({
  page,
}) => {
  const api = await memoryApi(page)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/')
  const panel = page.getByRole('region', { name: '生涯记忆' })
  await panel.getByText('设置目标或偏好', { exact: true }).click()
  await panel.getByRole('textbox', { name: '记忆确认内容' }).fill('每天只安排20分钟')
  await panel.getByRole('button', { name: '确认保存目标/偏好', exact: true }).click()
  await expect(panel).toContainText('记忆已保存')
  await panel.getByText('查看记忆与来源历史（1）', { exact: true }).click()
  await panel.getByRole('button', { name: '更正此记忆' }).click()
  await panel.getByRole('textbox', { name: '记忆确认内容' }).fill('每周安排三次')
  await panel.getByRole('textbox', { name: '记忆变更原因' }).fill('工作时间变化')
  await panel.getByRole('button', { name: '确认更正', exact: true }).click()
  await expect.poll(() => api.state.items.length).toBe(2)
  await panel.getByRole('button', { name: '撤回此记忆' }).click()
  await panel.getByRole('textbox', { name: '记忆变更原因' }).fill('暂时取消偏好')
  await panel.getByRole('button', { name: '确认撤回', exact: true }).click()
  await expect.poll(() => api.state.items.length).toBe(3)
  await panel.getByRole('button', { name: '同步E来源记录' }).click()
  await expect(panel).toContainText('已同步 2 条E事件，新增 2 条来源记忆')
  await page.reload()
  await panel.getByText('查看记忆与来源历史（5）', { exact: true }).click()
  await expect(panel).toContainText('更正原版本：v2；暂时取消偏好')
  await expect(panel).toContainText('每天只安排20分钟')
  await expect(panel).toContainText('event-memory-exec')
  await expect(panel).toContainText('不等于游戏核验或能力变化')
  await expect(panel.getByRole('button', { name: '更正此记忆' })).toHaveCount(0)
  await panel.screenshot({ path: 'test-results/c2-1-memory-history.png' })
  expect(errors).toEqual([])
})

test('C2.1 response loss retries an identical explicit confirmation after reload', async ({
  page,
}) => {
  const api = await memoryApi(page, true)
  await page.goto('/')
  const panel = page.getByRole('region', { name: '生涯记忆' })
  await panel.getByText('设置目标或偏好', { exact: true }).click()
  await panel.getByRole('textbox', { name: '记忆确认内容' }).fill('每周三次')
  await panel.getByRole('button', { name: '确认保存目标/偏好', exact: true }).click()
  await expect(panel.getByRole('alert')).toBeVisible()
  await page.reload()
  await panel.getByText('设置目标或偏好', { exact: true }).click()
  await panel.getByRole('button', { name: '确认保存目标/偏好', exact: true }).click()
  await expect(panel).toContainText('记忆已保存')
  expect(api.requests).toHaveLength(2)
  expect(api.requests[0]).toEqual(api.requests[1])
  expect(api.state.items).toHaveLength(1)
})

test('C2.1 unconfirmed candidates and stale E references stay distinct from current preferences', async ({
  page,
}) => {
  const api = await memoryApi(page)
  const candidate: CareerMemory = {
    ...reference('preference', 'candidate-memory'),
    content: '模型候选偏好',
    status: 'candidate',
    usage: 'unconfirmed',
    source: { ...emptySource, type: 'model_candidate', candidate_id: 'candidate-1' },
    status_reason: '模型候选未经用户确认',
    recommendation_validity: null,
  }
  api.state.items.push(candidate)
  await page.goto('/')
  const panel = page.getByRole('region', { name: '生涯记忆' })
  await panel.getByText('查看记忆与来源历史（1）', { exact: true }).click()
  await expect(panel).toContainText('待用户确认的候选 · 未确认')
  await panel.getByRole('button', { name: '选择确认此候选' }).click()
  await panel.getByRole('textbox', { name: '记忆确认内容' }).fill('用户核实后的偏好')
  await panel.getByRole('button', { name: '明确确认候选', exact: true }).click()
  await expect(panel).toContainText('明确确认候选原版本：v1')
  await panel.getByRole('button', { name: '同步E来源记录' }).click()
  await expect.poll(() => api.state.items.length).toBe(4)
  const execution = api.state.items.find((item) => item.kind === 'execution')!
  execution.usage = 'historical_only'
  execution.status = 'superseded'
  execution.status_reason = 'E原反馈已被更正，历史保留'
  await panel.getByRole('button', { name: '刷新记忆状态' }).click()
  await expect(panel).toContainText('E原反馈已被更正，历史保留')
  await expect(panel).toContainText('执行反馈引用 · 已被更正或替代 · 仅作历史参考')
})

test('C2.1 actual demo API saves explicit goals without changing player facts', async ({
  page,
}) => {
  await page.goto('/')
  await page.getByLabel('任务需求', { exact: true }).fill(`C2.1生涯记忆 ${Date.now()}`)
  await page.getByRole('button', { name: '创建任务', exact: true }).click()
  await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
  const before = await (await page.request.get('/api/player')).json()
  const panel = page.getByRole('region', { name: '生涯记忆' })
  await panel.getByText('设置目标或偏好', { exact: true }).click()
  await panel.getByRole('combobox', { name: '记忆类别' }).selectOption('goal')
  const content = `明确目标-${Date.now()}`
  await panel.getByRole('textbox', { name: '记忆确认内容' }).fill(content)
  await panel.getByRole('button', { name: '确认保存目标/偏好', exact: true }).click()
  await expect(panel).toContainText('记忆已保存')
  await page.reload()
  await panel.locator('summary').filter({ hasText: '查看记忆与来源历史' }).click()
  await expect(panel).toContainText(content)
  expect(await (await page.request.get('/api/player')).json()).toEqual(before)
})
