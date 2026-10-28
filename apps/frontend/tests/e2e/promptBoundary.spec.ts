import { confirmSessionEnd } from './sessionActions'
import { test, expect, type Page } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { useSyntheticMicrophone } from './syntheticMicrophone'

const screenshots = 'test-results/screenshots/prompt-boundary'
test.beforeEach(async ({ page }) => { await useSyntheticMicrophone(page) })

async function activateScenario(page: Page, scenario: 'boundary' | 'complete') {
  const response = await page.request.post('/api/settings/model-configs', { data: { name: `提示边界验证-${scenario}`, settings: {
    stt_provider: 'cloud', cloud_opt_in: true, cloud_stt_protocol: 'volcengine-ws', cloud_stt_auth_mode: 'api-key',
    cloud_stt_resource_id: 'volc.seedasr.sauc.duration', cloud_stt_endpoint: `ws://127.0.0.1:8010/mock/asr/${scenario}/bigmodel_async`,
    cloud_stt_access_token: 'synthetic-browser-asr-key', llm_align_mode: 'off',
  } } })
  expect(response.status()).toBe(201)
  const profile = await response.json()
  expect((await page.request.post(`/api/settings/model-configs/${profile.id}/activate`)).ok()).toBe(true)
  return profile.id as string
}
async function cleanup(page: Page, id: string) {
  const end = page.getByRole('button', { name: '结束', exact: true })
  if (await end.isVisible() && await end.isEnabled()) { await end.click(); await confirmSessionEnd(page) }
  expect((await page.request.post('/api/settings/model-configs/local-default/activate')).ok()).toBe(true)
  expect((await page.request.delete(`/api/settings/model-configs/${id}`)).ok()).toBe(true)
}

test('finishing a paragraph prompts the next paragraph instead of repeating the last character', async ({ page }) => {
  const id = await activateScenario(page, 'boundary')
  let promptText = ''
  page.on('websocket', socket => socket.on('framereceived', ({ payload }) => {
    const message = JSON.parse(String(payload))
    if (message.type === 'snapshot' && message.session.state === 'PROMPTING') promptText = message.session.prompt.text
  }))
  try {
    await page.goto('/studio')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name: '段末续播.txt', mimeType: 'text/plain', buffer: Buffer.from(['大家好，我宣布，从今天开始，我要好好调整自己，认真奔赴目标。', '我会坚持自律，好好吃饭，踏实学习。'].join(String.fromCharCode(10, 10))) })
    await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('1000')
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    await expect(page.locator('.reading-cursor')).toHaveText('标')
    await expect(page.locator('.prompt-cursor')).toHaveText('我')
    await expect(page.getByRole('button', { name: '打断播报', exact: true })).toBeVisible()
    expect(promptText).toBe('我会坚持自律，')
    await mkdir(screenshots, { recursive: true })
    await page.screenshot({ path: `${screenshots}/next-paragraph.png`, fullPage: true, animations: 'disabled' })
    console.log('paragraph completed: reading cursor remains 标, next prompt starts 我会坚持自律，')
  } finally { await cleanup(page, id) }
})

test('finishing the entire script keeps listening without speaking the last character again', async ({ page }) => {
  const id = await activateScenario(page, 'complete')
  let prompts = 0
  let audio = 0
  page.on('websocket', socket => socket.on('framereceived', ({ payload }) => {
    const message = JSON.parse(String(payload))
    if (message.type === 'snapshot' && message.session.state === 'PROMPTING') prompts++
    if (message.type === 'audio') audio++
  }))
  try {
    await page.goto('/studio')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name: '全文完成.txt', mimeType: 'text/plain', buffer: Buffer.from('大家好。谢谢大家。') })
    await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('1000')
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    await expect(page.locator('.reading-cursor')).toHaveText('家')
    await expect(page.locator('.prompt-cursor')).toHaveCount(0)
    await expect(page.getByText(/已到讲稿末尾/)).toBeVisible()
    await page.waitForTimeout(1400)
    await expect(page.getByRole('button', { name: '暂停演讲', exact: true })).toBeVisible()
    expect(prompts).toBe(0)
    expect(audio).toBe(0)
    await mkdir(screenshots, { recursive: true })
    await page.screenshot({ path: `${screenshots}/script-complete.png`, fullPage: true, animations: 'disabled' })
    console.log('script completed: final reading cursor kept, no next prompt or audio after silence deadline')
  } finally { await cleanup(page, id) }
})
