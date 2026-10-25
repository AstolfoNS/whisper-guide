import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

type SnapshotTrace = { state: string; ready?: boolean; generation: number; raw: string; final: boolean; current: boolean; fallback: boolean; reading: number | null; aligned: string; prompt: string }
type ReadinessTrace = { snapshots: SnapshotTrace[]; displayedRaw: string[]; sawConnectingMessage: boolean; firstPcm: { ready: boolean; generation: number; readyAt: number; sentAt: number } | null; pcmBeforeReady: number; audio: number }
const screenshots = 'test-results/screenshots/asr-readiness'

test('waits for a slow cloud handshake before PCM and replaces partials before the correct automatic prompt', async ({ page }) => {
  await page.addInitScript(() => {
    const trace: ReadinessTrace = { snapshots: [], displayedRaw: [], sawConnectingMessage: false, firstPcm: null, pcmBeforeReady: 0, audio: 0 }
    Object.assign(window, { asrReadinessTrace: trace })
    let frameIndex = 0, currentGeneration = -1, readyAt = 0
    let ready = false
    const observer = new MutationObserver(() => {
      if (document.body?.textContent?.includes('正在连接语音识别，请稍候再朗读')) trace.sawConnectingMessage = true
      const raw = document.querySelector('[aria-label="实时语音识别"] > p')?.textContent ?? ''
      if (raw && trace.displayedRaw.at(-1) !== raw) trace.displayedRaw.push(raw)
    })
    observer.observe(document, { childList: true, subtree: true, characterData: true })
    const NativeSocket = window.WebSocket
    window.WebSocket = class extends NativeSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        this.addEventListener('message', (event: MessageEvent) => {
          const message = JSON.parse(String(event.data))
          if (message.type === 'audio') trace.audio++
          if (message.type !== 'snapshot') return
          const current = message.session
          currentGeneration = current.generation
          ready = current.state === 'LISTENING' && current.stt_ready === true
          if (ready && readyAt === 0) readyAt = performance.now()
          trace.snapshots.push({ state: current.state, ready: current.stt_ready, generation: current.generation,
            raw: current.raw_transcript, final: current.raw_transcript_is_final === true,
            current: current.raw_transcript_is_current !== false, fallback: current.stt_fallback === true,
            reading: current.reading_position, aligned: current.aligned_transcript, prompt: current.prompt?.text ?? '',
          })
        })
      }
      send(data: string | ArrayBufferLike | Blob | ArrayBufferView) {
        if (typeof data === 'string' && JSON.parse(data).type === 'audio_ready') frameIndex = 0
        if (data instanceof ArrayBuffer) {
          if (!trace.firstPcm) trace.firstPcm = { ready, generation: currentGeneration, readyAt, sentAt: performance.now() }
          if (!ready) trace.pcmBeforeReady++
          const pcm = new Int16Array(data.byteLength / 2)
          if (frameIndex < 60) {
            for (let index = 0; index < pcm.length; index++) pcm[index] = Math.round(600 * Math.sin(2 * Math.PI * 220 * (frameIndex * pcm.length + index) / 16000))
          }
          frameIndex++
          return super.send(pcm.buffer)
        }
        return super.send(data)
      }
    }
  })
  const created = await page.request.post('/api/settings/model-configs', { data: { name: '慢握手与原始识别修订验证', settings: {
    stt_provider: 'cloud', cloud_opt_in: true, cloud_stt_protocol: 'volcengine-ws', cloud_stt_auth_mode: 'api-key',
    cloud_stt_resource_id: 'volc.seedasr.sauc.duration', cloud_stt_endpoint: 'ws://127.0.0.1:8010/mock/asr/ready-delay/bigmodel_async',
    cloud_stt_access_token: 'synthetic-browser-asr-key', cloud_stt_timeout_ms: 5000, llm_align_mode: 'off',
  } } })
  expect(created.status()).toBe(201)
  const profile = await created.json()
  expect((await page.request.post(`/api/settings/model-configs/${profile.id}/activate`)).ok()).toBe(true)
  expect((await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: true } })).ok()).toBe(true)
  try {
    await page.goto('/studio')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name: '识别连接就绪验证.txt', mimeType: 'text/plain', buffer: Buffer.from('我将继续立足岗位。下一段继续演讲。') })
    await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('1000')
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    const trace = () => page.evaluate(() => (window as unknown as { asrReadinessTrace: ReadinessTrace }).asrReadinessTrace)
    await expect.poll(async () => (await trace()).snapshots.some(snapshot => snapshot.state === 'LISTENING' && snapshot.ready === false)).toBe(true)
    await expect.poll(async () => (await trace()).snapshots.some(snapshot => snapshot.state === 'LISTENING' && snapshot.ready === true)).toBe(true)
    const phrase = '我将继续立足岗位'
    await expect.poll(async () => (await trace()).snapshots.some(snapshot => snapshot.raw === phrase && snapshot.final && snapshot.current)).toBe(true)
    await expect(page.getByRole('region', { name: '实时语音识别', exact: true }).locator('p')).toHaveText(phrase)
    await expect(page.locator('.reading-cursor')).toHaveText('位')
    await expect.poll(async () => (await trace()).snapshots.some(snapshot => snapshot.state === 'PROMPTING' && snapshot.prompt === '下一段继续演讲。')).toBe(true)
    await expect.poll(async () => (await trace()).audio).toBeGreaterThan(0)

    const final = await trace()
    const waiting = final.snapshots.findIndex(snapshot => snapshot.state === 'LISTENING' && snapshot.ready === false)
    const connected = final.snapshots.findIndex((snapshot, index) => index > waiting && snapshot.state === 'LISTENING' && snapshot.ready === true)
    expect(connected).toBeGreaterThan(waiting)
    expect(final.sawConnectingMessage).toBe(true)
    expect(final.firstPcm?.ready).toBe(true)
    expect(final.firstPcm!.generation).toBe(final.snapshots[connected]!.generation)
    expect(final.firstPcm!.readyAt).toBeGreaterThan(0)
    expect(final.firstPcm!.sentAt).toBeGreaterThanOrEqual(final.firstPcm!.readyAt)
    expect(final.pcmBeforeReady).toBe(0)
    expect(final.snapshots.every(snapshot => !snapshot.fallback)).toBe(true)
    const expected = ['我将', '我将继续', '我将继续立足', phrase]
    let previous = -1
    for (const raw of expected) {
      const position = final.snapshots.findIndex((snapshot, index) => index > previous && snapshot.raw === raw && snapshot.current)
      expect(position, `raw revision ${raw}`).toBeGreaterThan(previous)
      previous = position
    }
    const displayed = final.displayedRaw.filter(raw => raw.startsWith('我将'))
    expect(displayed).toContain(phrase)
    expect(displayed.every(raw => expected.includes(raw))).toBe(true)
    expect(final.snapshots.some(snapshot => snapshot.reading === 7 && snapshot.aligned === phrase)).toBe(true)
    await mkdir(screenshots, { recursive: true })
    await page.screenshot({ path: `${screenshots}/connected-cloud-final.png`, fullPage: true, animations: 'disabled' })
    console.log(`slow handshake: readiness false→true; first PCM after readiness; premature PCM=${final.pcmBeforeReady}; fallback=false; raw partial revisions replace; reading=7; automatic audio=${final.audio}`)
  } finally {
    const end = page.getByRole('button', { name: '结束', exact: true })
    if (await end.isVisible() && await end.isEnabled()) await end.click()
    await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: false } })
    await page.request.post('/api/settings/model-configs/local-default/activate')
    await page.request.delete(`/api/settings/model-configs/${profile.id}`)
  }
})
