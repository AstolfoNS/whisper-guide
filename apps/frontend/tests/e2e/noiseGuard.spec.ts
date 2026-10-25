import { useSyntheticMicrophone } from './syntheticMicrophone'
import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const screenshots = 'test-results/screenshots/noise-guard'

test('a silent hallucinated 点 cannot invalidate the matched phrase or stop the next automatic prompt', async ({ page }) => {
  await useSyntheticMicrophone(page)
  const created = await page.request.post('/api/settings/model-configs', { data: { name: '无声单字防误停验证', settings: {
    stt_provider: 'cloud', cloud_opt_in: true, cloud_stt_protocol: 'volcengine-ws', cloud_stt_auth_mode: 'api-key',
    cloud_stt_resource_id: 'volc.seedasr.sauc.duration', cloud_stt_endpoint: 'ws://127.0.0.1:8010/mock/asr/noise/bigmodel_async',
    cloud_stt_access_token: 'synthetic-browser-asr-key', llm_align_mode: 'off',
  } } })
  expect(created.status()).toBe(201)
  const profile = await created.json()
  let receivedAudio = 0
  let invalidatedAfterMatch = false
  let hasMatch = false
  page.on('websocket', (socket) => socket.on('framereceived', ({ payload }) => {
    const message = JSON.parse(String(payload))
    if (message.type === 'audio') receivedAudio++
    if (message.type === 'snapshot') {
      if (hasMatch && !message.session.reliable) invalidatedAfterMatch = true
      if (message.session.aligned_transcript === '我会坚持自律，好好') hasMatch = true
    }
  }))
  expect((await page.request.post(`/api/settings/model-configs/${profile.id}/activate`)).ok()).toBe(true)
  expect((await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: true } })).ok()).toBe(true)
  try {
    await page.goto('/studio')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name: '无声单字防误停.txt', mimeType: 'text/plain', buffer: Buffer.from('我会坚持自律，好好吃饭，踏实学习。') })
    await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('1500')
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    await expect(page.getByRole('region', { name: '讲稿匹配', exact: true })).toContainText('我会坚持自律，好好')
    const raw = page.getByRole('region', { name: '实时语音识别', exact: true })
    await expect(raw).toContainText('点')
    await expect(raw).toContainText('已过滤')
    await expect(raw).toContainText('静音等待继续')
    await expect(page.getByRole('alert').filter({ hasText: '自动提示' })).toHaveCount(0)
    await expect(page.locator('.reading-cursor')).toHaveText('好')
    await mkdir(screenshots, { recursive: true })
    await page.screenshot({ path: `${screenshots}/filtered-point.png`, fullPage: true, animations: 'disabled' })
    await expect(page.getByRole('button', { name: '打断播报', exact: true })).toBeVisible({ timeout: 8000 })
    await expect.poll(() => receivedAudio).toBeGreaterThan(0)
    expect(invalidatedAfterMatch).toBe(false)
    await page.screenshot({ path: `${screenshots}/automatic-prompt.png`, fullPage: true, animations: 'disabled' })
    console.log('matched phrase preserved after silent final 点; no AC-14 warning; automatic prompt and in-memory audio received')
  } finally {
    const end = page.getByRole('button', { name: '结束', exact: true })
    if (await end.isVisible() && await end.isEnabled()) await end.click()
    await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: false } })
    await page.request.post('/api/settings/model-configs/local-default/activate')
    await page.request.delete(`/api/settings/model-configs/${profile.id}`)
  }
})
