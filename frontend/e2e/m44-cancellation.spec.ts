import { test, expect, type Page, type Route } from '@playwright/test'
import type { Mission, Operation } from '../src/types/mission'

const original: Mission = {
  id: 'm44-original', conversation_id: 'm44-conversation', title: '原训练任务', objective: '原训练目标',
  status: 'RUNNING', created_at: '2026-10-26T00:00:00Z', sequence: 10, plan: null,
  agents: [{ name: 'Manager', status: 'RUNNING', activity: '正在规划' }], review: null, review_history: [],
  blocked: null, report: null, result: null, delivery_status: 'NOT_GENERATED', error: null, telemetry: {},
  available_operations: ['view'],
}
const workflow: Operation = {
  id: 'op-workflow', mission_id: original.id, kind: 'workflow', status: 'RUNNING', run_id: 1,
  pause_id: null, cleanup_status: 'PENDING',
}
type Posted = { path: string; body: Record<string, unknown> }
async function setup(page: Page, mission = original, ops = [workflow]) {
  const missions = new Map<string, Mission>([[mission.id, structuredClone(mission)]])
  const operations = new Map<string, Operation[]>([[mission.id, structuredClone(ops)]])
  const posts: Posted[] = []
  const errors: string[] = []
  const state = { missions, operations, posts, errors, post: async (_route: Route, _posted: Posted) => {} }
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => { if (message.type() === 'error' && message.text().includes('same key')) errors.push(message.text()) })
  await page.addInitScript((id) => localStorage.setItem('fait.mission', id), mission.id)
  await page.route((url) => url.pathname.startsWith('/api/'), async (route) => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() === 'POST') {
      const posted = { path, body: route.request().postDataJSON() }
      posts.push(posted)
      return state.post(route, posted)
    }
    if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: '' })
    const id = path.split('/')[3]
    const data = path === '/api/health' ? { status: 'ok', mode: 'live' }
      : path === '/api/player' ? { name: 'Fixture', attributes: {}, metadata: {} }
      : path === '/api/missions' ? [...missions.values()]
      : path.endsWith('/operations') ? operations.get(id) || []
      : path.endsWith('/report') ? { title: missions.get(id)?.title, markdown: '# 原报告\n原报告正文' }
      : missions.has(id) && path === `/api/missions/${id}` ? missions.get(id) : []
    return route.fulfill({ json: data })
  })
  return state
}
function cancelled(mission: Mission): Mission {
  return { ...mission, status: 'CANCELLED', sequence: mission.sequence + 1, blocked: null,
    agents: mission.agents.map((agent) => ({ ...agent, status: 'CANCELLED', activity: '已取消' })) }
}
function receipt(post: Posted, op: Operation, accepted = true) {
  return { kind: 'cancellation', request_id: post.body.request_id, mission_id: op.mission_id, operation_id: op.id,
    run_id: op.run_id, accepted, outcome: accepted ? 'CANCELLED' : 'ALREADY_TERMINAL',
    status: accepted ? 'CANCELLED' : 'COMPLETED', cleanup_status: accepted ? 'PENDING' : 'COMPLETE', replayed: false }
}

test('M4.4 lost cancel response reloads the same target and request, then observes cleanup', async ({ page }) => {
  const state = await setup(page)
  state.post = async (route, posted) => {
    if (state.posts.length === 1) return route.abort('failed')
    state.missions.set(original.id, cancelled(original))
    state.operations.set(original.id, [{ ...workflow, status: 'CANCELLED' }])
    return route.fulfill({ json: receipt(posted, workflow) })
  }
  await page.goto('/')
  await page.getByRole('button', { name: '取消任务', exact: true }).click()
  await expect(page.getByRole('alert')).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: '取消任务', exact: true }).click()
  await expect.poll(() => state.posts.length).toBe(2)
  expect(state.posts[1]).toEqual(state.posts[0])
  expect(state.posts[1].path).toBe(`/api/missions/${original.id}/operations/${workflow.id}/cancel`)
  await expect(page.getByText(/正在清理执行资源/)).toBeVisible()
  state.operations.set(original.id, [{ ...workflow, status: 'CANCELLED', cleanup_status: 'COMPLETE' }])
  await expect(page.getByText(/执行资源已清理/)).toBeVisible()
  await expect(page.locator('.status-badge')).toHaveText('已取消')
  await expect(page.locator('.agent-row .spin')).toHaveCount(0)
  await expect(page.getByPlaceholder('任务已取消，请新建任务或创建关联重试。')).toBeDisabled()
  await expect(page.getByText('任务已取消，未生成计划。')).toBeVisible()
  await expect(page.getByRole('button', { name: '取消任务', exact: true })).toHaveCount(0)
  expect(state.errors).toEqual([])
  await page.screenshot({ path: test.info().outputPath('cancelled-cleanup.png'), fullPage: true })
})

test('M4.4 stale pause cancel is rejected and the refreshed pause has a new target identity', async ({ page }) => {
  const paused = { ...workflow, status: 'PAUSED' as const, pause_id: 'pause-one', cleanup_status: 'COMPLETE' as const }
  const pausedMission: Mission = { ...original, status: 'BLOCKED', review: {
    availability: 'COMPLETED', decision: 'BLOCKED', summary: '原信息缺口', affected_subtasks: [], severity: 'HIGH',
  } }
  const state = await setup(page, pausedMission, [paused])
  state.post = async (route, posted) => {
    if (state.posts.length === 1) {
      state.operations.set(original.id, [{ ...paused, run_id: 2, pause_id: 'pause-two' }])
      return route.fulfill({ status: 409, json: { detail: '执行代次或等待表单已变化，请刷新当前操作' } })
    }
    state.missions.set(original.id, cancelled(pausedMission))
    state.operations.set(original.id, [{ ...paused, run_id: 2, pause_id: 'pause-two', status: 'CANCELLED' }])
    return route.fulfill({ json: receipt(posted, { ...paused, run_id: 2 }) })
  }
  await page.goto('/')
  const oldButton = await page.getByRole('button', { name: '取消任务', exact: true }).elementHandle()
  await page.getByRole('button', { name: '取消任务', exact: true }).click()
  await expect.poll(() => state.posts.length).toBe(1)
  await expect(page.getByText(/任务执行正在等待你的输入/)).toBeVisible()
  // The row is remounted for the new run/pause; it cannot reuse the old request.
  await expect.poll(() => oldButton!.evaluate((element) => element.isConnected)).toBe(false)
  await page.getByRole('button', { name: '取消任务', exact: true }).click()
  await expect.poll(() => state.posts.length).toBe(2)
  expect(state.posts[0].body).toMatchObject({ expected_run_id: 1, expected_pause_id: 'pause-one' })
  expect(state.posts[1].body).toMatchObject({ expected_run_id: 2, expected_pause_id: 'pause-two' })
  expect(state.posts[1].body.request_id).not.toBe(state.posts[0].body.request_id)
  await expect(page.getByText('原任务审查记录（任务已取消）')).toBeVisible()
  await expect(page.getByText('已收到补充信息，团队继续处理中')).toHaveCount(0)
  expect(state.errors).toEqual([])
})

for (const completedFirst of [false, true]) {
  test(`M4.4 explanation cancel ${completedFirst ? 'loses to completion' : 'preserves original report'}`, async ({ page }) => {
    const completed: Mission = { ...original, status: 'COMPLETED', result: '原报告结论', delivery_status: 'PUBLISHABLE',
      report: { title: '原训练任务', plan_version: 1 }, agents: [], available_operations: ['view', 'explain'] }
    const explanation: Operation = { ...workflow, id: 'op-explanation', kind: 'explanation' }
    const state = await setup(page, completed, [{ ...workflow, status: 'COMPLETED' }, explanation])
    state.post = async (route, posted) => {
      state.operations.set(original.id, [{ ...workflow, status: 'COMPLETED' },
        { ...explanation, status: completedFirst ? 'COMPLETED' : 'CANCELLED', cleanup_status: 'COMPLETE' }])
      return route.fulfill({ json: receipt(posted, explanation, !completedFirst) })
    }
    await page.goto('/')
    await expect(page.getByRole('button', { name: '取消任务', exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: '取消本次解释', exact: true }).click()
    expect(state.posts[0].path).toContain('/operations/op-explanation/cancel')
    await expect(page.locator('.status-badge')).toHaveText('已完成')
    await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
    if (completedFirst) await expect(page.getByText('该操作已经结束，无需取消。')).toBeVisible()
    else await expect(page.getByText(/本次解释已取消，原报告保留/)).toBeVisible()
    expect(state.errors).toEqual([])
  })
}

test('M4.4 a late cancel response cannot switch or overwrite the newly selected mission', async ({ page }) => {
  const state = await setup(page)
  const other = { ...original, id: 'm44-other', title: '另一项任务', objective: '另一项目标', status: 'COMPLETED' as const, agents: [] }
  state.missions.set(other.id, other)
  let release: () => void = () => {}
  const gate = new Promise<void>((resolve) => { release = resolve })
  state.post = async (route, posted) => {
    await gate
    state.missions.set(original.id, cancelled(original))
    return route.fulfill({ json: receipt(posted, workflow) })
  }
  await page.goto('/')
  await page.getByRole('button', { name: '取消任务', exact: true }).click()
  await expect.poll(() => state.posts.length).toBe(1)
  await page.getByRole('button', { name: '任务历史', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: /另一项任务/ }).click()
  await expect(page.getByRole('heading', { name: '另一项任务', exact: true })).toBeVisible()
  release()
  await expect(page.locator('.status-badge')).toHaveText('已完成')
  await expect.poll(() => page.evaluate(() => localStorage.getItem('fait.mission'))).toBe(other.id)
  expect(state.posts[0].path).toContain(`/missions/${original.id}/`)
  expect(state.errors).toEqual([])
})

test('M4.4 cancelled checkpoint results are not offered as history for a retry', async ({ page }) => {
  const mission: Mission = { ...cancelled(original), available_operations: ['view', 'retry'], input_reference: {
    verification: 'VERIFIED', context: { career_id: 'career', branch_id: 'main', player_id: 'player' },
    state_version: 'v1', snapshot_id: 's1', source_types: ['demo_fixture'],
  }, plan: { version: 1, objective: '原计划', reason: '', subtasks: [{ id: 'training', title: '内部候选成果',
    status: 'COMPLETED', assigned_agent: 'Coach', revision_count: 0, result_version: 1, result_summary: '未交付的专业候选' }] } }
  const state = await setup(page, mission, [{ ...workflow, status: 'CANCELLED', cleanup_status: 'COMPLETE' }])
  await page.goto('/')
  await page.getByRole('button', { name: '创建关联重试任务', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await expect(page.getByRole('dialog').getByText('内部候选成果')).toHaveCount(0)
  await expect(page.getByRole('dialog').getByText('未交付的专业候选')).toHaveCount(0)
  expect(state.errors).toEqual([])
})

test('M4.4 cancelling an active mission resets the associated-task draft to retry', async ({ page }) => {
  const active: Mission = { ...original, input_reference: {
    verification: 'VERIFIED', context: { career_id: 'career', branch_id: 'main', player_id: 'player' },
    state_version: 'v1', snapshot_id: 's1', source_types: ['demo_fixture'],
  } }
  const state = await setup(page, active)
  state.post = async (route, posted) => {
    if (posted.path.endsWith('/cancel')) {
      state.missions.set(active.id, { ...cancelled(active), available_operations: ['view', 'retry'] })
      state.operations.set(active.id, [{ ...workflow, status: 'CANCELLED', cleanup_status: 'COMPLETE' }])
      return route.fulfill({ json: receipt(posted, workflow) })
    }
    const child = { ...active, id: 'm44-retry-child', title: '明确的新任务', objective: '明确的新任务' }
    state.missions.set(child.id, child)
    return route.fulfill({ status: 202, json: { message_id: 'new-message', mission_id: child.id,
      operation_id: 'op-retry', accepted: true, created: true, replayed: false } })
  }
  await page.goto('/')
  await page.getByRole('button', { name: '取消任务', exact: true }).click()
  await page.getByRole('button', { name: '创建关联重试任务', exact: true }).click()
  await page.getByLabel('发起原因', { exact: true }).fill('用户决定重新开始')
  await page.getByLabel('新的评估要求', { exact: true }).fill('明确的新任务')
  await page.getByRole('button', { name: '创建关联任务', exact: true }).click()
  await expect.poll(() => state.posts.length).toBe(2)
  expect(state.posts[1].body).toMatchObject({ parent_mission_id: active.id, operation: 'retry', include_report: false, selected_results: [] })
  await expect(page.getByRole('heading', { name: '明确的新任务', exact: true })).toBeVisible()
  expect(state.errors).toEqual([])
})
