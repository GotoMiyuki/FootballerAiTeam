import { test, expect } from '@playwright/test'
import type { Mission } from '../src/types/mission'

for (const scenario of ['explanation-failure', 'approval', 'missing-checkpoint'] as const) {
  test(`${scenario}: original identity and explicit operations`, async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    const completed = scenario === 'explanation-failure'
    const mission: Mission = {
      id: 'original-task',
      conversation_id: 'original-conversation',
      title: '原任务报告',
      objective: '解释旧安排',
      status: completed ? 'COMPLETED' : 'BLOCKED',
      created_at: '2026-10-02T10:00:00+08:00',
      sequence: 10,
      plan: null,
      agents: [],
      review: {
        availability: 'COMPLETED',
        decision: 'PASS',
        summary: '原成果已通过',
        severity: 'INFO',
        affected_subtasks: [],
      },
      review_history: [],
      blocked: completed
        ? null
        : {
            reason: 'report_approval',
            message: '请确认生成报告',
            required_inputs: [
              {
                key: 'approved',
                label: '我已查看计划，继续生成报告',
                input_type: 'boolean',
                required: true,
                min: null,
                max: null,
                options: [],
              },
            ],
          },
      report: completed ? { title: '原任务报告', plan_version: 1 } : null,
      result: completed ? '原结论保留' : null,
      error: null,
      telemetry: {},
      delivery_status: completed ? 'PUBLISHABLE' : 'NOT_GENERATED',
      input_reference: {
        verification: 'VERIFIED',
        context: { career_id: 'career-one', branch_id: 'main', player_id: 'player-one' },
        state_version: 'original-v1',
        snapshot_id: 'original-snapshot',
        source_types: ['game_observation'],
      },
      available_operations:
        scenario === 'missing-checkpoint'
          ? ['view']
          : ['view', completed ? 'explain' : 'approve_report'],
      resume_error:
        scenario === 'missing-checkpoint' ? '原 checkpoint 不存在，无法恢复。请新建任务。' : null,
    }
    const posts: { path: string; body: Record<string, unknown> }[] = []
    let explained = false
    await page.addInitScript(() => localStorage.setItem('fait.mission', 'original-task'))
    await page.route(
      (url) => url.pathname.startsWith('/api/'),
      async (route) => {
        const path = new URL(route.request().url()).pathname
        if (route.request().method() === 'POST') {
          posts.push({ path, body: route.request().postDataJSON() })
          explained = true
          return route.fulfill({
            status: 202,
            json: { message_id: 'question', mission_id: mission.id },
          })
        }
        if (path.endsWith('/events'))
          return route.fulfill({ contentType: 'text/event-stream', body: '' })
        const data =
          path === '/api/health'
            ? { status: 'ok', mode: 'live' }
            : path === '/api/player'
              ? { name: '最新球员', attributes: {}, metadata: { state_version: 'latest-v2' } }
              : path === '/api/missions/original-task'
                ? mission
                : path === '/api/missions/original-task/report'
                  ? { title: '原任务报告', markdown: '# 原报告\n原结论保留' }
                  : path === '/api/conversations/original-conversation/messages' && explained
                    ? [
                        {
                          id: 'failed-explanation',
                          role: 'system',
                          content: '报告解释暂时失败。已有报告仍可查看。',
                          mission_id: mission.id,
                          kind: 'explanation',
                          operation_status: 'FAILED',
                          created_at: mission.created_at,
                        },
                      ]
                    : []
        return route.fulfill({ json: data })
      },
    )
    await page.goto('/')
    await expect(
      page.getByRole('heading', { name: '原任务报告', exact: true }).first(),
    ).toBeVisible()
    const reference = page.locator('.mission-input-reference')
    await expect(reference).toContainText('original-task')
    await expect(reference).toContainText('career-one / main / player-one')
    await expect(reference).toContainText('original-v1')
    await expect(reference).not.toContainText('latest-v2')
    expect(posts).toHaveLength(0)
    if (completed) {
      await page.getByLabel('报告解释问题', { exact: true }).fill('请解释原结论')
      await page.getByRole('button', { name: '解释报告', exact: true }).click()
      await expect(page.getByText('解释失败', { exact: true })).toBeVisible()
      expect(posts).toHaveLength(1)
      expect(posts[0].body).toMatchObject({
        intent: 'explain',
        mission_id: mission.id,
        conversation_id: mission.conversation_id,
      })
      await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
      await page.getByRole('button', { name: '查看报告', exact: true }).click()
      await expect(page.getByText('原结论保留', { exact: false })).toBeVisible()
      await page.getByRole('button', { name: '关闭', exact: true }).click()
      await page.getByRole('button', { name: '根据新情况新建任务', exact: true }).click()
      await expect(page.getByLabel('任务需求', { exact: true })).toBeEditable()
      expect(await page.evaluate(() => localStorage.getItem('fait.mission'))).toBeNull()
      expect(posts).toHaveLength(1) // choosing a new draft never reruns the old task
    } else {
      await expect(page.getByLabel('补充任务信息', { exact: true })).toBeDisabled()
      const resume = page.getByRole('button', { name: '提交并继续', exact: true })
      if (scenario === 'missing-checkpoint') {
        await expect(
          page.getByText('原 checkpoint 不存在，无法恢复。请新建任务。', { exact: true }),
        ).toBeVisible()
        await expect(resume).toBeDisabled()
        expect(posts).toHaveLength(0)
      } else {
        await page.getByLabel('我已查看计划，继续生成报告', { exact: true }).selectOption('true')
        await resume.click()
        await expect.poll(() => posts.length).toBe(1)
        expect(posts[0]).toEqual({
          path: '/api/missions/original-task/input',
          body: { values: { approved: true } },
        })
      }
    }
    expect(errors).toEqual([])
  })
}
