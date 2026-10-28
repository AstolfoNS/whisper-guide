import { confirmSessionEnd } from './sessionActions'
import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const screenshots = 'test-results/screenshots/alignment-state'

/** Two synthetic acoustic bursts; microphone audio is replaced in memory. */
test('an accepted cloud partial survives a second VAD onset and settles before the automatic prompt', async ({ page }) => {
  await page.addInitScript(() => {
    let frameIndex = 0
    const original = WebSocket.prototype.send
    WebSocket.prototype.send = function (data) {
      if (typeof data === 'string' && JSON.parse(data).type === 'audio_ready') frameIndex = 0
      if (data instanceof ArrayBuffer) {
        const pcm = new Int16Array(data.byteLength / 2)
        if (frameIndex < 20 || (frameIndex >= 40 && frameIndex < 50)) {
          for (let index = 0; index < pcm.length; index++) pcm[index] = Math.round(1600 * Math.sin(2 * Math.PI * 220 * (frameIndex * pcm.length + index) / 16000))
        }
        frameIndex++
        return original.call(this, pcm.buffer)
      }
      return original.call(this, data)
    }
  })
  const created = await page.request.post('/api/settings/model-configs', { data: { name: '待确认分段结算验证', settings: {
    stt_provider: 'cloud', cloud_opt_in: true, cloud_stt_protocol: 'volcengine-ws', cloud_stt_auth_mode: 'api-key',
    cloud_stt_resource_id: 'volc.seedasr.sauc.duration', cloud_stt_endpoint: 'ws://127.0.0.1:8010/mock/asr/pending/bigmodel_async',
    cloud_stt_access_token: 'synthetic-browser-asr-key', llm_align_mode: 'off',
  } } })
  expect(created.status()).toBe(201)
  const profile = await created.json()
  let receivedAudio = 0
  const snapshots: { state: string; speaking: boolean; current: boolean; final: boolean; raw: string; aligned: string; reading: number | null; alignment: string; feedback: number }[] = []
  page.on('websocket', (socket) => socket.on('framereceived', ({ payload }) => {
    const message = JSON.parse(String(payload))
    if (message.type === 'audio') receivedAudio++
    if (message.type === 'snapshot') {
      const snapshot = message.session
      snapshots.push({ state: snapshot.state, speaking: snapshot.speaking, current: snapshot.raw_transcript_is_current, final: snapshot.raw_transcript_is_final, raw: snapshot.raw_transcript, aligned: snapshot.aligned_transcript, reading: snapshot.reading_position, alignment: snapshot.alignment_status, feedback: snapshot.feedback_id })
    }
  }))
  expect((await page.request.post(`/api/settings/model-configs/${profile.id}/activate`)).ok()).toBe(true)
  expect((await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: true } })).ok()).toBe(true)
  try {
    await page.goto('/studio')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name: '待确认分段结算.txt', mimeType: 'text/plain', buffer: Buffer.from('大家好，今天验证静音提示。') })
    await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('1500')
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    await expect.poll(() => snapshots.some((snapshot) => snapshot.raw === '大家好' && snapshot.current && !snapshot.final)).toBe(true)
    await expect.poll(() => {
      const accepted = snapshots.findIndex((snapshot) => snapshot.raw === '大家好' && snapshot.current && !snapshot.final)
      return snapshots.slice(accepted + 1).some((snapshot) => snapshot.state === 'LISTENING' && snapshot.speaking && !snapshot.current && snapshot.alignment === 'pending')
    }).toBe(true)
    await expect(page.getByRole('button', { name: '打断播报', exact: true })).toBeVisible({ timeout: 8000 })
    await expect(page.getByRole('region', { name: '讲稿匹配', exact: true })).toContainText('大家好')
    await expect(page.locator('.reading-cursor')).toHaveText('好')
    await expect(page.getByRole('region', { name: '实时语音识别', exact: true })).toContainText('上次结果')
    await expect(page.getByRole('alert').filter({ hasText: '自动提示' })).toHaveCount(0)
    await expect.poll(() => receivedAudio).toBeGreaterThan(0)
    expect(snapshots.some((snapshot) => snapshot.state === 'PROMPTING' && snapshot.alignment === 'reliable' && snapshot.aligned === '大家好' && snapshot.reading === 2)).toBe(true)
    expect(snapshots.every((snapshot) => snapshot.feedback === 0)).toBe(true)
    await page.getByText('状态机与流转诊断', { exact: true }).click()
    await mkdir(screenshots, { recursive: true })
    await page.screenshot({ path: `${screenshots}/pending-partial-settled.png`, fullPage: true, animations: 'disabled' })
    console.log('accepted cloud partial survived a second confirmed VAD onset without final text; aligned 大家好 at silence deadline; PROMPTING and in-memory audio received without AC-14')
  } finally {
    const end = page.getByRole('button', { name: '结束', exact: true })
    if (await end.isVisible() && await end.isEnabled()) { await end.click(); await confirmSessionEnd(page) }
    await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: false } })
    await page.request.post('/api/settings/model-configs/local-default/activate')
    await page.request.delete(`/api/settings/model-configs/${profile.id}`)
  }
})
