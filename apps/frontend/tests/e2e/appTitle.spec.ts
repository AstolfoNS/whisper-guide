import { test, expect } from '@playwright/test'

test('uses the real public default title for navigation, accessible branding and routed browser titles', async ({ page }) => {
  const config = await page.request.get('/api/app-config')
  expect(config.ok()).toBe(true)
  expect(await config.json()).toEqual({ app_title: 'whisper-guide', default_silence_timeout_ms: 3000, script_max_file_bytes: 2097152, script_max_upload_files: 10 })
  await page.goto('/')
  await expect(page.locator('.app-brand')).toHaveText('whisper-guide')
  await expect(page.getByRole('link', { name: 'whisper-guide 概览', exact: true })).toBeVisible()
  await expect(page).toHaveTitle('运行概览 · whisper-guide')
  await page.getByRole('link', { name: '工作台', exact: true }).click()
  await expect(page).toHaveTitle('演讲工作台 · whisper-guide')
  await page.getByRole('link', { name: '模型配置', exact: true }).click()
  await expect(page).toHaveTitle('模型配置 · whisper-guide')
})

test('applies a late runtime title to the current route once per page and reloads it after refresh', async ({ page }) => {
  const title = '自定义语音提示'
  let requests = 0
  let releaseFirst!: () => void
  const navigated = new Promise<void>(resolve => { releaseFirst = resolve })
  await page.route('**/api/app-config', async route => {
    const number = ++requests
    await Promise.all([
      new Promise(resolve => setTimeout(resolve, 200)),
      number === 1 ? navigated : Promise.resolve(),
    ])
    await route.fulfill({ json: { app_title: title } })
  })
  await page.goto('/')
  await expect(page.locator('.app-brand')).toHaveText('whisper-guide')
  await page.getByRole('link', { name: '工作台', exact: true }).click()
  await expect(page).toHaveURL(/\/studio$/)
  await expect(page).toHaveTitle('演讲工作台 · whisper-guide')
  releaseFirst()
  await expect(page.locator('.app-brand')).toHaveText(title)
  await expect(page.getByRole('link', { name: `${title} 概览`, exact: true })).toBeVisible()
  await expect(page).toHaveTitle(`演讲工作台 · ${title}`)
  await page.getByRole('link', { name: '模型配置', exact: true }).click()
  await expect(page).toHaveTitle(`模型配置 · ${title}`)
  await page.getByRole('link', { name: `${title} 概览`, exact: true }).click()
  await expect(page).toHaveTitle(`运行概览 · ${title}`)
  expect(requests).toBe(1)
  await page.reload()
  await expect(page.locator('.app-brand')).toHaveText(title)
  await expect(page.locator('.app-brand')).toHaveAttribute('aria-label', `${title} 概览`)
  await expect(page).toHaveTitle(`运行概览 · ${title}`)
  expect(requests).toBe(2)
})
