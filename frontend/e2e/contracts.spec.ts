import { test, expect } from '@playwright/test'
import type { Mission } from '../src/types/mission'

for (const scenario of ['failed', 'invalidated', 'legacy'] as const) {
  test(`${scenario}: accurate result, review and unknown player display`, async ({ page }) => {
    const pageErrors: string[] = []
    page.on('pageerror', error => pageErrors.push(error.message))
    const mission: Mission = {
      id: 'contract-mission', conversation_id: 'contract-conversation', title: '契约验收任务',
      objective: '检查当前成果与事实显示', status: scenario === 'legacy' ? 'COMPLETED' : 'FAILED',
      created_at: '2026-10-01T10:00:00+08:00', sequence: 9,
      plan: { version: 2, objective: '当前计划', reason: '', subtasks: [
        { id: 'statistics', title: '保留统计', status: 'COMPLETED', assigned_agent: 'Analyst',
          revision_count: 0, result_summary: '已验证的比赛统计 714', result_version: 1 },
        { id: 'training', title: '训练建议', status: scenario === 'invalidated' ? 'INVALIDATED' : 'FAILED',
          assigned_agent: 'Coach', revision_count: 0, reason: '当前成果不可交付' },
      ] },
      agents: [{ name: 'Coach', status: 'FAILED', activity: '未输出结果' }],
      review: { availability: scenario === 'legacy' ? undefined : 'UNAVAILABLE',
        decision: scenario === 'legacy' ? 'PASS' : null, summary: '旧记录未验证或审查未完成',
        affected_subtasks: [], severity: 'INFO' },
      review_history: [], blocked: null, result: null,
      // A stale report must never authorize a button, even in malformed/old snapshots.
      report: { title: '旧报告', plan_version: 1 }, error: scenario === 'legacy' ? null : '未输出完整结果',
      telemetry: {}, delivery_status: 'NOT_GENERATED',
    }
    await page.addInitScript(() => localStorage.setItem('fait.mission', 'contract-mission'))
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const path = new URL(route.request().url()).pathname
      if (path.endsWith('/events')) return route.fulfill({ contentType: 'text/event-stream', body: '' })
      const data = path === '/api/health' ? { status: 'ok', mode: 'live' }
        : path === '/api/player' ? { name: null, club: null, position: null, nationality: null, age: null,
          overall: 0, height: null, weight: null, injury: null, preferred_foot: null,
          attributes: { physical: { speed: 0, stamina: null } }, long_term_goals: null }
        : path === '/api/missions/contract-mission' ? mission : []
      return route.fulfill({ json: data })
    })
    await page.goto('/')
    await expect(page.getByRole('heading', { name: '契约验收任务' })).toBeVisible()
    await expect(page.getByText('审查未完成', { exact: true })).toBeVisible()
    await expect(page.getByRole('button', { name: '查看报告', exact: true })).toHaveCount(0)
    await expect(page.getByRole('link', { name: '导出 Markdown 报告' })).toHaveCount(0)
    const player = page.locator('.player-panel')
    await expect(player.locator('.attribute').filter({ hasText: '速度' }).locator('b')).toHaveText('0')
    await expect(player.locator('.attribute').filter({ hasText: '耐力' }).locator('b')).toHaveText('未知')
    await expect(player.getByText('暂无记录', { exact: true })).toBeVisible()
    await page.getByText('查看已保留的专业结果', { exact: true }).click()
    await expect(page.getByText('已验证的比赛统计 714', { exact: true })).toBeVisible()
    if (scenario === 'invalidated') await expect(page.getByText('Coach·结果已失效')).toBeVisible()
    if (scenario === 'legacy') await expect(page.getByText('旧记录未验证，暂无可发布报告。')).toBeVisible()
    expect(pageErrors).toEqual([])
  })
}
