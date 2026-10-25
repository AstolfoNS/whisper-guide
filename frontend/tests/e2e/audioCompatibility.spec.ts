import { test, expect, type Page } from '@playwright/test'

interface AudioCompatibilityTrace {
  contexts: { id: number; rate: number; inputRate?: number; requestedRate?: number; state: string; role: string; trackStates: string[]; started: number }[]
  pcmFrames: number
  invalidFrames: number
  commands: { type: string; prompt_id?: string }[]
  snapshots: { state: string; generation: number; promptId?: string }[]
}

/** Native Chromium Web Audio + fake media device; no claim of audible output or ASR accuracy. */
async function observeNativeAudio(page: Page) {
  await page.addInitScript(() => {
    type ContextRecord = { context: AudioContext; input?: MediaStream; requestedRate?: number; role: string; started: number }
    const contexts: ContextRecord[] = []
    const trace = { pcmFrames: 0, invalidFrames: 0, commands: [] as { type: string; prompt_id?: string }[],
      snapshots: [] as { state: string; generation: number; promptId?: string }[] }
    const NativeContext = window.AudioContext
    window.AudioContext = class extends NativeContext {
      constructor(options?: AudioContextOptions) {
        super(options)
        contexts.push({ context: this, requestedRate: options?.sampleRate, role: 'unassigned', started: 0 })
      }
      createMediaStreamSource(stream: MediaStream) {
        const source = super.createMediaStreamSource(stream)
        const record = contexts.find(item => item.context === this)!
        record.input = stream
        record.role = 'capture'
        return source
      }
      createBufferSource() {
        const source = super.createBufferSource()
        const record = contexts.find(item => item.context === this)!
        record.role = 'playback'
        const start = source.start.bind(source)
        source.start = (...args: Parameters<AudioBufferSourceNode['start']>) => {
          record.started++
          return start(...args)
        }
        return source
      }
    }
    const NativeSocket = window.WebSocket
    window.WebSocket = class extends NativeSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        this.addEventListener('message', (event: MessageEvent) => {
          const message = JSON.parse(String(event.data))
          if (message.type === 'snapshot') trace.snapshots.push({ state: message.session.state,
            generation: message.session.generation, promptId: message.session.prompt?.id })
        })
      }
      send(data: string | ArrayBufferLike | Blob | ArrayBufferView) {
        if (data instanceof ArrayBuffer) {
          trace.pcmFrames++
          if (data.byteLength !== 640) trace.invalidFrames++
        } else if (typeof data === 'string') {
          const command = JSON.parse(data)
          trace.commands.push({ type: command.type, prompt_id: command.prompt_id })
        }
        return super.send(data)
      }
    }
    Object.assign(window, { audioCompatibility: {
      snapshot: () => ({ ...trace, contexts: contexts.map((item, id) => ({ id, rate: item.context.sampleRate,
        inputRate: item.input?.getAudioTracks()[0]?.getSettings().sampleRate, requestedRate: item.requestedRate,
        state: item.context.state, role: item.role, trackStates: item.input?.getTracks().map(track => track.readyState) ?? [], started: item.started,
      })) }),
      suspendCapture: () => contexts.findLast(item => item.role === 'capture' && item.context.state === 'running')!.context.suspend(),
      suspendPlayback: () => contexts.findLast(item => item.role === 'playback' && item.started > 0 && item.context.state === 'running')!.context.suspend(),
    } })
  })
}

async function snapshot(page: Page): Promise<AudioCompatibilityTrace> {
  return page.evaluate(() => (window as unknown as { audioCompatibility: { snapshot: () => AudioCompatibilityTrace } }).audioCompatibility.snapshot())
}

async function startFakeDeviceSession(page: Page, name: string) {
  expect((await page.request.post('/api/settings/model-configs/local-default/activate')).ok()).toBe(true)
  expect((await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: false } })).ok()).toBe(true)
  await page.goto('/studio')
  await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name, mimeType: 'text/plain', buffer: Buffer.from('大家好，继续验证浏览器音频设备。') })
  await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('30000')
  await page.getByRole('button', { name: '开始演讲', exact: true }).click()
  await expect.poll(async () => (await snapshot(page)).pcmFrames).toBeGreaterThan(3)
}

async function endSession(page: Page) {
  const end = page.getByRole('button', { name: '结束', exact: true })
  if (await end.isVisible() && await end.isEnabled()) await end.click()
}

test('native fake-device capture matches its reported rate and restarts after its context is suspended', async ({ page }) => {
  await observeNativeAudio(page)
  try {
    await startFakeDeviceSession(page, '采集采样率恢复.txt')
    const before = await snapshot(page)
    const capture = before.contexts.findLast(item => item.role === 'capture' && item.state === 'running')!
    expect(capture).toBeTruthy()
    expect(capture.inputRate).toBeGreaterThan(0)
    expect(capture.requestedRate).toBe(capture.inputRate)
    expect(capture.rate).toBe(capture.inputRate)
    expect(before.invalidFrames).toBe(0)
    await page.getByRole('button', { name: '打开服务诊断', exact: true }).click()
    await expect(page.locator('.health-details > div').filter({ has: page.locator('dt', { hasText: '采集采样率' }) }))
      .toContainText(`输入 ${capture.inputRate} Hz · 音频图 ${capture.rate} Hz → 16000 Hz 单声道 PCM`)
    await page.keyboard.press('Escape')
    await page.evaluate(() => (window as unknown as { audioCompatibility: { suspendCapture: () => Promise<void> } }).audioCompatibility.suspendCapture())
    await expect(page.getByRole('button', { name: '继续演讲', exact: true })).toBeVisible()
    await expect.poll(async () => (await snapshot(page)).snapshots.at(-1)?.state).toBe('PAUSED')
    await expect.poll(async () => (await snapshot(page)).contexts[capture.id]?.state).toBe('closed')
    expect((await snapshot(page)).contexts[capture.id]!.trackStates.every(state => state === 'ended')).toBe(true)
    const pausedFrames = (await snapshot(page)).pcmFrames
    await page.getByRole('button', { name: '继续演讲', exact: true }).click()
    await expect.poll(async () => (await snapshot(page)).pcmFrames).toBeGreaterThan(pausedFrames + 3)
    const resumed = await snapshot(page)
    const nextCapture = resumed.contexts.findLast(item => item.role === 'capture' && item.state === 'running')!
    expect(nextCapture.id).not.toBe(capture.id)
    expect(nextCapture.rate).toBe(nextCapture.inputRate)
    expect(resumed.invalidFrames).toBe(0)
    expect(resumed.commands.filter(command => command.type === 'audio_ready')).toHaveLength(2)
    console.log(`native fake microphone: input=${capture.inputRate}Hz graph=${capture.rate}Hz; 640B PCM; suspended capture closed and restarted; no ASR accuracy assertion`)
  } finally { await endSession(page) }
})

test('suspending a native prompt output sends playback_failed and safely pauses the session', async ({ page }) => {
  await observeNativeAudio(page)
  try {
    await startFakeDeviceSession(page, '播放中断保护.txt')
    await page.getByRole('button', { name: '手动提示', exact: true }).click()
    await expect.poll(async () => (await snapshot(page)).contexts.some(item => item.role === 'playback' && item.started > 0 && item.state === 'running')).toBe(true)
    const prompting = (await snapshot(page)).snapshots.findLast(item => item.state === 'PROMPTING')!
    expect(prompting.promptId).toBeTruthy()
    await page.evaluate(() => (window as unknown as { audioCompatibility: { suspendPlayback: () => Promise<void> } }).audioCompatibility.suspendPlayback())
    await expect(page.getByRole('button', { name: '继续演讲', exact: true })).toBeVisible()
    await expect.poll(async () => (await snapshot(page)).snapshots.at(-1)?.state).toBe('PAUSED')
    const stopped = await snapshot(page)
    expect(stopped.commands.filter(command => command.type === 'playback_failed' && command.prompt_id === prompting.promptId)).toHaveLength(1)
    expect(stopped.commands.some(command => command.type === 'playback_finished' && command.prompt_id === prompting.promptId)).toBe(false)
    await page.getByRole('button', { name: '打开服务诊断', exact: true }).click()
    await expect(page.locator('.health-details > div').filter({ has: page.locator('dt', { hasText: '最近播放' }) })).toContainText('播放失败')
    await expect(page.locator('.health-details > div').filter({ has: page.locator('dt', { hasText: '播放原因' }) })).toContainText('中断')
    await page.keyboard.press('Escape')
    console.log('native output suspended during prompt: playback_failed exactly once, no playback_finished, session PAUSED; audibility not asserted')
  } finally { await endSession(page) }
})
