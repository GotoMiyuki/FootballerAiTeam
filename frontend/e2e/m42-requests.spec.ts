import { test, expect } from '@playwright/test'
import type { Mission } from '../src/types/mission'

const base: Mission = {
  id: 'm42-mission',
  conversation_id: 'm42-conversation',
  title: 'M42 request',
  objective: 'M42 request',
  status: 'COMPLETED',
  created_at: '2026-10-26T00:00:00Z',
  sequence: 10,
  plan: null,
  agents: [],
  review: {
    availability: 'COMPLETED',
    decision: 'PASS',
    summary: 'passed',
    affected_subtasks: [],
    severity: 'INFO',
  },
  review_history: [],
  blocked: null,
  report: { title: 'M42 request', plan_version: 1 },
  result: 'original report',
  delivery_status: 'PUBLISHABLE',
  error: null,
  telemetry: {},
  available_operations: ['view', 'explain'],
}

for (const edit of [false, true]) {
  test(`M4.2 lost create response: ${edit ? 'edited intent gets a new request' : 'reload reuses request and draft conversation'}`, async ({
    page,
  }) => {
    const posts: Record<string, unknown>[] = []
    let selected = base
    await page.route(
      (url) => url.pathname.startsWith('/api/'),
      async (route) => {
        const path = new URL(route.request().url()).pathname
        if (route.request().method() === 'POST') {
          const body = route.request().postDataJSON()
          posts.push(body)
          if (posts.length === 1) return route.abort('failed')
          selected = {
            ...base,
            title: body.content,
            objective: body.content,
            conversation_id: body.conversation_id,
          }
          return route.fulfill({
            status: 202,
            json: {
              message_id: 'accepted',
              mission_id: base.id,
              operation_id: 'op-one',
              accepted: true,
              replayed: !edit,
            },
          })
        }
        if (path.endsWith('/events'))
          return route.fulfill({ contentType: 'text/event-stream', body: '' })
        const value =
          path === '/api/health'
            ? { status: 'ok', mode: 'live' }
            : path === '/api/player'
              ? { name: 'Fixture', attributes: {}, metadata: {} }
              : path === '/api/missions/m42-mission'
                ? selected
                : []
        return route.fulfill({ json: value })
      },
    )
    await page.goto('/')
    const content = page.getByLabel('任务需求', { exact: true })
    await content.fill('M42 request')
    await page.getByRole('button', { name: '创建任务', exact: true }).click()
    await expect.poll(() => posts.length).toBe(1)
    await expect(page.getByRole('button', { name: '创建任务', exact: true })).toBeEnabled()
    await page.reload()
    await expect(content).toHaveValue('M42 request')
    if (edit) await content.fill('M42 edited request')
    await page.getByRole('button', { name: '创建任务', exact: true }).click()
    await expect.poll(() => posts.length).toBe(2)
    expect(posts[1].conversation_id).toBe(posts[0].conversation_id)
    if (edit) expect(posts[1].request_id).not.toBe(posts[0].request_id)
    else expect(posts[1]).toEqual(posts[0])
    await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
    await page.getByRole('button', { name: '新建任务', exact: true }).first().click()
    await page.getByLabel('任务需求', { exact: true }).fill('M42 request')
    await page.getByRole('button', { name: '创建任务', exact: true }).click()
    await expect.poll(() => posts.length).toBe(3)
    expect(posts[2].request_id).not.toBe(posts[1].request_id)
    expect(posts[2].conversation_id).not.toBe(posts[1].conversation_id)
  })
}

test('M4.2 lost input response replays exact request; later same-field pause starts empty', async ({
  page,
}) => {
  const fields = [
    {
      key: 'information',
      label: '请补充以上信息',
      input_type: 'text' as const,
      required: true,
      min: null,
      max: null,
      options: [],
    },
  ]
  let mission: Mission = {
    ...base,
    status: 'BLOCKED',
    report: null,
    result: null,
    delivery_status: 'NOT_GENERATED',
    available_operations: ['view', 'supply_input'],
    blocked: {
      pause_id: 'pause-one',
      reason: 'missing_user_input',
      message: 'need input',
      required_inputs: fields,
    },
  }
  const posts: Record<string, unknown>[] = []
  await page.addInitScript(() => localStorage.setItem('fait.mission', 'm42-mission'))
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const path = new URL(route.request().url()).pathname
      if (route.request().method() === 'POST') {
        posts.push(route.request().postDataJSON())
        if (posts.length === 1) return route.abort('failed')
        if (posts.length === 2)
          mission = {
            ...mission,
            sequence: 11,
            blocked: { ...mission.blocked!, pause_id: 'pause-two' },
          }
        return route.fulfill({
          status: 202,
          json: {
            message_id: 'input',
            mission_id: base.id,
            operation_id: 'op-workflow',
            accepted: true,
            replayed: posts.length === 2,
          },
        })
      }
      if (path.endsWith('/events'))
        return route.fulfill({ contentType: 'text/event-stream', body: '' })
      const value =
        path === '/api/health'
          ? { status: 'ok', mode: 'live' }
          : path === '/api/player'
            ? { name: 'Fixture', attributes: {}, metadata: {} }
            : path === '/api/missions/m42-mission'
              ? mission
              : []
      return route.fulfill({ json: value })
    },
  )
  await page.goto('/')
  await page.getByLabel('请补充以上信息', { exact: true }).fill('first answer')
  await page.getByRole('button', { name: '提交并继续', exact: true }).click()
  await expect.poll(() => posts.length).toBe(1)
  await expect(page.getByRole('button', { name: '提交并继续', exact: true })).toBeEnabled()
  await page.reload()
  await expect(page.getByLabel('请补充以上信息', { exact: true })).toHaveValue('first answer')
  await page.getByRole('button', { name: '提交并继续', exact: true }).click()
  await expect.poll(() => posts.length).toBe(2)
  expect(posts[1]).toEqual(posts[0])
  await expect(page.getByLabel('请补充以上信息', { exact: true })).toHaveValue('')
  await page.getByLabel('请补充以上信息', { exact: true }).fill('second answer')
  await page.getByRole('button', { name: '提交并继续', exact: true }).click()
  await expect.poll(() => posts.length).toBe(3)
  expect(posts[2].pause_id).toBe('pause-two')
  expect(posts[2].request_id).not.toBe(posts[1].request_id)
})
