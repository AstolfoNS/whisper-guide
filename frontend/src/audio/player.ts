/** One native-rate output context; cancelled work never starts a later prompt. */
const RESUME_TIMEOUT_MS = 5000
const DECODE_TIMEOUT_MS = 15000
const PLAYBACK_GRACE_MS = 2000

export class PlaybackCancelled extends Error {}
class PlaybackFailure extends Error {}

export class PromptPlayer {
  private context?: AudioContext
  private active?: { generation: number; cancel: () => void }
  private generation = 0
  private pending = new Set<() => void>()

  private outputContext() {
    if (this.context?.state === 'closed') this.context = undefined
    if (!this.context) {
      const Constructor = globalThis.AudioContext
        ?? (globalThis as typeof globalThis & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!Constructor) throw new PlaybackFailure('当前浏览器无法播放提示音，请使用支持 Web Audio 的浏览器。')
      try { this.context = new Constructor() }
      catch { throw new PlaybackFailure('无法启用音频输出，请检查系统音频设备后重试。') }
    }
    return this.context
  }

  private releaseContext(context: AudioContext) {
    if (this.context === context) this.context = undefined
    context.onstatechange = null
    try { void Promise.resolve(context.close()).catch(() => {}) } catch { /* Already closed. */ }
  }

  private waitFor<T>(operation: Promise<T>, timeout: number, message: string, generation: number): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      let settled = false
      const finish = (callback: () => void) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        this.pending.delete(cancel)
        callback()
      }
      const cancel = () => finish(() => reject(new PlaybackCancelled()))
      const timer = setTimeout(() => finish(() => reject(new PlaybackFailure(message))), timeout)
      this.pending.add(cancel)
      // Keep rejection handlers attached even after stop/timeout cancels this wait.
      operation.then(value => finish(() => resolve(value)), error => finish(() => reject(error)))
      if (generation !== this.generation) cancel()
    })
  }

  async unlock() {
    const generation = this.generation
    const context = this.outputContext()
    try {
      if (context.state !== 'running') {
        // Called synchronously from the user's start/continue gesture.
        await this.waitFor(context.resume(), RESUME_TIMEOUT_MS,
          '浏览器未允许播放提示音，请返回页面点击继续，并检查声音权限。', generation)
      }
      if (generation !== this.generation || context !== this.context) throw new PlaybackCancelled()
      if (context.state !== 'running') {
        throw new PlaybackFailure('音频输出尚未恢复，请返回页面点击继续，并检查声音权限。')
      }
    } catch (error) {
      if (error instanceof PlaybackCancelled) throw error
      if (generation !== this.generation || context !== this.context) throw new PlaybackCancelled()
      this.releaseContext(context)
      throw error instanceof PlaybackFailure ? error
        : new PlaybackFailure('无法恢复提示音播放，请检查声音权限后点击继续。')
    }
  }

  stop() {
    this.generation++
    for (const cancel of [...this.pending]) cancel()
    this.active?.cancel()
  }

  async play(encoded: string, ended: () => void, failed: (reason?: string) => void) {
    this.stop()
    const generation = this.generation
    let settled = false
    let context: AudioContext | undefined
    let source: AudioBufferSourceNode | undefined
    let watchdog: ReturnType<typeof setTimeout> | undefined
    let stateChanged: (() => void) | undefined
    const current = () => generation === this.generation && (!context || context === this.context)
    const cleanup = (stop: boolean) => {
      clearTimeout(watchdog)
      if (context && context.onstatechange === stateChanged) context.onstatechange = null
      if (this.active?.generation === generation) this.active = undefined
      if (source) {
        source.onended = null
        if (stop) { try { source.stop() } catch { /* It may never have started. */ } }
        try { source.disconnect() } catch { /* Output may already be disconnected. */ }
      }
    }
    const complete = (success: boolean, reason?: string) => {
      if (settled || !current()) return
      settled = true
      cleanup(!success)
      if (success) ended()
      else failed(reason)
    }
    try {
      await this.unlock()
      if (!current()) return
      context = this.context!
      let bytes: Uint8Array
      try {
        bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0))
        if (!bytes.length) throw new Error()
      } catch { throw new PlaybackFailure('提示音频数据无效，请点击继续重试。') }
      let buffer: AudioBuffer
      try {
        buffer = await this.waitFor(context.decodeAudioData(bytes.buffer as ArrayBuffer), DECODE_TIMEOUT_MS,
          '提示音频处理超时，请点击继续重试。', generation)
      } catch (error) {
        if (error instanceof PlaybackCancelled || error instanceof PlaybackFailure) throw error
        throw new PlaybackFailure('提示音频无法解码，请重试或检查浏览器音频支持。')
      }
      if (!current()) return
      if (context.state !== 'running') throw new PlaybackFailure('音频输出已中断，请检查设备后点击继续。')
      if (!Number.isFinite(buffer.duration) || buffer.duration <= 0) {
        throw new PlaybackFailure('提示音频没有可播放内容，请点击继续重试。')
      }
      source = context.createBufferSource()
      this.active = { generation, cancel: () => { settled = true; cleanup(true) } }
      source.buffer = buffer
      source.connect(context.destination)
      source.onended = () => {
        // Only a real, current source ending on a running clock confirms playback.
        if (context?.state === 'running') complete(true)
        else complete(false, '音频输出已中断，请检查设备后点击继续。')
      }
      stateChanged = () => {
        if (context?.state === 'running' || !current() || settled) return
        complete(false, context?.state === 'closed'
          ? '音频输出已关闭，请检查设备后点击继续。'
          : '提示音播放被系统中断，请返回页面并点击继续。')
      }
      context.onstatechange = stateChanged
      // Missing ended events are failures, never simulated successful completion.
      watchdog = setTimeout(() => complete(false, '提示音未能播放完成，请检查音频输出后点击继续。'),
        Math.min(2 ** 31 - 1, buffer.duration * 1000 + PLAYBACK_GRACE_MS))
      source.start()
    } catch (error) {
      if (error instanceof PlaybackCancelled) return
      complete(false, error instanceof PlaybackFailure ? error.message
        : '无法播放提示音，请检查音频输出后点击继续。')
    }
  }

  close() {
    this.stop()
    if (this.context) this.releaseContext(this.context)
  }
}
