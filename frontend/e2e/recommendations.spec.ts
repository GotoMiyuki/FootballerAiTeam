import { test, expect } from '@playwright/test'
import type { Mission } from '../src/types/mission'
import type {
  Recommendation,
  RecommendationList,
  RecommendationActivity,
  DecisionRequest,
  ExecutionRequest,
  DecisionRecord,
  ExecutionRecord,
} from '../src/types/recommendation'
import type { Page } from '@playwright/test'

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
                  : path.endsWith('/activity')
                    ? emptyActivity()
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

function emptyActivity(): RecommendationActivity {
  return {
    recommendation_id: recommendation.recommendation_id,
    recommendation_revision: 1,
    context: reference.context,
    current_decision_id: null,
    current_execution_ids: [],
    decisions: [],
    executions: [],
  }
}

async function actionApi(page: Page, loseResponse?: 'decisions' | 'executions') {
  const activity = emptyActivity()
  const requests: { path: string; body: DecisionRequest | ExecutionRequest }[] = []
  const saved = new Map<string, { record: DecisionRecord | ExecutionRecord; body: string }>()
  let lost = false
  let validity: Recommendation['validity'] = 'current'
  await page.addInitScript(() => localStorage.setItem('fait.mission', 'recommendation-mission'))
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const path = new URL(route.request().url()).pathname
      if (path.endsWith('/events'))
        return route.fulfill({ contentType: 'text/event-stream', body: '' })
      if (route.request().method() === 'POST') {
        const body = route.request().postDataJSON() as DecisionRequest | ExecutionRequest
        requests.push({ path, body })
        const duplicate = saved.get(body.request_id)
        if (duplicate) {
          if (duplicate.body !== JSON.stringify(body))
            return route.fulfill({ status: 409, json: { detail: 'request conflict' } })
          return route.fulfill({ json: { record: duplicate.record, created: false } })
        }
        const common = {
          schema_version: 1,
          recommendation_id: recommendation.recommendation_id,
          source: recommendation.source,
          recommendation_validity: validity,
          checked_state_version: reference.state_version,
          created_at: '2026-10-24T10:00:00+08:00',
        }
        let record: DecisionRecord | ExecutionRecord
        if (path.endsWith('/decisions')) {
          const choice = body as DecisionRequest
          if (choice.status === 'ADOPTED' && validity !== 'current')
            return route.fulfill({ status: 409, json: { detail: '需重新评估' } })
          if (choice.previous_decision_id !== activity.current_decision_id)
            return route.fulfill({ status: 409, json: { detail: '选择已变化，请刷新' } })
          record = {
            ...common,
            ...choice,
            decision_id: `decision-${activity.decisions.length + 1}`,
            revision: activity.decisions.length + 1,
          }
          activity.decisions.push(record)
          activity.current_decision_id = record.decision_id
        } else {
          const feedback = body as ExecutionRequest
          const previous = activity.executions.find(
            (item) => item.execution_id === feedback.corrects_execution_id,
          )
          record = {
            ...common,
            ...feedback,
            execution_id: `execution-${activity.executions.length + 1}`,
            revision: previous ? previous.revision + 1 : 1,
            source_type: 'user_reported',
          }
          activity.executions.push(record)
          activity.current_execution_ids = activity.current_execution_ids.filter(
            (id) => id !== feedback.corrects_execution_id,
          )
          activity.current_execution_ids.push(record.execution_id)
        }
        saved.set(body.request_id, { record, body: JSON.stringify(body) })
        if (!lost && loseResponse && path.endsWith(`/${loseResponse}`)) {
          lost = true
          return route.abort('failed')
        }
        return route.fulfill({ json: { record, created: true } })
      }
      const data =
        path === '/api/health'
          ? { status: 'ok', mode: 'live' }
          : path === '/api/player'
            ? { name: 'Test Player', attributes: {}, long_term_goals: [] }
            : path === `/api/missions/${mission.id}`
              ? mission
              : path.endsWith('/recommendations')
                ? {
                    mission_id: mission.id,
                    availability: 'AVAILABLE',
                    reason: '',
                    items: [{ ...recommendation, validity }],
                  }
                : path.endsWith('/activity')
                  ? activity
                  : []
      return route.fulfill({ json: data })
    },
  )
  return {
    activity,
    requests,
    setValidity: (value: Recommendation['validity']) => {
      validity = value
    },
  }
}

test('E1.2 choices, independent feedback, correction, withdrawal and reload retain history', async ({
  page,
}) => {
  const api = await actionApi(page)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/')
  const panel = page.getByRole('region', { name: '训练焦点建议' })
  await expect(panel).toContainText('未知 · 尚无执行反馈')
  await panel.getByText('记录选择与执行反馈', { exact: true }).click()
  await panel.getByRole('combobox', { name: '玩家选择', exact: true }).selectOption('ADOPTED')
  await panel.getByRole('button', { name: '保存选择', exact: true }).click()
  await expect(panel).toContainText('选择已保存')
  await expect(panel).toContainText('未知 · 尚无执行反馈')
  await panel.getByRole('combobox', { name: '执行情况', exact: true }).selectOption('PARTIAL')
  await panel.getByRole('textbox', { name: '实际行动或未知说明' }).fill('尝试了10分钟，自报速度99')
  await panel.getByRole('combobox', { name: '执行时间域' }).selectOption('game')
  await panel.getByRole('textbox', { name: '实际时间或比赛范围' }).fill('游戏第3周')
  await panel.screenshot({ path: 'test-results/e1-2-action-forms.png' })
  await panel.getByRole('button', { name: '保存执行反馈', exact: true }).click()
  await expect(panel).toContainText('自报部分执行（游戏时间：游戏第3周）')
  await panel.getByRole('combobox', { name: '玩家选择', exact: true }).selectOption('REJECTED')
  await panel.getByRole('textbox', { name: '选择理由' }).fill('撤回采纳')
  await panel.getByRole('button', { name: '保存选择', exact: true }).click()
  await expect.poll(() => api.activity.decisions.length).toBe(2)
  await expect(panel).toContainText('自报部分执行（游戏时间：游戏第3周）')
  await panel.getByRole('combobox', { name: '执行记录方式' }).selectOption('execution-1')
  await panel.getByRole('textbox', { name: '实际行动或未知说明' }).fill('撤回未经核实的执行反馈')
  await panel.getByRole('textbox', { name: '更正原因' }).fill('记忆不清，维持未知')
  await panel.getByRole('button', { name: '保存执行反馈', exact: true }).click()
  await expect.poll(() => api.activity.executions.length).toBe(2)
  await page.reload()
  await expect(panel).toContainText('选择：拒绝')
  await expect(panel).toContainText('执行：未知（未知窗口）')
  await panel.getByText('查看选择与执行历史（4）', { exact: true }).click()
  await expect(panel).toContainText('前次选择 decision-1')
  await expect(panel).toContainText('更正 execution-1：记忆不清，维持未知')
  await expect(panel).toContainText('自报速度99')
  await expect(panel).toContainText('执行支持：待验证')
  await expect(panel).toContainText('等待观察：尚未关联后续观察')
  await page.screenshot({ path: 'test-results/e1-2-actions.png', fullPage: true })
  expect(api.activity.current_execution_ids).toEqual(['execution-2'])
  expect(errors).toEqual([])
})

for (const kind of ['decisions', 'executions'] as const) {
  test(`E1.2 lost ${kind} response retries the same request after reload`, async ({ page }) => {
    const api = await actionApi(page, kind)
    await page.goto('/')
    const panel = page.getByRole('region', { name: '训练焦点建议' })
    await panel.getByText('记录选择与执行反馈', { exact: true }).click()
    const label = kind === 'decisions' ? '选择' : '执行反馈'
    if (kind === 'executions')
      await panel.getByRole('textbox', { name: '实际行动或未知说明' }).fill('未知，稍后确认')
    await panel.getByRole('button', { name: `保存${label}`, exact: true }).click()
    await expect(panel.getByRole('button', { name: `重试保存${label}`, exact: true })).toBeVisible()
    await page.reload()
    await panel.getByText('记录选择与执行反馈', { exact: true }).click()
    await panel.getByRole('button', { name: `重试保存${label}`, exact: true }).click()
    await expect.poll(() => api.requests.length).toBe(2)
    expect(api.requests[0].body).toEqual(api.requests[1].body)
    expect(api.activity.decisions.length + api.activity.executions.length).toBe(1)
    await expect(panel.getByRole('button', { name: `保存${label}`, exact: true })).toBeVisible()
  })
}

test('E1.2 stale applicability disables adoption while allowing historical feedback', async ({
  page,
}) => {
  const api = await actionApi(page)
  api.setValidity('needs_reassessment')
  await page.goto('/')
  const panel = page.getByRole('region', { name: '训练焦点建议' })
  await panel.getByText('记录选择与执行反馈', { exact: true }).click()
  await expect(panel.getByRole('option', { name: '采纳', exact: true })).toHaveAttribute(
    'disabled',
    '',
  )
  await panel.getByRole('button', { name: '保存选择', exact: true }).click()
  await expect.poll(() => api.activity.decisions.length).toBe(1)
  expect(api.activity.decisions[0].status).toBe('DEFERRED')
  await panel.getByRole('combobox', { name: '执行情况', exact: true }).selectOption('CONFIRMED')
  await panel.getByRole('textbox', { name: '实际行动或未知说明' }).fill('虽未采纳但尝试了')
  await panel.getByRole('button', { name: '保存执行反馈', exact: true }).click()
  await expect(panel).toContainText('自报已执行（未知窗口）')
  expect(api.activity.executions[0].source_type).toBe('user_reported')
})

test('E1.2 a conflicting choice can be refreshed and submitted with a new request', async ({
  page,
}) => {
  const api = await actionApi(page)
  await page.goto('/')
  const panel = page.getByRole('region', { name: '训练焦点建议' })
  await panel.getByText('记录选择与执行反馈', { exact: true }).click()
  // Another view saves a choice after this page loaded its empty history.
  api.activity.current_decision_id = 'other-view-choice'
  await panel.getByRole('button', { name: '保存选择', exact: true }).click()
  await expect(panel).toContainText('选择已变化，请刷新')
  await panel.getByRole('button', { name: '刷新并重新填写选择' }).click()
  await panel.getByRole('button', { name: '保存选择', exact: true }).click()
  await expect(panel).toContainText('选择已保存')
  expect(api.requests).toHaveLength(2)
  expect(api.requests[0].body.request_id).not.toBe(api.requests[1].body.request_id)
  expect((api.requests[1].body as DecisionRequest).previous_decision_id).toBe('other-view-choice')
})
