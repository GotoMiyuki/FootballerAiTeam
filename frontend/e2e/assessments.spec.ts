import { test, expect, type Page } from '@playwright/test'
import type { Mission } from '../src/types/mission'
import type { Recommendation } from '../src/types/recommendation'
import type { ContinuationRequest } from '../src/api/missions'
import type {
  Assessment,
  AssessmentRequest,
  ComparisonView,
  ComparisonPlan,
  ComparisonRequest,
  ObservationReference,
} from '../src/types/assessment'

const context = { career_id: 'comparison-test', branch_id: 'main', player_id: 'p' }
const reference = {
  verification: 'VERIFIED' as const,
  context,
  state_version: 'baseline-v1',
  snapshot_id: 'snapshot-v1',
  source_types: ['game_observation'],
}
const mission: Mission = {
  id: 'comparison-mission',
  conversation_id: 'comparison-conv',
  title: '比较来源任务',
  objective: '比较来源任务',
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
  input_reference: reference,
  delivery_status: 'PUBLISHABLE',
  available_operations: ['view', 'reevaluate'],
}
const rec: Recommendation = {
  schema_version: 1,
  recommendation_id: 'rec-comparison',
  revision: 1,
  context,
  source: {
    mission_id: mission.id,
    subtask_id: 'training',
    capability: 'skill_training',
    result_version: 1,
    payload_position: '/focus_areas/0',
    payload_hash: 'payload',
    input_fingerprint: 'input',
    review_plan_version: 1,
    reviewed_result_version: 1,
    mapping_version: 'training-focus-v1',
  },
  content: {
    title: '训练焦点',
    text: '训练焦点',
    expected_goal: '训练方向',
    basis: '已审查成果',
    limitations: [],
  },
  applicability: {
    input_reference: reference,
    conditions: [],
    game_versions: ['test-v1'],
    game_mode: null,
    valid_window: null,
    window_reason: '未知',
  },
  execution_support: {
    status: 'pending_verification',
    reason: '尚未核验操作',
    evidence_references: [],
  },
  evaluation_spec: {
    status: 'undefined',
    baseline_reference: reference,
    metrics: [],
    observation_window: null,
    reason: '需明确比较规范',
  },
  validity: 'current',
  validity_reason: '原来源当前一致',
  checked_state_version: 'baseline-v1',
  created_at: '2026-10-24T10:00:00+08:00',
}
const observation = (key: string, value: number | null): ObservationReference => ({
  key,
  value,
  observation_id: key,
  provider: 'test',
  source_type: 'game_observation',
  source_record_id: `source-${key}`,
  game_version: 'test-v1',
  adapter_version: 'adapter-v1',
  semantic_hash: key,
  effective_at: key === 'baseline' ? '2026-09-02' : key === 'f1' ? '2026-09-04' : '2026-09-05',
  time_domain: 'game',
  time_precision: 'day',
})

async function comparisonApi(
  page: Page,
  loseResponse?: 'plan' | 'assessment' | 'adjustment',
  failChild = false,
) {
  const state: ComparisonView = { supported: true, reason: '', plans: [], assessments: [] }
  const requests: { kind: string; body: ComparisonRequest | AssessmentRequest }[] = []
  const saved = new Map<string, { record: ComparisonPlan | Assessment }>()
  let mode: 'pending' | 'complete' | 'incomparable' = 'pending'
  let lost = false
  const adjustments: ContinuationRequest[] = []
  let child: Mission | undefined
  let fixedFeedback: Record<string, unknown> | undefined
  await page.addInitScript(() => localStorage.setItem('fait.mission', 'comparison-mission'))
  await page.route(
    (url) => url.pathname.startsWith('/api/'),
    async (route) => {
      const path = new URL(route.request().url()).pathname
      if (path.endsWith('/events'))
        return route.fulfill({ contentType: 'text/event-stream', body: '' })
      if (path === '/api/mission-continuations') {
        const body = route.request().postDataJSON() as ContinuationRequest
        adjustments.push(body)
        const assessment = state.assessments.find(
          (item) =>
            item.assessment_id === body.assessment_id && item.revision === body.assessment_revision,
        )
        if (!assessment || assessment.validity !== 'current')
          return route.fulfill({ status: 409, json: { detail: '比较版本已失效，请刷新' } })
        if (!child) {
          child = {
            ...mission,
            id: 'adjusted-mission',
            title: body.content,
            objective: body.content,
            status: failChild ? 'FAILED' : 'COMPLETED',
            error: failChild ? '新任务失败（测试）' : null,
            input_reference: {
              ...reference,
              snapshot_id: 'snapshot-v2',
              state_version: 'state-v2',
            },
            lineage: {
              parent_mission_id: mission.id,
              operation: 'reevaluate',
              reason: body.reason,
              request_id: body.request_id,
              history_references: [
                {
                  kind: 'assessment',
                  mission_id: mission.id,
                  subtask_id: null,
                  object_id: assessment.assessment_id,
                  version: assessment.revision,
                  content_hash: 'assessment-hash',
                },
              ],
              feedback_reference: {
                content_hash: 'feedback-hash',
                recommendation_id: rec.recommendation_id,
                recommendation_revision: 1,
                assessment_id: assessment.assessment_id,
                assessment_revision: assessment.revision,
                execution_ids: [],
              },
            },
          }
          fixedFeedback = {
            content_hash: 'feedback-hash',
            parent_mission_id: mission.id,
            recommendation: rec,
            assessment: { ...assessment, latest_input_differs_from_assessment: false },
            decision: null,
            executions: [],
            limitations: ['观测变化不证明因果；新建议不继承原采纳'],
          }
        }
        if (loseResponse === 'adjustment' && !lost) {
          lost = true
          return route.abort('failed')
        }
        return route.fulfill({
          status: 202,
          json: {
            message_id: 'adjust-message',
            mission_id: child.id,
            created: adjustments.length === 1,
          },
        })
      }
      if (path.endsWith('/feedback-input')) return route.fulfill({ json: fixedFeedback || {} })
      if (path.endsWith('/followups'))
        return route.fulfill({
          json: {
            items: child
              ? [
                  {
                    mission_id: child.id,
                    status: child.status,
                    reason: child.lineage!.reason,
                    recommendation_ids: [],
                  },
                ]
              : [],
          },
        })
      if (child && path === `/api/missions/${child.id}`) return route.fulfill({ json: child })
      if (path.endsWith('/comparison-plans') || path.endsWith('/assessments')) {
        const kind = path.endsWith('/comparison-plans') ? 'plan' : 'assessment'
        const body = route.request().postDataJSON() as ComparisonRequest | AssessmentRequest
        requests.push({ kind, body })
        const duplicate = saved.get(body.request_id)
        if (duplicate) return route.fulfill({ json: { ...duplicate, created: false } })
        let record: ComparisonPlan | Assessment
        if (kind === 'plan') {
          record = {
            plan_id: 'plan-original',
            recommendation_id: rec.recommendation_id,
            recommendation_revision: 1,
            context,
            source: rec.source,
            baseline_reference: reference,
            anchor_at: '2026-09-03',
            specification: body as ComparisonRequest,
            metric_definition: {
              label: '比赛评分',
              method: 'mean',
              unit: 'canonical_rating_point',
              scale: '0..10',
            },
            baseline_observations: [observation('baseline', 0)],
            created_at: '2026-10-24T10:00:00+08:00',
            algorithm_version: 'observed-comparison-v1',
          }
          state.plans.push(record)
        } else {
          const revision = state.assessments.length + 1
          state.assessments.forEach((entry) => {
            entry.validity = 'superseded'
          })
          record = {
            assessment_id: 'assessment-original',
            revision,
            plan_id: 'plan-original',
            recommendation_id: rec.recommendation_id,
            created_at: '2026-10-24T10:00:00+08:00',
            corrects_revision: revision > 1 ? revision - 1 : null,
            signature: `sig-${revision}`,
            execution_requested_id: null,
            validity: 'current',
            validity_reason: '来源引用当前一致；不代表建议有效',
            result: {
              evidence_status:
                mode === 'complete'
                  ? 'COMPLETE'
                  : mode === 'incomparable'
                    ? 'INCOMPARABLE'
                    : 'INSUFFICIENT',
              goal_status: mode === 'complete' ? 'MET' : 'UNDETERMINED',
              before: mode === 'incomparable' ? null : 0,
              after: mode === 'complete' ? 6 : null,
              delta: mode === 'complete' ? 6 : null,
              baseline_samples: 1,
              followup_samples: mode === 'complete' ? 1 : 0,
              baseline_missing: 0,
              followup_missing: mode === 'complete' ? 1 : 0,
              window_matches: mode === 'pending' ? 0 : 2,
              window_complete: mode !== 'pending',
              baseline_observations: [observation('baseline', 0)],
              followup_observations:
                mode === 'complete' ? [observation('f1', null), observation('f2', 6)] : [],
              missing: mode === 'complete' ? ['缺值排除，明确零纳入'] : [],
              exclusions: {},
              method: 'mean',
              unit: 'canonical_rating_point',
              scale: '0..10',
              reason: mode === 'incomparable' ? '游戏版本不同，默认不可比' : '按冻结规范比较',
              execution: {
                requested_id: null,
                execution_id: null,
                revision: null,
                status: 'UNKNOWN',
                source_type: null,
              },
              limitations: ['观察变化不证明由建议造成', '游戏操作仍待验证'],
            },
          }
          state.assessments.push(record)
        }
        saved.set(body.request_id, { record })
        if (!lost && kind === loseResponse) {
          lost = true
          return route.abort('failed')
        }
        return route.fulfill({ json: { record, created: true } })
      }
      const data = path.endsWith('/comparison')
        ? state
        : path.endsWith('/recommendations')
          ? {
              mission_id: mission.id,
              availability: 'AVAILABLE',
              reason: '',
              items: path.includes('adjusted-mission') ? [] : [rec],
            }
          : path === `/api/missions/${mission.id}`
            ? mission
            : path.endsWith('/activity')
              ? {
                  recommendation_id: rec.recommendation_id,
                  recommendation_revision: 1,
                  context,
                  current_decision_id: null,
                  current_execution_ids: [],
                  decisions: [],
                  executions: [],
                }
              : path.endsWith('/career-memory')
                ? { context, items: [] }
                : path === '/api/health'
                  ? { status: 'ok', mode: 'live' }
                  : path === '/api/player'
                    ? { name: 'Test', attributes: {}, long_term_goals: [] }
                    : []
      return route.fulfill({ json: data })
    },
  )
  return {
    state,
    requests,
    adjustments,
    setMode: (next: typeof mode) => {
      mode = next
      state.assessments.forEach((entry) => {
        entry.validity = 'superseded'
        entry.validity_reason = '观察或更正已变化，原版本保留'
      })
    },
  }
}

async function createPlan(page: Page) {
  const panel = page.getByRole('region', { name: '后续观察与比较' })
  await panel.getByText('后续观察与比较', { exact: true }).click()
  await panel.getByText('确认新的比较规范', { exact: true }).click()
  await panel.getByRole('spinbutton', { name: '后续场数' }).fill('2')
  await panel.getByRole('spinbutton', { name: '后续值目标', exact: true }).fill('5')
  await panel.getByRole('button', { name: '确认并冻结比较规范' }).click()
  return panel
}

test('E2.1 pending, observed comparison, correction history and unknown execution', async ({
  page,
}) => {
  const api = await comparisonApi(page)
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  await page.goto('/')
  const panel = await createPlan(page)
  await panel.getByRole('button', { name: '生成/刷新比较', exact: true }).click()
  await expect(panel).toContainText('待观察 · 证据不足')
  await expect(panel).toContainText('目标未判定')
  api.setMode('complete')
  await panel.getByRole('button', { name: '刷新观察状态' }).click()
  await expect(panel).toContainText('历史版本需更新')
  await panel.getByRole('button', { name: '生成/刷新比较', exact: true }).click()
  await expect(panel).toContainText('观测目标达到')
  await expect(panel).toContainText('执行关联：执行未知')
  await expect(panel.getByRole('cell', { name: '6', exact: true })).toHaveCount(2)
  await expect(panel).toContainText('观察变化不证明由建议造成')
  await expect(panel).toContainText('更正原版本 v1')
  await panel.getByText('查看观察来源', { exact: true }).last().click()
  await expect(panel).toContainText('source-f2')
  await panel.screenshot({ path: 'test-results/e2-1-observed-comparison.png' })
  await page.reload()
  await panel.getByText('后续观察与比较', { exact: true }).click()
  await expect(panel).toContainText('比较 v2')
  expect(api.state.assessments).toHaveLength(2)
  expect(errors).toEqual([])
})

for (const kind of ['plan', 'assessment'] as const) {
  test(`E2.1 lost ${kind} response reuses the exact request after reload`, async ({ page }) => {
    const api = await comparisonApi(page, kind)
    await page.goto('/')
    const panel = await createPlan(page)
    if (kind === 'assessment')
      await panel.getByRole('button', { name: '生成/刷新比较', exact: true }).click()
    await expect(panel.getByRole('alert')).toBeVisible()
    await page.reload()
    await panel.getByText('后续观察与比较', { exact: true }).click()
    if (kind === 'plan') {
      await panel.getByText('确认新的比较规范', { exact: true }).click()
      await panel.getByRole('button', { name: '确认并冻结比较规范' }).click()
    } else await panel.getByRole('button', { name: '重试生成/刷新比较', exact: true }).click()
    await expect.poll(() => api.requests.filter((entry) => entry.kind === kind).length).toBe(2)
    const retries = api.requests.filter((entry) => entry.kind === kind)
    expect(retries[0].body).toEqual(retries[1].body)
    expect(kind === 'plan' ? api.state.plans.length : api.state.assessments.length).toBe(1)
  })
}

test('E2.1 incompatible evidence never displays a target failure or numeric gain', async ({
  page,
}) => {
  const api = await comparisonApi(page)
  api.setMode('incomparable')
  await page.goto('/')
  const panel = await createPlan(page)
  await panel.getByRole('button', { name: '生成/刷新比较', exact: true }).click()
  await expect(panel).toContainText('游戏版本不同，默认不可比')
  await expect(panel).toContainText('目标未判定')
  await expect(panel.getByRole('cell', { name: '未知', exact: true })).toHaveCount(3)
  await expect(panel).not.toContainText('观测目标未达到')
})

for (const failChild of [false, true]) {
  test(`E2.2 fixed feedback, reload and identical retry preserve parent; failed child=${failChild}`, async ({
    page,
  }) => {
    const api = await comparisonApi(page, 'adjustment', failChild)
    api.setMode('complete')
    await page.goto('/')
    const panel = await createPlan(page)
    await panel.getByRole('button', { name: '生成/刷新比较', exact: true }).click()
    await panel.getByRole('button', { name: '根据结果调整方案', exact: true }).click()
    await page.getByLabel('结果调整原因', { exact: true }).fill('比赛评分变化')
    await page.getByLabel('结果调整要求', { exact: true }).fill('按照新评分调整训练，每天20分钟')
    await page.getByRole('button', { name: '创建调整任务', exact: true }).click()
    await expect(page.getByRole('button', { name: '原样重试调整', exact: true })).toBeVisible()
    await page.reload()
    await page
      .getByRole('region', { name: '后续观察与比较' })
      .getByText('后续观察与比较', { exact: true })
      .click()
    await page.getByRole('button', { name: '根据结果调整方案', exact: true }).click()
    await expect(page.getByLabel('结果调整要求', { exact: true })).toHaveValue(
      '按照新评分调整训练，每天20分钟',
    )
    await page.getByRole('button', { name: '创建调整任务', exact: true }).click()
    await expect(page.getByRole('button', { name: '查看来源任务', exact: true })).toBeVisible()
    expect(api.adjustments).toHaveLength(2)
    expect(api.adjustments[0]).toEqual(api.adjustments[1])
    expect(api.adjustments[0]).toMatchObject({
      recommendation_id: rec.recommendation_id,
      recommendation_revision: 1,
      assessment_id: 'assessment-original',
      assessment_revision: 1,
      execution_ids: [],
      include_report: false,
    })
    const feedback = page.getByLabel('本次固定反馈来源', { exact: true })
    await feedback.locator('summary').first().click()
    await expect(feedback).toContainText('观测前值 0 / 后值 6 / 变化 6')
    await expect(feedback).toContainText('执行未知')
    if (failChild) await expect(page.getByRole('alert')).toContainText('新任务失败')
    await page.getByRole('button', { name: '查看来源任务', exact: true }).click()
    await expect(page.getByRole('heading', { name: mission.title, exact: true })).toBeVisible()
    await page.getByText('查看结果调整任务', { exact: true }).click()
    await expect(page.getByRole('button', { name: '查看调整任务', exact: true })).toBeVisible()
  })
}

test('E2.2 stale assessment cannot silently launch an adjustment', async ({ page }) => {
  const api = await comparisonApi(page)
  await page.goto('/')
  const panel = await createPlan(page)
  await panel.getByRole('button', { name: '生成/刷新比较', exact: true }).click()
  api.setMode('complete')
  await panel.getByRole('button', { name: '刷新观察状态', exact: true }).click()
  await expect(panel.getByRole('button', { name: '根据结果调整方案', exact: true })).toBeDisabled()
  expect(api.adjustments).toEqual([])
})

test('M3 finishing comparison uses one attribute baseline and explicit game dates', async ({
  page,
}) => {
  const api = await comparisonApi(page)
  await page.goto('/')
  const panel = page.getByRole('region', { name: '后续观察与比较' })
  await panel.getByText('后续观察与比较', { exact: true }).click()
  await panel.getByText('确认新的比较规范', { exact: true }).click()
  await panel.getByLabel('比较指标', { exact: true }).selectOption('finishing')
  await expect(panel.getByRole('spinbutton', { name: '基线场数' })).toHaveCount(0)
  await expect(panel.getByRole('spinbutton', { name: '后续场数' })).toHaveCount(0)
  await panel.getByLabel('游戏开始日期', { exact: true }).fill('2026-09-04')
  await panel.getByLabel('游戏结束日期', { exact: true }).fill('2026-09-05')
  await panel.getByRole('button', { name: '确认并冻结比较规范' }).click()
  await expect.poll(() => api.requests.length).toBe(1)
  expect(api.requests[0].body).toMatchObject({
    metric: 'finishing',
    baseline_count: 1,
    minimum_samples: 1,
    window: { kind: 'game_dates', start: '2026-09-04', end: '2026-09-05' },
  })
})
