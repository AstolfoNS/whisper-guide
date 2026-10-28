import { test, expect } from '@playwright/test'
import type { SessionSnapshot } from '../../src/api/session'

test('progress timeout takes over continuous voice without transcripts, then waits for new progress', async ({ page }) => {
  await page.addInitScript(() => {
    const trace = { readyAt: 0, frames: 0, commands: [] as string[] }
    Object.assign(window, { progressInputTrace: trace })
    const send = WebSocket.prototype.send
    WebSocket.prototype.send = function (data) {
      if (typeof data === 'string') {
        const command = JSON.parse(data)
        trace.commands.push(command.type)
        if (command.type === 'audio_ready' && !trace.readyAt) trace.readyAt = Date.now()
      }
      if (data instanceof ArrayBuffer) {
        const pcm = new Int16Array(data.byteLength / 2)
        for (let index = 0; index < pcm.length; index++) {
          pcm[index] = Math.round(1600 * Math.sin(2 * Math.PI * 220 * (trace.frames * pcm.length + index) / 16000))
        }
        trace.frames++
        return send.call(this, pcm.buffer)
      }
      return send.call(this, data)
    }
  })
  expect((await page.request.post('/api/settings/model-configs/local-default/activate')).ok()).toBe(true)
  expect((await page.request.post('http://127.0.0.1:8010/mock/progress-noise', { data: { enabled: true } })).ok()).toBe(true)
  const states: { value: SessionSnapshot; at: number }[] = []
  const promptIds = new Set<string>()
  page.on('websocket', socket => socket.on('framereceived', ({ payload }) => {
    const message = JSON.parse(String(payload))
    if (message.type !== 'snapshot') return
    states.push({ value: message.session, at: Date.now() })
    if (message.session.state === 'PROMPTING') promptIds.add(message.session.prompt.id)
  }))
  try {
    await page.goto('/studio')
    await page.getByLabel('上传讲稿', { exact: false }).setInputFiles({
      name: '持续声音进度提示.txt', mimeType: 'text/plain', buffer: Buffer.from('第一段辅助。第二段不要自动接管。'),
    })
    await page.getByRole('button', { name: '更多演讲操作', exact: true }).click()
    await page.getByRole('combobox', { name: '自动提示模式', exact: true }).selectOption('progress')
    await page.getByRole('textbox', { name: '进度等待', exact: true }).fill('2000')
    await page.getByRole('button', { name: '应用演讲设置', exact: true }).click()
    await page.getByRole('button', { name: '开始演讲', exact: true }).click()
    await expect.poll(() => promptIds.size, { timeout: 7000 }).toBe(1)
    const first = states.find(item => item.value.state === 'PROMPTING')!
    expect(first.value.prompt_mode).toBe('progress')
    expect(first.value.transition_trigger).toBe('progress_timeout')
    expect(first.value.prompt?.text).toBe('第一段辅助。')
    expect(first.value.reading_position).toBeNull()
    const trace = await page.evaluate(() => (window as unknown as { progressInputTrace: { readyAt: number; frames: number; commands: string[] } }).progressInputTrace)
    expect(trace.frames).toBeGreaterThan(40)
    expect(first.at - trace.readyAt).toBeGreaterThanOrEqual(1900)
    expect(states.some(item => item.value.state === 'LISTENING' && item.value.speaking)).toBe(true)
    expect(trace.commands).not.toContain('manual_prompt')
    await expect.poll(() => states.at(-1)?.value.state, { timeout: 10000 }).toBe('LISTENING')
    await expect(page.locator('.prompt-wait')).toContainText(/等待.*朗读|已提示|等待新/)
    // A fresh complete timeout with live input must not advance to the next sentence.
    await page.waitForTimeout(2600)
    expect(promptIds.size).toBe(1)
    expect(states.at(-1)?.value.reading_position).toBeNull()
    await page.getByRole('button', { name: '再等一下', exact: true }).click()
    await expect.poll(() => promptIds.size, { timeout: 7000 }).toBe(2)
    expect(states.filter(item => item.value.state === 'PROMPTING').at(-1)?.value.prompt?.text).toBe('第二段不要自动接管。')
  } finally {
    if (!page.isClosed()) {
      await page.keyboard.press('Escape')
      await page.keyboard.press('Alt+Shift+E')
      const confirm = page.getByRole('button', { name: '确认结束', exact: true })
      if (await confirm.isVisible()) await confirm.click()
    }
    await page.request.post('http://127.0.0.1:8010/mock/progress-noise', { data: { enabled: false } })
  }
})
