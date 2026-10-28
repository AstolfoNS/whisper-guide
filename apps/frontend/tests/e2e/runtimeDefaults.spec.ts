import { confirmSessionEnd } from './sessionActions'
import { test, expect } from '@playwright/test'

test('uses the actual server UI defaults and leaves untouched session options to the backend', async ({ page }) => {
  await page.request.post('/api/settings/model-configs/local-default/activate')
  const original = await (await page.request.get('/api/app-config')).json()
  const override = await page.request.post('http://127.0.0.1:8010/mock/ui-defaults', { data: {
    default_silence_timeout_ms: 1500, script_max_file_bytes: 4194304, script_max_upload_files: 15,
  } })
  expect(override.ok()).toBe(true)
  const prepare: { silence_timeout_ms?: number }[] = []
  page.on('websocket', socket => socket.on('framesent', ({ payload }) => {
    if (typeof payload !== 'string') return
    try { const command = JSON.parse(payload); if (command.type === 'prepare') prepare.push(command) } catch { /* PCM frame. */ }
  }))
  try {
    await page.goto('/studio')
    await expect(page.getByRole('textbox', { name: '静音等待', exact: true })).toHaveValue('1500')
    await expect(page.locator('.upload-label')).toContainText('4 MiB')
    await expect(page.locator('.upload-label')).toContainText('15 份')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name: '环境默认验证.txt', mimeType: 'text/plain', buffer: Buffer.from('大家好。环境默认配置。') })
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    await expect.poll(() => prepare.length).toBe(1)
    expect(prepare[0]).not.toHaveProperty('silence_timeout_ms')
    await expect(page.getByRole('button', { name: '暂停演讲', exact: true })).toBeEnabled()
    await expect(page.getByRole('textbox', { name: '静音等待', exact: true })).toHaveValue('1500')
  } finally {
    const end = page.getByRole('button', { name: '结束', exact: true })
    if (await end.isVisible() && await end.isEnabled()) { await end.click(); await confirmSessionEnd(page) }
    await page.request.post('http://127.0.0.1:8010/mock/ui-defaults', { data: {
      default_silence_timeout_ms: original.default_silence_timeout_ms,
      script_max_file_bytes: original.script_max_file_bytes, script_max_upload_files: original.script_max_upload_files,
    } })
  }
})
