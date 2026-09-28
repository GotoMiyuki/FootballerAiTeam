import { test, expect } from '@playwright/test'

for (const scenario of ['pass', 'revision', 'replan', 'blocked']) {
  test(`${scenario}: complete, report, refresh, history, followup`, async ({ page }) => {
    await page.goto('/')
    await expect(page.getByText('当前为演示模式', { exact: false })).toBeVisible()
    const title = `验收 ${scenario} ${Date.now()}`
    await page.getByLabel('任务需求', { exact: true }).fill(title)
    await page.getByLabel('演示场景', { exact: true }).selectOption(scenario)
    await page.getByRole('button', { name: '创建任务', exact: true }).click()
    if (scenario === 'blocked') {
      await expect(page.getByRole('heading', { name: '补充信息，继续任务' })).toBeVisible()
      await page.reload()
      await page.getByLabel('昨晚睡眠（小时）', { exact: true }).fill('7.5')
      await page.getByLabel('是否疼痛', { exact: true }).selectOption('false')
      await page.getByLabel('今天可用时间', { exact: true }).selectOption('30 分钟')
      await page.getByRole('button', { name: '提交并继续', exact: true }).click()
    }
    await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
    if (scenario === 'revision')
      await expect(page.getByText('修订 1 次', { exact: true })).toBeVisible()
    if (scenario === 'replan')
      await expect(page.getByText('PLAN v2', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: '查看报告', exact: true }).click()
    await expect(page.getByRole('dialog').getByText('演示报告：', { exact: false })).toBeVisible()
    const download = page.waitForEvent('download')
    await page.getByRole('link', { name: '导出 .md', exact: true }).click()
    expect((await download).suggestedFilename()).toContain('.md')
    await page.getByRole('button', { name: '关闭', exact: true }).click()
    await page.reload()
    await expect(page.getByRole('button', { name: '查看报告', exact: true })).toBeVisible()
    await page.getByLabel('追问或补充信息', { exact: true }).fill('为什么这样安排？')
    await page.getByRole('button', { name: '发送', exact: true }).click()
    await expect(page.getByText('这是演示模式的追问回复。', { exact: false })).toBeVisible()
    await page.getByRole('button', { name: '任务历史', exact: true }).click()
    await page.getByLabel('搜索任务', { exact: true }).fill(title)
    await expect(page.locator('.history-item')).toHaveCount(1)
    await page.locator('.history-item').click()
    await expect(page.getByRole('heading', { name: title, exact: true }).first()).toBeVisible()
  })
}
