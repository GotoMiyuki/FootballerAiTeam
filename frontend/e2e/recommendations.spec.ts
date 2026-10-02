import { test, expect } from '@playwright/test'
import type { Mission } from '../src/types/mission'
import type { Recommendation, RecommendationList } from '../src/types/recommendation'

const reference = {
  verification: 'VERIFIED' as const,
  context: { career_id: 'career', branch_id: 'main', player_id: 'p' },
  state_version: 'baseline-v1',
  snapshot_id: 'snapshot-v1',
  source_types: ['demo_fixture'],
}
const mission: Mission = {
  id: 'recommendation-mission',
  conversation_id: 'recommendation-conversation',
  title: '训练建议来源任务',
  objective: '训练建议来源任务',
  status: 'COMPLETED',
  created_at: '2026-10-02T10:00:00+08:00',
  sequence: 20,
  plan: {
    version: 1,
    objective: '训练建议来源任务',
    reason: '',
    subtasks: [
      {
        id: 'training',
        title: '训练分析',
        status: 'COMPLETED',
        assigned_agent: 'Coach',
        revision_count: 0,
        result_version: 1,
      },
    ],
  },
  agents: [],
  review: {
    availability: 'COMPLETED',
    decision: 'PASS',
    summary: '通过',
    affected_subtasks: [],
    severity: 'INFO',
  },
  review_history: [],
  blocked: null,
  result: '原报告结论',
  report: { title: '原报告', plan_version: 1 },
  error: null,
  telemetry: {},
  delivery_status: 'PUBLISHABLE',
  input_reference: reference,
  available_operations: ['view', 'explain'],
}
const recommendation: Recommendation = {
  schema_version: 1,
  recommendation_id: 'rec_training_original',
  revision: 1,
  context: reference.context,
  source: {
    mission_id: mission.id,
    subtask_id: 'training',
    capability: 'skill_training',
    result_version: 1,
    payload_position: '/focus_areas/0',
    payload_hash: 'source-hash',
    input_fingerprint: 'input-fingerprint',
    review_plan_version: 1,
    reviewed_result_version: 1,
    mapping_version: 'training-focus-v1',
  },
  content: {
    title: '接球与传球焦点',
    text: '接球与传球焦点',
    expected_goal: '明确训练方向',
    basis: '按已审查专业成果投影',
    limitations: ['样本来源，不证明已执行'],
  },
  applicability: {
    input_reference: reference,
    conditions: ['每天20分钟'],
    game_versions: [],
    game_mode: null,
    valid_window: null,
    window_reason: '来源没有可验证的有效窗口',
  },
  execution_support: {
    status: 'pending_verification',
    reason: '尚无目标游戏的操作证据',
    evidence_references: [],
  },
  evaluation_spec: {
    status: 'undefined',
    baseline_reference: reference,
    metrics: [],
    observation_window: null,
    reason: '没有统一单位与观察窗口，不能计算效果或达标结论',
  },
  validity: 'current',
  validity_reason: '来源有效，数据版本一致',
  checked_state_version: 'baseline-v1',
  created_at: '2026-10-02T10:00:00+08:00',
}

for (const scenario of ['current', 'withdrawn', 'superseded'] as const) {
  test(`recommendations: ${scenario} provenance, unknown support, refresh and original report`, async ({
    page,
  }) => {
    let refreshed = false
    const mutations: string[] = []
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.addInitScript(() => localStorage.setItem('fait.mission', 'recommendation-mission'))
    await page.route(
      (url) => url.pathname.startsWith('/api/'),
      async (route) => {
        const path = new URL(route.request().url()).pathname
        if (route.request().method() !== 'GET') mutations.push(path)
        if (path.endsWith('/events'))
          return route.fulfill({ contentType: 'text/event-stream', body: '' })
        const record = {
          ...recommendation,
          validity:
            scenario === 'current'
              ? refreshed
                ? ('needs_reassessment' as const)
                : ('current' as const)
              : scenario,
          validity_reason:
            scenario === 'current'
              ? refreshed
                ? '球员数据已变化，需重新评估当前适用性'
                : '来源有效，数据版本一致'
              : '原专业成果不可作为当前建议',
          checked_state_version: refreshed ? 'latest-v2' : 'baseline-v1',
        }
        const result: RecommendationList = {
          mission_id: mission.id,
          availability: scenario === 'withdrawn' ? 'UNAVAILABLE' : 'AVAILABLE',
          reason: '建议来源检查',
          items: [record],
        }
        const data =
          path === '/api/health'
            ? { status: 'ok', mode: 'live' }
            : path === '/api/player'
              ? { name: 'Test Player', attributes: {}, long_term_goals: [] }
              : path === `/api/missions/${mission.id}`
                ? mission
                : path.endsWith('/recommendations')
                  ? result
                  : path.endsWith('/report')
                    ? { title: '原报告', markdown: '# 原报告 v1\n原数据与结论仍然保留' }
                    : []
        return route.fulfill({ json: data })
      },
    )
    await page.goto('/')
    const panel = page.getByRole('region', { name: '训练焦点建议' })
    await expect(panel.getByRole('heading', { name: '接球与传球焦点' })).toBeVisible()
    await expect(panel).toContainText('执行支持：待验证')
    await panel.getByText('查看来源与适用范围', { exact: true }).click()
    await expect(panel).toContainText('training · v1')
    await expect(panel).toContainText('/focus_areas/0')
    await expect(panel).toContainText('baseline-v1（测试样本）')
    await expect(panel).toContainText('不能计算效果或达标结论')
    await expect(page.getByRole('button', { name: /采纳|已执行/ })).toHaveCount(0)
    if (scenario === 'current') {
      refreshed = true
      await panel.getByRole('button', { name: '刷新适用性' }).click()
      await expect(panel).toContainText('当前适用性待重评')
      await expect(panel).toContainText('baseline-v1（测试样本）')
      await page.evaluate(() => window.scrollTo(0, 0))
      await page.screenshot({ path: 'test-results/e1-1-recommendations.png', fullPage: true })
      await page.reload()
      await expect(panel).toContainText('当前适用性待重评')
      await panel.getByText('查看来源与适用范围', { exact: true }).click()
    } else {
      await expect(panel).toContainText(
        scenario === 'withdrawn' ? '来源不可用 · 历史记录' : '已被替代 · 历史记录',
      )
    }
    await panel.getByRole('button', { name: '查看来源报告' }).click()
    await expect(page.getByRole('heading', { name: '原报告 v1' })).toBeVisible()
    expect(mutations).toEqual([])
    expect(errors).toEqual([])
  })
}
