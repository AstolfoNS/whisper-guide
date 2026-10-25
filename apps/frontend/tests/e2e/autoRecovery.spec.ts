import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import type { SessionSnapshot } from '../../src/api/session'

const screenshots = 'test-results/screenshots/auto-recovery'
const first = '我们继续介绍实时讲稿定位的工作流程'
const second = '系统需要根据后续正文恢复可靠位置'
const script = `开场白。${'甲'.repeat(600)}${first}。${second}。接下来开始演示。`

/** Test-only 1.8 seconds of synthetic acoustic input, followed by live silence. */
test('recovers across 600 script units after noise and resumes automatic prompting without manual repositioning', async ({ page }) => {
  await page.addInitScript(() => {
    let frameIndex = 0
    let captureIndex = 0
    const trace = { lastVoiceAt: 0 }
    Object.assign(window, { recoveryAudioTrace: trace })
    const original = WebSocket.prototype.send
    WebSocket.prototype.send = function (data) {
      if (typeof data === 'string' && JSON.parse(data).type === 'audio_ready') {
        frameIndex = 0
        captureIndex++
      }
      if (data instanceof ArrayBuffer) {
        const pcm = new Int16Array(data.byteLength / 2)
        if (frameIndex < 90) {
          for (let index = 0; index < pcm.length; index++) {
            pcm[index] = Math.round(1600 * Math.sin(2 * Math.PI * 220 * (frameIndex * pcm.length + index) / 16000))
          }
          if (captureIndex === 1) trace.lastVoiceAt = Date.now()
        }
        frameIndex++
        return original.call(this, pcm.buffer)
      }
      return original.call(this, data)
    }
  })
  const created = await page.request.post('/api/settings/model-configs', { data: { name: '噪声后跨段自动恢复验证', settings: {
    stt_provider: 'cloud', cloud_opt_in: true, cloud_stt_protocol: 'volcengine-ws', cloud_stt_auth_mode: 'api-key',
    cloud_stt_resource_id: 'volc.seedasr.sauc.duration', cloud_stt_endpoint: 'ws://127.0.0.1:8010/mock/asr/recovery/bigmodel_async',
    cloud_stt_access_token: 'synthetic-browser-asr-key', llm_align_mode: 'off',
  } } })
  expect(created.status()).toBe(201)
  const profile = await created.json()
  const snapshots: { session: SessionSnapshot; receivedAt: number }[] = []
  const commands: string[] = []
  let receivedAudio = 0
  page.on('websocket', (socket) => {
    socket.on('framesent', ({ payload }) => {
      if (typeof payload !== 'string') return
      const command = JSON.parse(payload)
      if (typeof command.type === 'string') commands.push(command.type)
    })
    socket.on('framereceived', ({ payload }) => {
      const message = JSON.parse(String(payload))
      if (message.type === 'audio') receivedAudio++
      if (message.type === 'snapshot') snapshots.push({ session: message.session, receivedAt: Date.now() })
    })
  })
  expect((await page.request.post(`/api/settings/model-configs/${profile.id}/activate`)).ok()).toBe(true)
  expect((await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: true } })).ok()).toBe(true)
  try {
    await page.goto('/studio')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name: '噪声后自动恢复.txt', mimeType: 'text/plain', buffer: Buffer.from(script) })
    await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('1500')
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()

    await expect.poll(() => snapshots.some(({ session }) => session.alignment_status === 'mismatch')).toBe(true)
    await expect.poll(() => snapshots.some(({ session }) => session.recovery_status === 'confirming')).toBe(true)
    const expectedPosition = script.indexOf(second) + second.length - 1
    await expect.poll(() => snapshots.some(({ session }) => session.state === 'LISTENING'
      && session.reliable && session.reading_position === expectedPosition && session.aligned_transcript === second)).toBe(true)

    const mismatch = snapshots.findIndex(({ session }) => session.alignment_status === 'mismatch')
    const confirming = snapshots.findIndex(({ session }, index) => index > mismatch && session.recovery_status === 'confirming')
    const recovered = snapshots.findIndex(({ session }, index) => index > confirming && session.state === 'LISTENING'
      && session.alignment_status === 'reliable' && session.reading_position === expectedPosition)
    expect(mismatch).toBeGreaterThanOrEqual(0)
    expect(confirming).toBeGreaterThan(mismatch)
    expect(recovered).toBeGreaterThan(confirming)
    expect(snapshots[confirming]!.session.reading_position).toBeNull()
    expect(snapshots[recovered]!.session.reading_position).toBeGreaterThan(600)
    expect(snapshots[recovered]!.session.recovery_status).toBe('idle')
    expect(commands).not.toContain('reposition')
    expect(commands).not.toContain('manual_prompt')

    await expect.poll(() => snapshots.some(({ session }) => session.state === 'PROMPTING'
      && session.transition_trigger === 'silence_threshold_reached' && session.reading_position === expectedPosition), { timeout: 8000 }).toBe(true)
    await expect.poll(() => receivedAudio).toBeGreaterThan(0)
    const prompted = snapshots.find(({ session }) => session.state === 'PROMPTING'
      && session.transition_trigger === 'silence_threshold_reached' && session.reading_position === expectedPosition)!
    expect(prompted.session.prompt?.text).toBe('接下来开始演示。')
    const lastVoiceAt = await page.evaluate(() => (window as unknown as { recoveryAudioTrace: { lastVoiceAt: number } }).recoveryAudioTrace.lastVoiceAt)
    expect(lastVoiceAt).toBeGreaterThan(0)
    expect(prompted.receivedAt - lastVoiceAt).toBeGreaterThanOrEqual(1400)
    await expect(page.locator('.reading-cursor')).toHaveText('置')
    await expect(page.getByRole('region', { name: '讲稿匹配', exact: true })).toContainText(second)
    await expect(page.getByRole('alert').filter({ hasText: '自动提示' })).toHaveCount(0)
    await page.getByText('状态机与流转诊断', { exact: true }).click()
    await mkdir(screenshots, { recursive: true })
    await page.screenshot({ path: `${screenshots}/recovered-automatic-prompt.png`, fullPage: true, animations: 'disabled' })
    console.log(`noise mismatch → confirming → reliable at ${expectedPosition}; automatic prompt after ${prompted.receivedAt - lastVoiceAt}ms silence; no manual reposition or prompt; audio=${receivedAudio}`)
  } finally {
    const end = page.getByRole('button', { name: '结束', exact: true })
    if (await end.isVisible() && await end.isEnabled()) await end.click()
    await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: false } })
    await page.request.post('/api/settings/model-configs/local-default/activate')
    await page.request.delete(`/api/settings/model-configs/${profile.id}`)
  }
})
