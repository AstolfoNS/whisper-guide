import { confirmSessionEnd } from './sessionActions'
import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'

const screenshots = 'test-results/screenshots/silence-state'

test('continuous microphone silence automatically prompts, finishes, and prompts the next sentence', async ({ page }) => {
  await page.addInitScript(() => {
    const trace = { frames: 0, readyAt: 0, prompts: [] as { elapsed: number; text: string; reading: number | null }[], states: [] as string[], audio: 0 }
    Object.assign(window, { quietTrace: trace })
    const originalSend = WebSocket.prototype.send
    WebSocket.prototype.send = function (data) {
      if (data instanceof ArrayBuffer) {
        trace.frames++
        return originalSend.call(this, new ArrayBuffer(data.byteLength))
      }
      if (typeof data === 'string') {
        const command = JSON.parse(data)
        if (command.type === 'audio_ready') trace.readyAt = performance.now()
      }
      return originalSend.call(this, data)
    }
    const originalAdd = WebSocket.prototype.addEventListener
    const OriginalSocket = window.WebSocket
    window.WebSocket = class extends OriginalSocket {
      constructor(url: string | URL, protocols?: string | string[]) {
        super(url, protocols)
        originalAdd.call(this, 'message', (event: MessageEvent) => {
          const message = JSON.parse(String(event.data))
          if (message.type === 'audio') trace.audio++
          if (message.type !== 'snapshot') return
          const snapshot = message.session
          if (trace.states.at(-1) !== snapshot.state) trace.states.push(snapshot.state)
          if (trace.states.length > 20) trace.states.shift()
          if (snapshot.state === 'PROMPTING' && snapshot.transition_trigger === 'silence_threshold_reached') {
            if (trace.prompts.at(-1)?.text !== snapshot.prompt.text) trace.prompts.push({ elapsed: performance.now() - trace.readyAt, text: snapshot.prompt.text, reading: snapshot.reading_position })
          }
        })
      }
    }
  })
  expect((await page.request.post('/api/settings/model-configs/local-default/activate')).ok()).toBe(true)
  expect((await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: true } })).ok()).toBe(true)
  try {
    await page.goto('/studio')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({ name: '持续静音验证.txt', mimeType: 'text/plain', buffer: Buffer.from('第一句提示。第二句提示。') })
    await page.getByRole('textbox', { name: '静音等待', exact: true }).fill('1000')
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    await expect(page.getByRole('button', { name: '打断播报', exact: true })).toBeVisible()
    await expect(page.getByRole('region', { name: '实时语音识别', exact: true })).toContainText('等待语音输入')
    const first = await page.evaluate(() => (window as unknown as { quietTrace: { frames: number; prompts: { elapsed: number; text: string; reading: number | null }[] } }).quietTrace)
    expect(first.frames).toBeGreaterThan(25)
    expect(first.prompts[0]?.elapsed).toBeGreaterThanOrEqual(950)
    expect(first.prompts[0]?.text).toBe('第一句提示。')
    expect(first.prompts[0]?.reading).toBeNull()
    await page.getByText('状态机与流转诊断', { exact: true }).click()
    await mkdir(screenshots, { recursive: true })
    await page.locator('.state-monitor').screenshot({ path: `${screenshots}/state-machine.png`, animations: 'disabled' })
    await page.getByLabel('界面主题', { exact: true }).selectOption('dark')
    await page.locator('.state-monitor').screenshot({ path: `${screenshots}/state-machine-dark.png`, animations: 'disabled' })
    await expect.poll(async () => page.evaluate(() => (window as unknown as { quietTrace: { prompts: unknown[] } }).quietTrace.prompts.length), { timeout: 12000 }).toBe(2)
    const final = await page.evaluate(() => (window as unknown as { quietTrace: { states: string[]; audio: number; prompts: { elapsed: number; text: string; reading: number | null }[] } }).quietTrace)
    expect(final.states).toEqual(['READY', 'LISTENING', 'PROMPTING', 'LISTENING', 'PROMPTING'])
    expect(final.prompts[1]?.text).toBe('第二句提示。')
    expect(final.prompts[1]?.elapsed).toBeGreaterThanOrEqual(950)
    expect(final.prompts[1]?.reading).toBeNull()
    expect(final.audio).toBeGreaterThanOrEqual(1)
    console.log(`quiet prompt: first=${Math.round(final.prompts[0]!.elapsed)}ms second=${Math.round(final.prompts[1]!.elapsed)}ms; microphoneFrames=${first.frames}`)
  } finally {
    const end = page.getByRole('button', { name: '结束', exact: true })
    if (await end.isVisible() && await end.isEnabled()) { await end.click(); await confirmSessionEnd(page) }
    await page.request.post('http://127.0.0.1:8010/mock/silence', { data: { enabled: false } })
  }
})
