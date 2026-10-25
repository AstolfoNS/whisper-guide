import { test, expect } from '@playwright/test'
import type { SessionSnapshot } from '../../src/api/session'

test('a cloud result timeout still prompts from the saved anchor after untranscribed voice and live silence', async ({ page }) => {
  // Native capture/worklet stay active. Only outbound PCM values are replaced
  // by two finite synthetic bursts followed by continuous live silence.
  await page.addInitScript(() => {
    let index = 0, turn = 0
    const trace = { frames: 0, lastVoiceAt: 0 }
    Object.assign(window, { silenceFallbackInput: trace })
    const send = WebSocket.prototype.send
    WebSocket.prototype.send = function (data) {
      if (typeof data === 'string' && JSON.parse(data).type === 'audio_ready') { index = 0; turn++ }
      if (data instanceof ArrayBuffer) {
        const pcm = new Int16Array(data.byteLength / 2)
        if (index < 30 || (index >= 45 && index < 60)) {
          for (let sample = 0; sample < pcm.length; sample++) {
            pcm[sample] = Math.round(1600 * Math.sin(2 * Math.PI * 220 * (index * pcm.length + sample) / 16000))
          }
          if (turn === 1) trace.lastVoiceAt = Date.now()
        }
        trace.frames++
        index++
        return send.call(this, pcm.buffer)
      }
      return send.call(this, data)
    }
  })
  const created = await page.request.post('/api/settings/model-configs', { data: { name: '无新转写静音兜底验证', settings: {
    stt_provider: 'cloud', cloud_opt_in: true, cloud_stt_protocol: 'volcengine-ws', cloud_stt_auth_mode: 'api-key',
    cloud_stt_resource_id: 'volc.seedasr.sauc.duration', cloud_stt_endpoint: 'ws://127.0.0.1:8010/mock/asr/no-result/bigmodel_async',
    cloud_stt_access_token: 'synthetic-browser-asr-key', cloud_stt_timeout_ms: 1000, llm_align_mode: 'off',
  } } })
  expect(created.status()).toBe(201)
  const profile = await created.json()
  const states: { snapshot: SessionSnapshot; at: number }[] = []
  const diagnostics: { generation: number; value: Partial<SessionSnapshot> }[] = []
  const commands: string[] = []
  let audio = 0
  page.on('websocket', socket => {
    socket.on('framereceived', ({ payload }) => {
      const message = JSON.parse(String(payload))
      if (message.type === 'snapshot') states.push({ snapshot: message.session, at: Date.now() })
      if (message.type === 'audio_diagnostics') diagnostics.push({ generation: message.generation, value: message.diagnostics })
      if (message.type === 'audio') audio++
    })
    socket.on('framesent', ({ payload }) => {
      if (typeof payload === 'string') commands.push(JSON.parse(payload).type)
    })
  })
  expect((await page.request.post(`/api/settings/model-configs/${profile.id}/activate`)).ok()).toBe(true)
  try {
    await page.goto('/studio')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name: '无新转写静音兜底.txt', mimeType: 'text/plain', buffer: Buffer.from('大家好。下一段继续演讲。') })
    await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('3000')
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    await expect.poll(() => states.some(({ snapshot }) => snapshot.state === 'LISTENING' && snapshot.reliable && snapshot.reading_position === 2)).toBe(true)
    await expect.poll(() => states.some(({ snapshot }) => snapshot.state === 'LISTENING' && snapshot.alignment_status === 'pending' && snapshot.raw_transcript_is_current === false && snapshot.reading_position === 2)).toBe(true)
    await expect.poll(() => states.some(({ snapshot }) => snapshot.state === 'LISTENING' && snapshot.stt_fallback && snapshot.stt_provider_active === 'local')).toBe(true)
    const fallback = states.find(({ snapshot }) => snapshot.stt_fallback && snapshot.state === 'LISTENING')!.snapshot
    expect(fallback.stt_fallback_reason).toMatchObject({ error_code: 'TIMEOUT', stage: 'result', timeout_ms: 1000 })
    expect(fallback.reading_position).toBe(2)
    expect(fallback.reliable).toBe(false)
    await page.getByRole('button', { name: '打开服务诊断', exact: true }).click()
    await expect(page.getByTestId('fallback-reason')).toContainText('1000 ms')
    await expect(page.locator('.health-details')).toContainText('自动提示等待原因')
    await expect(page.locator('.health-details')).toContainText('静音计时')
    await expect(page.locator('.health-details')).toContainText('音频处理')
    await page.keyboard.press('Escape')

    await expect.poll(() => states.some(({ snapshot }) => snapshot.state === 'PROMPTING' && snapshot.transition_trigger === 'recognition_wait_timeout'), { timeout: 8000 }).toBe(true)
    await expect.poll(() => audio).toBeGreaterThan(0)
    const prompted = states.find(({ snapshot }) => snapshot.state === 'PROMPTING' && snapshot.transition_trigger === 'recognition_wait_timeout')!
    expect(prompted.snapshot.silence_prompt_policy).toBe('saved_position')
    expect(prompted.snapshot.reliable).toBe(false)
    expect(prompted.snapshot.alignment_status).toBe('pending')
    expect(prompted.snapshot.reading_position).toBe(2)
    expect(prompted.snapshot.prompt_position).toBe(4)
    expect(prompted.snapshot.prompt?.text).toBe('下一段继续演讲。')
    expect(prompted.snapshot.prompt?.start).toBe(4)
    expect(states.every(({ snapshot }) => snapshot.reading_position === null || snapshot.reading_position === 2)).toBe(true)
    const listening = states.filter(({ snapshot }) => snapshot.state === 'LISTENING')
    expect(listening.every(({ snapshot }) => !snapshot.raw_transcript || snapshot.raw_transcript === '大家好')).toBe(true)
    expect(diagnostics.some(({ value }) => value.audio_ready && value.last_audio_received_age_ms != null && value.last_audio_received_age_ms < 500 && value.silence_remaining_ms != null)).toBe(true)
    expect(commands).not.toContain('reposition')
    expect(commands).not.toContain('manual_prompt')
    const input = await page.evaluate(() => (window as unknown as { silenceFallbackInput: { frames: number; lastVoiceAt: number } }).silenceFallbackInput)
    expect(input.frames).toBeGreaterThan(150)
    expect(prompted.at - input.lastVoiceAt).toBeGreaterThanOrEqual(2800)
    await expect(page.getByRole('region', { name: '讲稿匹配', exact: true })).toContainText('大家好')
    console.log(`cloud result timeout=1000ms → local; raw unchanged; unreliable reading=2; saved prompt=4; live silence=${prompted.at - input.lastVoiceAt}ms; automatic audio=${audio}`)
  } finally {
    const end = page.getByRole('button', { name: '结束', exact: true })
    if (await end.isVisible() && await end.isEnabled()) await end.click()
    await page.request.post('/api/settings/model-configs/local-default/activate')
    await page.request.delete(`/api/settings/model-configs/${profile.id}`)
  }
})
