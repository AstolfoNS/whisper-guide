import { useSyntheticMicrophone } from './syntheticMicrophone'
import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const screenshots = '../docs/screenshots/cloud-silence-2026-10-24'

test.beforeEach(async ({ page }) => { await useSyntheticMicrophone(page) })

test('cloud revisions recover from an unreliable partial and automatically prompt after silence', async ({ page }) => {
  const created = await page.request.post('/api/settings/model-configs', { data: { name: '云端静音修订验证', settings: {
    stt_provider: 'cloud', cloud_opt_in: true, cloud_stt_protocol: 'volcengine-ws', cloud_stt_auth_mode: 'api-key',
    cloud_stt_resource_id: 'volc.seedasr.sauc.duration', cloud_stt_endpoint: 'ws://127.0.0.1:8010/mock/asr/revising/bigmodel_async',
    cloud_stt_access_token: 'synthetic-browser-asr-key', llm_align_mode: 'off',
  } } })
  expect(created.status()).toBe(201)
  const profile = await created.json()
  let audio = 0
  const states: { state: string; reliable: boolean; reading: number | null }[] = []
  page.on('websocket', (socket) => socket.on('framereceived', ({ payload }) => {
    const message = JSON.parse(String(payload))
    if (message.type === 'audio') audio++
    if (message.type === 'snapshot') states.push({ state: message.session.state, reliable: message.session.reliable, reading: message.session.reading_position })
  }))
  expect((await page.request.post(`/api/settings/model-configs/${profile.id}/activate`)).ok()).toBe(true)
  expect((await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: true } })).ok()).toBe(true)
  try {
    await page.goto('/studio')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name: '云端静音修订.txt', mimeType: 'text/plain', buffer: Buffer.from('大家好，今天测试云端静音播报。') })
    await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('1000')
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    await expect(page.getByRole('region', { name: '实时语音识别', exact: true })).toContainText('完全不相干的文字')
    await expect(page.getByRole('alert').filter({ hasText: '自动提示' })).toBeVisible()
    await mkdir(screenshots, { recursive: true })
    await page.screenshot({ path: `${screenshots}/alignment-warning.png`, fullPage: true, animations: 'disabled' })
    await expect(page.getByRole('button', { name: '打断播报', exact: true })).toBeVisible({ timeout: 12000 })
    await expect(page.locator('.reading-cursor')).toHaveText('好')
    await expect(page.getByRole('region', { name: '讲稿匹配', exact: true })).toContainText('大家好')
    await expect(page.getByRole('alert').filter({ hasText: '自动提示' })).toHaveCount(0)
    await expect.poll(() => audio).toBeGreaterThan(0)
    expect(states.some((state) => state.state === 'LISTENING' && !state.reliable && state.reading === null)).toBe(true)
    expect(states.some((state) => state.state === 'PROMPTING' && state.reliable && state.reading === 2)).toBe(true)
    await page.getByText('状态机与流转诊断', { exact: true }).click()
    await page.screenshot({ path: `${screenshots}/recovered-prompt.png`, fullPage: true, animations: 'disabled' })
    console.log('cloud partial revision: unreliable position held, corrected same-segment partial aligned to 大家好, automatic prompt and in-memory audio received')
  } finally {
    const end = page.getByRole('button', { name: '结束', exact: true })
    if (await end.isVisible() && await end.isEnabled()) await end.click()
    await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: false } })
    await page.request.post('/api/settings/model-configs/local-default/activate')
    await page.request.delete(`/api/settings/model-configs/${profile.id}`)
  }
})
