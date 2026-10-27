import { test, expect, type Page } from '@playwright/test'
import type { Mission } from '../src/types/mission'
import type { RuntimeOperation, RuntimeView, UsageSummary } from '../src/types/runtime'

const mission: Mission = { id: 'm45-task', conversation_id: 'm45-chat', title: '原报告任务', objective: '原报告目标',
  status: 'COMPLETED', created_at: '2026-10-26T00:00:00Z', sequence: 10, plan: null, agents: [], review: null,
  review_history: [], blocked: null, report: { title: '原报告', plan_version: 1 }, result: '原结论', error: null,
  delivery_status: 'PUBLISHABLE', telemetry: {}, available_operations: ['view', 'explain'] }
const usage: UsageSummary = { available: true, complete: true, counts_complete: true, mode: 'live',
  logical_calls: 1, network_attempts: 1, known_network_attempts: 1, input_tokens: 7, output_tokens: 3,
  known_input_tokens: 7, known_output_tokens: 3, usage_known_calls: 1, usage_unknown_calls: 0, unknown_attempts: 0 }
function operation(id = 'workflow'): RuntimeOperation {
  return { id, mission_id: mission.id, kind: id === 'workflow' ? 'workflow' : 'explanation', status: 'COMPLETED',
    run_id: 1, revision: 1, pause_id: null, cleanup_status: 'COMPLETE', usage: structuredClone(usage),
    timing: { history_complete: true, queue_seconds: 1, execution_seconds: 5, user_wait_seconds: 30, wall_seconds: 36,
      known_queue_seconds: 1, known_execution_seconds: 5, known_user_wait_seconds: 30, live: false } }
}
function runtime(): RuntimeView {
  return { mission_id: mission.id, mission_sequence: 10, read_at: '2026-10-26T00:00:40Z', workflow_operation_id: 'workflow',
    operations: [operation()], totals: structuredClone(usage), report_telemetry: { llm_call_count: 1, latency_ms: 100000, review_count: 1 } }
}
async function setup(page: Page, initial = mission, initialRuntime = runtime()) {
  const missions = new Map([[initial.id, structuredClone(initial)]])
  const values = new Map([[initial.id, structuredClone(initialRuntime)]])
  const errors: string[] = []
  const state = { missions, values, errors, requests: 0,
    load: async (id: string) => values.get(id) }
  await page.addInitScript((id) => localStorage.setItem('fait.mission', id), initial.id)
  page.on('pageerror', (error) => errors.push(error.message))
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const path = new URL(route.request().url()).pathname
    const id = path.split('/')[3]
    if (path.endsWith('/runtime')) {
      state.requests += 1
      return route.fulfill({ json: await state.load(id) })
    }
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: '' })
    const body = path === '/api/health' ? { status: 'ok', mode: 'live' }
      : path === '/api/player' ? { name: 'Fixture', attributes: {}, metadata: {} }
      : path === '/api/missions' ? [...missions.values()]
      : path.endsWith('/operations') ? values.get(id)?.operations || []
      : path.endsWith('/report') ? { title: '原报告', markdown: '# 原报告\n原结论' }
      : path === `/api/missions/${id}` ? missions.get(id) : []
    return route.fulfill({ json: body })
  })
  return state
}
async function openRuntime(page: Page) {
  await page.locator('summary').filter({ hasText: '任务运行信息' }).click()
}

test('M4.5 report generation, two explanations and known partial usage remain separate', async ({ page }) => {
  const view = runtime()
  const first = operation('explain-one')
  first.usage = { ...usage, complete: false, network_attempts: 2, known_network_attempts: 2,
    input_tokens: null, output_tokens: null, known_input_tokens: 11, known_output_tokens: 7, unknown_attempts: 1 }
  const second = operation('explain-two')
  second.usage = { ...usage, complete: false, input_tokens: null, output_tokens: null,
    known_input_tokens: 0, known_output_tokens: 0, usage_known_calls: 0, usage_unknown_calls: 1 }
  view.operations.push(first, second)
  view.totals = { ...first.usage, logical_calls: 3, network_attempts: 4, known_network_attempts: 4, known_input_tokens: 18, known_output_tokens: 10 }
  const state = await setup(page, mission, view)
  await page.goto('/')
  await openRuntime(page)
  const generation = page.getByRole('region', { name: '报告生成运行记录', exact: true })
  await expect(generation.getByText('已执行调用的用量已知。')).toBeVisible()
  await expect(generation.getByText('5.00 秒', { exact: true })).toBeVisible()
  await expect(generation.getByText('36.00 秒', { exact: true })).toBeVisible()
  await expect(page.getByRole('region', { name: '报告解释 1运行记录' }).getByText('有失败或中断的传输尝试，其用量未取得。')).toBeVisible()
  await expect(page.getByRole('region', { name: '报告解释 2运行记录' }).getByText(/完整用量未知/)).toBeVisible()
  await expect(page.getByRole('region', { name: '报告节点记录' }).getByText('节点耗时累计：100.00 秒')).toBeVisible()
  await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
  expect(state.errors).toEqual([])
  await page.screenshot({ path: test.info().outputPath('runtime-partial-usage.png'), fullPage: true })
})

test('M4.5 legacy and interrupted usage render unknown totals, not zero', async ({ page }) => {
  const view = runtime()
  view.operations[0].usage = { ...usage, available: false, complete: false, logical_calls: null,
    network_attempts: null, known_network_attempts: 0, input_tokens: null, output_tokens: null,
    known_input_tokens: 0, known_output_tokens: 0 }
  view.operations[0].timing = { ...view.operations[0].timing, history_complete: false,
    queue_seconds: null, execution_seconds: null, user_wait_seconds: null }
  view.totals = view.operations[0].usage
  const state = await setup(page, mission, view)
  await page.goto('/')
  await openRuntime(page)
  const generation = page.getByRole('region', { name: '报告生成运行记录' })
  await expect(generation.getByText('含未计量的历史，完整调用数与用量未知。')).toBeVisible()
  await expect(generation.locator('div').filter({ has: page.locator('dt', { hasText: '完整输入用量' }) }).locator('dd')).toHaveText('未知')
  await expect(generation.locator('div').filter({ has: page.locator('dt', { hasText: '实际执行（含启动/清理）' }) }).locator('dd')).toHaveText('未知')
  expect(state.errors).toEqual([])
})

test('M4.5 failed explanation shows unknown usage and the correct new-request action', async ({ page }) => {
  const view = runtime()
  const failed = operation('timeout')
  failed.status = 'FAILED'
  failed.error = 'MODEL_TIMEOUT'
  failed.usage = { ...usage, complete: false, counts_complete: false, input_tokens: null, output_tokens: null,
    network_attempts: null, known_input_tokens: 0, known_output_tokens: 0, usage_known_calls: 0,
    usage_unknown_calls: 1, interrupted_calls: 1, unknown_attempts: 1 }
  view.operations.push(failed)
  view.totals = failed.usage
  const state = await setup(page, mission, view)
  await page.goto('/')
  await openRuntime(page)
  await expect(page.getByRole('region', { name: '报告解释 1运行记录' }).getByText('MODEL_TIMEOUT。原报告保留；重新提问使用新请求。')).toBeVisible()
  await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
  expect(state.errors).toEqual([])
})

test('M4.5 equal revisions can advance user waiting time while execution stays fixed', async ({ page }) => {
  const view = runtime()
  view.operations[0].status = 'PAUSED'
  view.operations[0].timing.live = true
  const state = await setup(page, { ...mission, status: 'BLOCKED', report: null }, view)
  await page.goto('/')
  await openRuntime(page)
  const generation = page.getByRole('region', { name: '报告生成运行记录' })
  await expect(generation.getByText('30.00 秒', { exact: true })).toBeVisible()
  state.values.get(mission.id)!.operations[0].timing.user_wait_seconds = 32
  state.values.get(mission.id)!.read_at = '2026-10-26T00:00:42Z'
  await expect(generation.getByText('32.00 秒', { exact: true })).toBeVisible()
  await expect(generation.getByText('5.00 秒', { exact: true })).toBeVisible()
  expect(state.errors).toEqual([])
})

test('M4.5 an older HTTP runtime response cannot roll back newer operation counters', async ({ page }) => {
  await page.addInitScript(() => {
    const w = window as unknown as { streams: object[] }
    w.streams = []
    class Stream {
      onopen: ((event: object) => void) | null = null
      onmessage: ((event: object) => void) | null = null
      constructor() { w.streams.push(this); queueMicrotask(() => this.onopen?.({})) }
      close() {}
    }
    window.EventSource = Stream as unknown as typeof EventSource
  })
  const view = runtime()
  view.operations[0].status = 'RUNNING'
  view.operations[0].timing.live = true
  const state = await setup(page, { ...mission, status: 'RUNNING', report: null }, view)
  let release: () => void = () => {}
  const gate = new Promise<void>((resolve) => { release = resolve })
  let calls = 0
  state.load = async () => {
    calls += 1
    if (calls === 2) { const old = structuredClone(view); await gate; return old }
    const next = structuredClone(view)
    if (calls >= 3) {
      next.mission_sequence = 11
      next.operations[0].revision = 3
      next.operations[0].usage.logical_calls = 3
      next.read_at = '2026-10-26T00:00:43Z'
    }
    return next
  }
  await page.goto('/')
  await openRuntime(page)
  await expect.poll(() => calls).toBe(2)
  const nextMission = { ...mission, status: 'RUNNING', report: null, sequence: 11 }
  await page.evaluate((snapshot) => {
    const stream = (window as unknown as { streams: { onmessage: (event: { data: string }) => void }[] }).streams.at(-1)!
    stream.onmessage({ data: JSON.stringify({ event_id: 'runtime-update', mission_id: snapshot.id,
      sequence: snapshot.sequence, type: 'operation.updated', timestamp: '2026-10-26T00:00:43Z', data: { snapshot } }) })
  }, nextMission)
  const generation = page.getByRole('region', { name: '报告生成运行记录' })
  await expect(generation.locator('div').filter({ has: page.locator('dt', { hasText: '已观测逻辑调用' }) }).locator('dd')).toHaveText('3')
  release()
  await expect(generation.locator('div').filter({ has: page.locator('dt', { hasText: '已观测逻辑调用' }) }).locator('dd')).toHaveText('3')
  expect(state.errors).toEqual([])
})

test('M4.5 a previous mission response does not contaminate the newly selected runtime', async ({ page }) => {
  const state = await setup(page)
  const other = { ...mission, id: 'm45-other', title: '另一份报告', objective: '另一份报告目标' }
  const otherRuntime = { ...runtime(), mission_id: other.id,
    operations: [{ ...operation(), mission_id: other.id, usage: { ...usage, logical_calls: 5 } }] }
  state.missions.set(other.id, other)
  state.values.set(other.id, otherRuntime)
  let release: () => void = () => {}
  const gate = new Promise<void>((resolve) => { release = resolve })
  let started = false
  state.load = async (id) => { if (id === mission.id) { started = true; await gate }; return state.values.get(id) }
  await page.goto('/')
  await expect.poll(() => started).toBe(true)
  await page.getByRole('button', { name: '任务历史', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: /另一份报告/ }).click()
  await openRuntime(page)
  const generation = page.getByRole('region', { name: '报告生成运行记录' })
  await expect(generation.locator('div').filter({ has: page.locator('dt', { hasText: '已观测逻辑调用' }) }).locator('dd')).toHaveText('5')
  release()
  await expect(page.getByRole('heading', { name: '另一份报告', exact: true })).toBeVisible()
  await expect(generation.locator('div').filter({ has: page.locator('dt', { hasText: '已观测逻辑调用' }) }).locator('dd')).toHaveText('5')
  expect(state.errors).toEqual([])
})
