import { test, expect } from '@playwright/test'

test('C2.2 demo API fixes selected revision, previews current memory and can opt out', async ({
  page,
}) => {
  const title = `速度训练 C2.2 ${Date.now()}`
  const oldContent = `C22_OLD_${Date.now()} 每天速度训练20分钟`
  const newContent = `C22_NEW_${Date.now()} 每天速度训练40分钟`
  await page.goto('/')
  await page.getByLabel('任务需求', { exact: true }).fill(title)
  await page.getByRole('button', { name: '创建任务', exact: true }).click()
  await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
  const parent = await page.evaluate(() => localStorage.getItem('fait.mission'))
  const mission = await (await page.request.get(`/api/missions/${parent}`)).json()
  const endpoint = `/api/missions/${parent}/career-memory`
  const body = {
    context: mission.input_reference.context,
    request_id: `memory-${Date.now()}`,
    confirmed: true,
    operation: 'create',
    kind: 'preference',
    content: oldContent,
  }
  const saved = await page.request.post(endpoint + '/user-records', { data: body })
  expect(saved.ok()).toBeTruthy()
  const memory = (await saved.json()).record
  await page.getByRole('button', { name: '根据新情况重新评估', exact: true }).click()
  await page.getByLabel('发起原因', { exact: true }).fill('新的训练安排')
  await page.getByLabel('新的评估要求', { exact: true }).fill('速度训练如何安排')
  await page.getByRole('button', { name: '预览相关记忆', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText(oldContent)
  await page.getByRole('button', { name: '创建关联任务', exact: true }).click()
  await expect
    .poll(() => page.evaluate(() => localStorage.getItem('fait.mission')))
    .not.toBe(parent)
  await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
  const child = await page.evaluate(() => localStorage.getItem('fait.mission'))
  expect(child).not.toBe(parent)
  const panel = page.getByLabel('本次固定记忆输入', { exact: true })
  await panel.locator('summary').first().click()
  await expect(panel).toContainText(oldContent)
  const fixed = await (await page.request.get(`/api/missions/${child}/memory-input`)).json()
  const corrected = await page.request.post(endpoint + '/user-records', {
    data: {
      ...body,
      request_id: `correct-${Date.now()}`,
      operation: 'correct',
      memory_id: memory.memory_id,
      expected_revision: 1,
      content: newContent,
      reason: '时间改变',
    },
  })
  expect(corrected.ok()).toBeTruthy()
  await page.reload()
  await panel.locator('summary').first().click()
  await expect(panel).toContainText(oldContent)
  await expect(panel).not.toContainText(newContent)
  expect(await (await page.request.get(`/api/missions/${child}/memory-input`)).json()).toEqual(
    fixed,
  )
  await page.getByRole('button', { name: '根据新情况重新评估', exact: true }).click()
  await page.getByLabel('发起原因', { exact: true }).fill('不引用记忆')
  await page.getByLabel('新的评估要求', { exact: true }).fill('速度训练独立分析')
  await page.getByRole('button', { name: '预览相关记忆', exact: true }).click()
  await expect(page.getByRole('dialog')).toContainText(newContent)
  await page
    .getByRole('checkbox', { name: '引用相关生涯记忆（提交时固定）', exact: true })
    .uncheck()
  await expect(page.getByRole('button', { name: '预览相关记忆', exact: true })).toHaveCount(0)
  await page.getByRole('button', { name: '创建关联任务', exact: true }).click()
  await expect.poll(() => page.evaluate(() => localStorage.getItem('fait.mission'))).not.toBe(child)
  await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
  const disabled = await page.evaluate(() => localStorage.getItem('fait.mission'))
  const input = await (await page.request.get(`/api/missions/${disabled}/memory-input`)).json()
  expect(input.enabled).toBe(false)
  expect(input.items).toEqual([])
})
