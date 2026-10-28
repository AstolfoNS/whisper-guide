import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { useSyntheticMicrophone } from './syntheticMicrophone'

test('phone workspace fits narrow viewports and provides reachable library, settings and diagnostics', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.route('**/api/app-config', route => route.fulfill({ json: {
    app_title: '用于验证手机窄屏下长标题不会挤压操作按钮的语音提示工作台',
    default_silence_timeout_ms: 3000, default_prompt_mode: 'silence',
    default_progress_timeout_ms: 4000, default_continuous_prompt: false,
    script_max_file_bytes: 2097152, script_max_upload_files: 10,
  } }))
  expect((await page.request.post('/api/scripts/upload', { multipart: { files: { name: '手机长稿布局.txt', mimeType: 'text/plain', buffer: Buffer.from('手机端长稿正文验证。'.repeat(160) + '最后一行必须可见。') } } })).ok()).toBe(true)
  for (const width of [320, 360, 390, 430]) {
    await page.setViewportSize({ width, height: 844 })
    await page.goto('/studio')
    await expect(page.getByRole('heading', { name: '演讲工作台', exact: true })).toBeVisible()
    const overflow = () => page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth)
    await expect.poll(overflow).toBe(false)
    const menu = page.getByRole('button', { name: '打开导航菜单', exact: true })
    const bounds = await menu.boundingBox()
    expect(bounds!.width).toBeGreaterThanOrEqual(44)
    expect(bounds!.height).toBeGreaterThanOrEqual(44)
    await page.getByRole('button', { name: '讲稿库', exact: true }).click()
    const library = page.getByRole('dialog')
    await expect(library).toBeVisible()
    expect((await library.boundingBox())!.width).toBeLessThanOrEqual(width + 0.5)
    await library.getByRole('radio', { name: /手机长稿布局/ }).first().check()
    await page.keyboard.press('Escape')
    await expect(library).toBeHidden()
    await expect(page.getByRole('button', { name: '讲稿库', exact: true })).toBeFocused()
    const lastLineVisible = await page.locator('.prompter-body').evaluate(element => {
      element.scrollTop = element.scrollHeight
      const text = element.querySelector('p:last-of-type > span:last-of-type')
      if (!text) throw new Error('Expected rendered last text fragment')
      const range = document.createRange(); range.selectNodeContents(text)
      const rects = Array.from(range.getClientRects())
      const line = rects.at(-1)!
      const body = element.getBoundingClientRect()
      const controls = document.querySelector('.session-controls')!.getBoundingClientRect()
      const lowerEdge = controls.top < body.bottom && controls.bottom > body.top ? controls.top : body.bottom
      return { line: { top: line.top, bottom: line.bottom }, body: { top: body.top, bottom: body.bottom }, lowerEdge, scrollHeight: element.scrollHeight, scrollTop: element.scrollTop, visible: line.top >= body.top && line.bottom <= lowerEdge + 1 }
    })
    expect(lastLineVisible.visible, JSON.stringify(lastLineVisible)).toBe(true)
    await page.getByRole('button', { name: '更多演讲操作', exact: true }).click()
    await expect(page.getByRole('dialog')).toBeVisible()
    await expect(page.getByRole('textbox', { name: '静音等待', exact: true })).toBeVisible()
    expect((await page.getByRole('dialog').boundingBox())!.width).toBeLessThanOrEqual(width + 0.5)
    await expect.poll(overflow).toBe(false)
    await page.keyboard.press('Escape')
    await menu.click()
    await page.getByRole('button', { name: '打开服务诊断', exact: true }).click()
    await expect(page.locator('.health-details')).toBeVisible()
    const diagnostics = page.getByRole('dialog').filter({ has: page.locator('.health-details') })
    expect((await diagnostics.boundingBox())!.width).toBeLessThanOrEqual(width + 0.5)
    await expect.poll(overflow).toBe(false)
    await page.keyboard.press('Escape')
    await page.keyboard.press('Escape')
  }
  await page.setViewportSize({ width: 700, height: 390 })
  await page.goto('/studio')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await expect(page.getByRole('button', { name: '更多演讲操作', exact: true })).toBeVisible()
  await mkdir('test-results/screenshots/mobile', { recursive: true })
  await page.screenshot({ path: 'test-results/screenshots/mobile/studio-landscape.png', fullPage: true, animations: 'disabled' })
  expect(errors).toEqual([])
})

test('phone model management uses list then editor and keeps saved draft semantics', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/settings/models')
  await expect(page.locator('.model-page__layout')).toBeVisible()
  await page.getByRole('button', { name: '新建配置', exact: true }).click()
  await expect(page.getByRole('textbox', { name: '配置名称', exact: true })).toBeVisible()
  await page.getByRole('textbox', { name: '配置名称', exact: true }).fill('手机配置布局回归')
  await page.getByRole('button', { name: '保存修改', exact: true }).click()
  await expect(page.getByText('配置已保存，当前生效方案未改变。')).toBeVisible()
  const back = page.getByRole('button', { name: '返回配置列表', exact: true })
  await back.click()
  await expect(page.getByRole('textbox', { name: '配置名称', exact: true })).toBeHidden()
  await expect(page.getByRole('button', { name: /手机配置布局回归/ })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
})

test('backgrounding pauses phone capture and requires an explicit resume', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await useSyntheticMicrophone(page)
  const commands: string[] = []
  page.on('websocket', socket => socket.on('framesent', ({ payload }) => {
    if (typeof payload === 'string') commands.push(JSON.parse(payload).type)
  }))
  await page.goto('/studio')
  await page.getByRole('button', { name: '讲稿库', exact: true }).click()
  await page.getByRole('dialog').getByLabel('上传讲稿', { exact: false }).setInputFiles({
    name: '手机后台暂停.txt', mimeType: 'text/plain', buffer: Buffer.from('大家好，手机后台暂停与恢复验证。'),
  })
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: '更多演讲操作', exact: true }).click()
  await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('30000')
  await page.getByRole('button', { name: '应用演讲设置', exact: true }).click()
  await page.getByRole('button', { name: '开始演讲', exact: true }).click()
  await expect(page.getByRole('button', { name: '暂停演讲', exact: true })).toBeVisible()
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'hidden' })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await expect(page.getByRole('button', { name: '继续演讲', exact: true })).toBeVisible()
  expect(commands).toContain('pause')
  const readyCount = commands.filter(command => command === 'audio_ready').length
  await page.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' })
    document.dispatchEvent(new Event('visibilitychange'))
  })
  await page.waitForTimeout(300)
  expect(commands.filter(command => command === 'audio_ready')).toHaveLength(readyCount)
  await page.getByRole('button', { name: '继续演讲', exact: true }).click()
  await expect.poll(() => commands.filter(command => command === 'audio_ready').length).toBe(readyCount + 1)
  await page.getByRole('button', { name: '更多演讲操作', exact: true }).click()
  await page.getByRole('button', { name: '结束演讲', exact: true }).click()
  await page.getByRole('button', { name: '确认结束', exact: true }).click()
  await expect(page.getByRole('button', { name: '开始新演讲', exact: true })).toBeVisible()
})
