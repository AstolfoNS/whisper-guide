import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { PromptPlayer } from '../../src/audio/player'

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (error: Error) => void
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

function audio(options: {
  state?: string; resume?: () => Promise<void>; decode?: () => Promise<{ duration: number }>
  startFailure?: boolean; connectFailure?: boolean; cleanupFailure?: boolean; closeFailure?: boolean
} = {}) {
  const contexts: FakeContext[] = []
  const sources: {
    buffer: unknown; onended: (() => void) | null
    connect: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn>
    start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn>
  }[] = []
  const constructorArgs: unknown[][] = []
  class FakeContext {
    state = options.state ?? 'running'
    sampleRate = 48000
    destination = {}
    onstatechange: (() => void) | null = null
    constructor(...args: unknown[]) { constructorArgs.push(args); contexts.push(this) }
    resume = vi.fn(async () => {
      if (options.resume) await options.resume()
      this.state = 'running'
    })
    decodeAudioData = vi.fn(options.decode ?? (async () => ({ duration: 0.25 })))
    createBufferSource = vi.fn(() => {
      const source = {
        buffer: null as unknown, onended: null as (() => void) | null,
        connect: vi.fn(() => { if (options.connectFailure) throw new Error('Output unavailable') }),
        disconnect: vi.fn(() => { if (options.cleanupFailure) throw new Error('Already disconnected') }),
        start: vi.fn(() => { if (options.startFailure) throw new Error('Start failed') }),
        stop: vi.fn(() => { if (options.cleanupFailure) throw new Error('Already stopped') }),
      }
      sources.push(source)
      return source
    })
    close = vi.fn(async () => { this.state = 'closed'; if (options.closeFailure) throw new Error('Already closed') })
  }
  vi.stubGlobal('AudioContext', FakeContext)
  return { contexts, sources, constructorArgs, FakeContext }
}

const players: PromptPlayer[] = []
const player = () => { const item = new PromptPlayer(); players.push(item); return item }
const flush = async () => { for (let index = 0; index < 8; index++) await Promise.resolve() }

beforeEach(() => vi.useFakeTimers())
afterEach(() => { for (const item of players.splice(0)) item.close(); vi.useRealTimers(); vi.unstubAllGlobals() })

it('uses a native-rate output context and confirms only the source ended event', async () => {
  const { contexts, sources, constructorArgs } = audio()
  const item = player(), ended = vi.fn(), failed = vi.fn()
  await item.unlock()
  await item.play('YQ==', ended, failed)
  expect(constructorArgs).toEqual([[]])
  expect(contexts[0]!.resume).not.toHaveBeenCalled()
  expect(sources[0]!.connect).toHaveBeenCalledWith(contexts[0]!.destination)
  expect(sources[0]!.start).toHaveBeenCalledTimes(1)
  expect(ended).not.toHaveBeenCalled()
  sources[0]!.onended!()
  expect(ended).toHaveBeenCalledTimes(1)
  expect(failed).not.toHaveBeenCalled()
  expect(sources[0]!.disconnect).toHaveBeenCalledTimes(1)
  expect(contexts[0]!.onstatechange).toBeNull()
  expect(vi.getTimerCount()).toBe(0)
})

it('calls resume synchronously for a user gesture before awaiting the unlock', async () => {
  const pending = deferred<void>()
  const { contexts } = audio({ state: 'suspended', resume: () => pending.promise })
  const unlocking = player().unlock()
  expect(contexts[0]!.resume).toHaveBeenCalledTimes(1)
  pending.resolve()
  await unlocking
  expect(contexts[0]!.state).toBe('running')
})

it('recreates an externally closed context on the next user unlock', async () => {
  const { contexts } = audio()
  const item = player()
  await item.unlock()
  contexts[0]!.state = 'closed'
  await item.unlock()
  expect(contexts).toHaveLength(2)
  expect(contexts[1]!.state).toBe('running')
})

it('supports the prefixed output context when the standard constructor is absent', async () => {
  const { FakeContext } = audio()
  vi.stubGlobal('AudioContext', undefined)
  vi.stubGlobal('webkitAudioContext', FakeContext)
  await expect(player().unlock()).resolves.toBeUndefined()
})

it('returns an actionable error when Web Audio is unavailable', async () => {
  vi.stubGlobal('AudioContext', undefined)
  vi.stubGlobal('webkitAudioContext', undefined)
  const failed = vi.fn()
  await player().play('YQ==', vi.fn(), failed)
  expect(failed).toHaveBeenCalledWith(expect.stringContaining('浏览器'))
})

it('does not decode or construct another context after a pending resume is cancelled', async () => {
  const pending = deferred<void>()
  const { contexts, sources } = audio({ state: 'suspended', resume: () => pending.promise })
  const item = player(), ended = vi.fn(), failed = vi.fn()
  const playing = item.play('YQ==', ended, failed)
  item.stop()
  await playing
  pending.resolve()
  await flush()
  expect(contexts).toHaveLength(1)
  expect(contexts[0]!.decodeAudioData).not.toHaveBeenCalled()
  expect(sources).toHaveLength(0)
  expect(ended).not.toHaveBeenCalled()
  expect(failed).not.toHaveBeenCalled()
  expect(vi.getTimerCount()).toBe(0)
})

it('bounds an autoplay resume that never resolves and permits a later fresh unlock', async () => {
  const pending = deferred<void>()
  const { contexts } = audio({ state: 'suspended', resume: () => pending.promise })
  const item = player(), failed = vi.fn()
  const playing = item.play('YQ==', vi.fn(), failed)
  await vi.advanceTimersByTimeAsync(5000)
  await playing
  expect(failed).toHaveBeenCalledTimes(1)
  expect(failed).toHaveBeenCalledWith(expect.stringContaining('点击继续'))
  expect(contexts[0]!.close).toHaveBeenCalledTimes(1)
  pending.resolve()
  await flush()
  await item.unlock()
  expect(contexts).toHaveLength(2)
})

it('reports denied resume without exposing the browser exception text', async () => {
  const { contexts } = audio({ state: 'suspended', resume: async () => { throw new Error('private device identifier') } })
  const failed = vi.fn()
  await player().play('YQ==', vi.fn(), failed)
  expect(failed).toHaveBeenCalledTimes(1)
  expect(failed.mock.calls[0]![0]).toContain('声音权限')
  expect(failed.mock.calls[0]![0]).not.toContain('private')
  expect(contexts[0]!.close).toHaveBeenCalledTimes(1)
})

it('cancels delayed decoding immediately and ignores its late rejection', async () => {
  const pending = deferred<{ duration: number }>()
  const { contexts, sources } = audio({ decode: () => pending.promise })
  const item = player(), ended = vi.fn(), failed = vi.fn()
  const playing = item.play('YQ==', ended, failed)
  await flush()
  expect(contexts[0]!.decodeAudioData).toHaveBeenCalledTimes(1)
  item.stop()
  await playing
  pending.reject(new Error('Late decode failure'))
  await flush()
  expect(sources).toHaveLength(0)
  expect(ended).not.toHaveBeenCalled()
  expect(failed).not.toHaveBeenCalled()
  expect(vi.getTimerCount()).toBe(0)
})

it('fails a hung decode once and never starts the late decoded audio', async () => {
  const pending = deferred<{ duration: number }>()
  const { sources } = audio({ decode: () => pending.promise })
  const failed = vi.fn()
  const playing = player().play('YQ==', vi.fn(), failed)
  await flush()
  await vi.advanceTimersByTimeAsync(15000)
  await playing
  expect(failed).toHaveBeenCalledWith(expect.stringContaining('处理超时'))
  pending.resolve({ duration: 1 })
  await flush()
  expect(sources).toHaveLength(0)
  expect(failed).toHaveBeenCalledTimes(1)
})

it.each(['suspended', 'interrupted', 'closed'])('fails once when active output becomes %s', async state => {
  const { contexts, sources } = audio()
  const item = player(), ended = vi.fn(), failed = vi.fn()
  await item.play('YQ==', ended, failed)
  const oldEnded = sources[0]!.onended!, oldStateChanged = contexts[0]!.onstatechange!
  contexts[0]!.state = state
  oldStateChanged()
  oldEnded()
  oldStateChanged()
  await vi.advanceTimersByTimeAsync(10000)
  expect(failed).toHaveBeenCalledTimes(1)
  expect(failed.mock.calls[0]![0]).toContain('点击继续')
  expect(ended).not.toHaveBeenCalled()
  expect(sources[0]!.stop).toHaveBeenCalledTimes(1)
  expect(contexts[0]!.onstatechange).toBeNull()
})

it('does not report completion if ended races an output interruption', async () => {
  const { contexts, sources } = audio()
  const ended = vi.fn(), failed = vi.fn()
  await player().play('YQ==', ended, failed)
  contexts[0]!.state = 'interrupted'
  sources[0]!.onended!()
  expect(ended).not.toHaveBeenCalled()
  expect(failed).toHaveBeenCalledTimes(1)
})

it('stopping leaves a queued source event inert and releases its watchdog', async () => {
  const { contexts, sources } = audio()
  const item = player(), ended = vi.fn(), failed = vi.fn()
  await item.play('YQ==', ended, failed)
  const oldEnded = sources[0]!.onended!, oldChanged = contexts[0]!.onstatechange!
  item.stop()
  oldEnded()
  contexts[0]!.state = 'closed'
  oldChanged()
  await vi.advanceTimersByTimeAsync(5000)
  expect(ended).not.toHaveBeenCalled()
  expect(failed).not.toHaveBeenCalled()
  expect(vi.getTimerCount()).toBe(0)
})

it('a replacement prompt stops the old source without stopping the new source', async () => {
  const { sources } = audio()
  const item = player(), oldEnded = vi.fn(), ended = vi.fn(), failed = vi.fn()
  await item.play('YQ==', oldEnded, failed)
  const stale = sources[0]!.onended!
  await item.play('Yg==', ended, failed)
  stale()
  expect(sources[0]!.stop).toHaveBeenCalledTimes(1)
  expect(sources[1]!.stop).not.toHaveBeenCalled()
  sources[1]!.onended!()
  expect(oldEnded).not.toHaveBeenCalled()
  expect(ended).toHaveBeenCalledTimes(1)
  expect(failed).not.toHaveBeenCalled()
})

it.each([{ startFailure: true }, { connectFailure: true }])('cleans up output startup failures and fails exactly once: %j', async options => {
  const { sources, contexts } = audio(options)
  const failed = vi.fn()
  await player().play('YQ==', vi.fn(), failed)
  expect(failed).toHaveBeenCalledTimes(1)
  expect(sources[0]!.onended).toBeNull()
  expect(sources[0]!.disconnect).toHaveBeenCalledTimes(1)
  expect(contexts[0]!.onstatechange).toBeNull()
  expect(vi.getTimerCount()).toBe(0)
})

it('reports a missing ended event as failure, never synthetic success', async () => {
  audio()
  const ended = vi.fn(), failed = vi.fn()
  await player().play('YQ==', ended, failed)
  await vi.advanceTimersByTimeAsync(2249)
  expect(failed).not.toHaveBeenCalled()
  await vi.advanceTimersByTimeAsync(1)
  expect(failed).toHaveBeenCalledWith(expect.stringContaining('未能播放完成'))
  expect(ended).not.toHaveBeenCalled()
})

it('stop and close remain safe when audio nodes and context reject cleanup', async () => {
  const { sources } = audio({ cleanupFailure: true, closeFailure: true })
  const item = player(), ended = vi.fn(), failed = vi.fn()
  await item.play('YQ==', ended, failed)
  expect(() => item.stop()).not.toThrow()
  expect(() => item.close()).not.toThrow()
  await flush()
  expect(sources[0]!.onended).toBeNull()
  expect(ended).not.toHaveBeenCalled()
  expect(failed).not.toHaveBeenCalled()
})

it.each(['', '%%%'])('rejects invalid encoded audio without decoding: %j', async encoded => {
  const { contexts, sources } = audio()
  const failed = vi.fn()
  await player().play(encoded, vi.fn(), failed)
  expect(contexts[0]!.decodeAudioData).not.toHaveBeenCalled()
  expect(sources).toHaveLength(0)
  expect(failed).toHaveBeenCalledWith(expect.stringContaining('数据无效'))
})

it.each([0, NaN, Infinity])('rejects an invalid decoded duration: %s', async duration => {
  const { sources } = audio({ decode: async () => ({ duration }) })
  const failed = vi.fn()
  await player().play('YQ==', vi.fn(), failed)
  expect(sources).toHaveLength(0)
  expect(failed).toHaveBeenCalledWith(expect.stringContaining('没有可播放内容'))
})


it('a resolved resume still requires the output clock to be running', async () => {
  const { contexts } = audio()
  const item = player()
  await item.unlock()
  contexts[0]!.state = 'suspended'
  contexts[0]!.resume.mockImplementationOnce(async () => {})
  const failed = vi.fn()
  await item.play('YQ==', vi.fn(), failed)
  expect(failed).toHaveBeenCalledWith(expect.stringContaining('尚未恢复'))
  expect(contexts[0]!.decodeAudioData).not.toHaveBeenCalled()
})

it('closing during decode prevents an old completion from changing a replacement prompt', async () => {
  const pending = deferred<{ duration: number }>()
  const { contexts, sources } = audio({ decode: () => pending.promise })
  const item = player(), oldFailure = vi.fn(), oldEnded = vi.fn()
  const original = item.play('YQ==', oldEnded, oldFailure)
  await flush()
  item.close()
  await original
  await item.unlock()
  contexts[1]!.decodeAudioData.mockImplementationOnce(async () => ({ duration: 0.25 }))
  const ended = vi.fn(), failed = vi.fn()
  await item.play('Yg==', ended, failed)
  pending.resolve({ duration: 10 })
  await flush()
  expect(contexts).toHaveLength(2)
  expect(sources).toHaveLength(1)
  expect(sources[0]!.start).toHaveBeenCalledTimes(1)
  sources[0]!.onended!()
  expect(ended).toHaveBeenCalledTimes(1)
  expect(oldEnded).not.toHaveBeenCalled()
  expect(oldFailure).not.toHaveBeenCalled()
  expect(failed).not.toHaveBeenCalled()
})

it('fails when output closes during decoding instead of starting a source', async () => {
  const pending = deferred<{ duration: number }>()
  const { contexts, sources } = audio({ decode: () => pending.promise })
  const ended = vi.fn(), failed = vi.fn()
  const playing = player().play('YQ==', ended, failed)
  await flush()
  contexts[0]!.state = 'closed'
  pending.resolve({ duration: 0.25 })
  await playing
  expect(sources).toHaveLength(0)
  expect(ended).not.toHaveBeenCalled()
  expect(failed).toHaveBeenCalledWith(expect.stringContaining('音频输出已中断'))
})

it('reports decoding failure and permits another prompt on the same context', async () => {
  const { contexts, sources } = audio({ decode: async () => { throw new Error('Private decoder message') } })
  const item = player(), ended = vi.fn(), failed = vi.fn()
  await item.play('YQ==', ended, failed)
  expect(failed).toHaveBeenCalledWith(expect.stringContaining('无法解码'))
  expect(failed.mock.calls[0]![0]).not.toContain('Private')
  contexts[0]!.decodeAudioData.mockImplementationOnce(async () => ({ duration: 0.25 }))
  await item.play('Yg==', ended, vi.fn())
  sources[0]!.onended!()
  expect(contexts).toHaveLength(1)
  expect(ended).toHaveBeenCalledTimes(1)
})

it('reports output constructor failures through the normal failure callback', async () => {
  vi.stubGlobal('AudioContext', class { constructor() { throw new Error('Private device error') } })
  const failed = vi.fn()
  await player().play('YQ==', vi.fn(), failed)
  expect(failed).toHaveBeenCalledTimes(1)
  expect(failed).toHaveBeenCalledWith(expect.stringContaining('音频设备'))
  expect(failed.mock.calls[0]![0]).not.toContain('Private')
})
