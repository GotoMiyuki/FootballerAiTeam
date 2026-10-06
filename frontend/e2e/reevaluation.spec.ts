import { test, expect } from '@playwright/test'
import type { Mission } from '../src/types/mission'

test('demo: associated evaluation keeps parent report, switches to child and restores lineage', async ({
  page,
}) => {
  await page.goto('/')
  const title = `来源任务 C1.2 ${Date.now()}`
  const objective = `关联新评估 ${Date.now()}`
  await page.getByLabel('任务需求', { exact: true }).fill(title)
  await page.getByRole('button', { name: '创建任务', exact: true }).click()
  await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
  const parentId = await page.evaluate(() => localStorage.getItem('fait.mission'))
  const original = await (await page.request.get(`/api/missions/${parentId}/report`)).json()
  await page.getByRole('button', { name: '根据新情况重新评估', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: '根据新情况重新评估', exact: true })
  await dialog.getByLabel('发起原因', { exact: true }).fill('新增比赛，时间改变')
  await dialog.getByLabel('新的评估要求', { exact: true }).fill(objective)
  const accepted = page.waitForResponse(
    (response) =>
      response.url().endsWith('/api/mission-continuations') && response.status() === 202,
  )
  await dialog.getByRole('button', { name: '创建关联任务', exact: true }).click()
  const created = await (await accepted).json()
  expect(created.created).toBe(true)
  expect(created.mission_id).not.toBe(parentId)
  await expect(page.getByRole('heading', { name: objective, exact: true }).first()).toBeVisible()
  await expect(page.getByRole('button', { name: '查看来源任务', exact: true })).toBeVisible()
  await expect(page.locator('.mission-input-reference')).toContainText(
    '发起原因：新增比赛，时间改变',
  )
  await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
  expect(await (await page.request.get(`/api/missions/${parentId}/report`)).json()).toEqual(
    original,
  )
  await page.reload()
  await expect(page.getByRole('heading', { name: objective, exact: true }).first()).toBeVisible()
  await page.screenshot({ path: 'test-results/c1-2-associated-evaluation.png', fullPage: true })
  await page.getByRole('button', { name: '查看来源任务', exact: true }).click()
  await expect(page.getByRole('heading', { name: title, exact: true }).first()).toBeVisible()
  expect(await page.evaluate(() => localStorage.getItem('fait.mission'))).toBe(parentId)
})

for (const edited of [false, true]) {
  test(`lost response, reload, ${edited ? 'edited draft creates a new request' : 'retry keeps the exact request'}`, async ({
    page,
  }) => {
    const parent: Mission = {
      id: 'parent-task',
      conversation_id: 'conv-reevaluation',
      title: '原报告',
      objective: '原目标',
      status: 'COMPLETED',
      created_at: '2026-10-02T10:00:00+08:00',
      sequence: 10,
      plan: {
        version: 1,
        objective: '原目标',
        reason: '',
        subtasks: [
          {
            id: 'training',
            title: '训练分析',
            status: 'COMPLETED',
            assigned_agent: 'Coach',
            revision_count: 0,
            result_version: 1,
            result_summary: '原有效建议',
          },
        ],
      },
      agents: [],
      review: null,
      review_history: [],
      blocked: null,
      report: { title: '原报告', plan_version: 1 },
      result: '原结论',
      error: null,
      telemetry: {},
      delivery_status: 'PUBLISHABLE',
      input_reference: {
        verification: 'VERIFIED',
        context: { career_id: 'career-one', branch_id: 'main', player_id: 'player-one' },
        state_version: 'v1',
        snapshot_id: 'snapshot-v1',
        source_types: ['game_observation'],
      },
      available_operations: ['view', 'explain', 'reevaluate'],
    }
    let child: Mission | undefined
    const posts: Record<string, unknown>[] = []
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.addInitScript(() => localStorage.setItem('fait.mission', 'parent-task'))
    await page.route(
      (url) => url.pathname.startsWith('/api/'),
      async (route) => {
        const path = new URL(route.request().url()).pathname
        if (path === '/api/mission-continuations') {
          const body = route.request().postDataJSON()
          posts.push(body)
          if (posts.length === 1) return route.abort('failed')
          child = {
            ...parent,
            id: 'child-task',
            title: body.content,
            objective: body.content,
            input_reference: {
              ...parent.input_reference!,
              state_version: 'v2',
              snapshot_id: 'snapshot-v2',
            },
            report: { title: body.content, plan_version: 1 },
            lineage: {
              parent_mission_id: parent.id,
              operation: 'reevaluate',
              reason: body.reason,
              request_id: body.request_id,
              history_references: [
                {
                  kind: 'report',
                  mission_id: parent.id,
                  subtask_id: null,
                  version: 1,
                  content_hash: 'hash-report',
                },
                {
                  kind: 'result',
                  mission_id: parent.id,
                  subtask_id: 'training',
                  version: 1,
                  content_hash: 'hash-result',
                },
              ],
            },
          }
          return route.fulfill({
            status: 202,
            json: { message_id: 'new-message', mission_id: child.id, created: false },
          })
        }
        if (path.endsWith('/events'))
          return route.fulfill({ contentType: 'text/event-stream', body: '' })
        const data =
          path === '/api/health'
            ? { status: 'ok', mode: 'live' }
            : path === '/api/player'
              ? { name: '最新球员', attributes: {} }
              : path === '/api/missions/parent-task'
                ? parent
                : path === '/api/missions/child-task'
                  ? child
                  : path === '/api/missions'
                    ? [parent, ...(child ? [child] : [])]
                    : []
        return route.fulfill({ json: data })
      },
    )
    await page.goto('/')
    await page.getByRole('button', { name: '根据新情况重新评估', exact: true }).click()
    await page.getByLabel('发起原因', { exact: true }).fill('新增比赛')
    await page.getByLabel('新的评估要求', { exact: true }).fill('每天仅训练20分钟')
    await page.getByRole('checkbox', { name: '训练分析 · 版本 1', exact: true }).check()
    await page.getByRole('button', { name: '创建关联任务', exact: true }).click()
    await expect(page.getByRole('button', { name: '重试创建', exact: true })).toBeVisible()
    await page.reload()
    await page.getByRole('button', { name: '根据新情况重新评估', exact: true }).click()
    await expect(page.getByLabel('新的评估要求', { exact: true })).toHaveValue('每天仅训练20分钟')
    if (edited) await page.getByLabel('新的评估要求', { exact: true }).fill('每天仅训练30分钟')
    await page.getByRole('button', { name: '创建关联任务', exact: true }).click()
    await expect(page.getByRole('button', { name: '查看来源任务', exact: true })).toBeVisible()
    expect(posts).toHaveLength(2)
    expect(posts[0]).toMatchObject({
      parent_mission_id: parent.id,
      include_report: true,
      selected_results: [{ subtask_id: 'training', version: 1 }],
    })
    if (edited) expect(posts[1].request_id).not.toBe(posts[0].request_id)
    else expect(posts[1]).toEqual(posts[0])
    await expect(page.locator('.mission-input-reference')).toContainText('原数据版本：v2')
    await page.getByRole('button', { name: '查看来源任务', exact: true }).click()
    await expect(page.locator('.mission-input-reference')).toContainText('原数据版本：v1')
    expect(errors).toEqual([])
  })
}
