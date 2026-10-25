import { afterEach, expect, it, vi } from 'vitest'
import { Microphone } from '../../src/audio/microphone'

interface TestNode { connect: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn> }
interface TestProcessor extends TestNode {
  port: { onmessage: ((event: MessageEvent<ArrayBuffer>) => void) | null; postMessage: ReturnType<typeof vi.fn> }
  onprocessorerror: (() => void) | null
}
interface TestContext {
  sampleRate: number; state: string; onstatechange: (() => void) | null
  createMediaStreamSource: ReturnType<typeof vi.fn>; resume: () => Promise<void>; close: ReturnType<typeof vi.fn>
  audioWorklet: { addModule: ReturnType<typeof vi.fn> }
}
function capture(options: {
  supported?: Record<string, boolean>
  settings?: Record<string, unknown>
  missingOptionalApis?: boolean
  settingsError?: boolean
  resume?: () => Promise<void>
  contextError?: (init: AudioContextOptions) => Error | undefined
  sourceError?: (init: AudioContextOptions, index: number) => Error | undefined
  workletError?: Error
  loadModule?: () => Promise<void>
  defaultRate?: number
  closeError?: boolean
  noTracks?: boolean
  ended?: boolean
} = {}) {
  const track = {
    enabled: true, muted: false, readyState: options.ended ? 'ended' : 'live', contentHint: '',
    onended: null as (() => void) | null, onmute: null as (() => void) | null, stop: vi.fn(),
    ...(options.missingOptionalApis ? {} : { getSettings: vi.fn(() => {
      if (options.settingsError) throw new Error('Settings unavailable')
      return options.settings ?? {}
    }) }),
  }
  const stream = { active: true, getTracks: () => [track], getAudioTracks: () => options.noTracks ? [] : [track] }
  const getUserMedia = vi.fn(async () => stream)
  const processors: TestProcessor[] = []
  const contexts: TestContext[] = []
  const contextOptions: AudioContextOptions[] = []
  const workletOptions: AudioWorkletNodeOptions[] = []
  vi.stubGlobal('navigator', { mediaDevices: {
    getUserMedia,
    ...(options.missingOptionalApis ? {} : { getSupportedConstraints: () => options.supported ?? {} }),
  } })
  const node = (): TestNode => ({ connect: vi.fn(), disconnect: vi.fn() })
  vi.stubGlobal('AudioContext', class {
    audioWorklet = { addModule: vi.fn(options.loadModule ?? (async () => { if (options.workletError) throw options.workletError })) }
    destination = {}
    state = 'running'
    sampleRate: number
    onstatechange = null as (() => void) | null
    createMediaStreamSource: ReturnType<typeof vi.fn>
    createGain = () => ({ ...node(), gain: { value: 1 } })
    resume = options.resume ?? (async () => {})
    close = vi.fn(async () => {
      this.state = 'closed'
      if (options.closeError) throw new Error('close failed')
    })
    constructor(init: AudioContextOptions) {
      contextOptions.push(init)
      const error = options.contextError?.(init)
      if (error) throw error
      this.sampleRate = init.sampleRate ?? options.defaultRate ?? 48000
      const index = contexts.length
      this.createMediaStreamSource = vi.fn(() => {
        const error = options.sourceError?.(init, index)
        if (error) throw error
        return node()
      })
      contexts.push(this)
    }
  })
  vi.stubGlobal('AudioWorkletNode', class {
    port = { onmessage: null as ((event: MessageEvent<ArrayBuffer>) => void) | null, postMessage: vi.fn() }
    onprocessorerror = null as (() => void) | null
    constructor(_context: unknown, _name: string, init: AudioWorkletNodeOptions) { processors.push(this); workletOptions.push(init) }
    connect = vi.fn()
    disconnect = vi.fn()
  })
  return { track, getUserMedia, processors, contexts, contextOptions, workletOptions }
}

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

it('requests speech processing and voice isolation only when supported', async () => {
  const { track, getUserMedia } = capture({ supported: { voiceIsolation: true } })
  const microphone = new Microphone()
  await microphone.open(vi.fn(), vi.fn())
  expect(getUserMedia).toHaveBeenCalledWith({ audio: {
    channelCount: { ideal: 1 }, echoCancellation: { ideal: true },
    noiseSuppression: { ideal: true }, autoGainControl: { ideal: true }, voiceIsolation: { ideal: true },
  } })
  expect(track.contentHint).toBe('speech')
  microphone.close()
})

it('keeps actual processing settings separately from requests without recording device identifiers', async () => {
  capture({ settings: {
    echoCancellation: true, noiseSuppression: false, autoGainControl: false,
    voiceIsolation: false, channelCount: 2, sampleRate: 48000, deviceId: 'private-device', groupId: 'private-group',
  } })
  const microphone = new Microphone()
  await microphone.open(vi.fn(), vi.fn())
  expect(microphone.processingSettings).toEqual({
    echoCancellation: true, noiseSuppression: false, autoGainControl: false,
    voiceIsolation: false, channelCount: 2, sampleRate: 48000,
  })
  expect(microphone.processingSettings).not.toHaveProperty('deviceId')
  const copy = microphone.processingSettings as { noiseSuppression?: boolean }
  copy.noiseSuppression = true
  expect(microphone.processingSettings?.noiseSuppression).toBe(false)
  microphone.close()
  expect(microphone.processingSettings).toBeUndefined()
})

it('opens capture when optional constraints and settings APIs are absent', async () => {
  const { getUserMedia } = capture({ missingOptionalApis: true })
  const microphone = new Microphone()
  await microphone.open(vi.fn(), vi.fn())
  expect(microphone.isOpen).toBe(true)
  expect(getUserMedia.mock.calls[0]?.[0]).not.toHaveProperty('audio.voiceIsolation')
  expect(microphone.processingSettings).toBeUndefined()
  microphone.close()
})

it('does not treat unavailable settings diagnostics as microphone failure', async () => {
  capture({ settingsError: true })
  const microphone = new Microphone()
  const failure = vi.fn()
  await microphone.open(vi.fn(), failure)
  expect(microphone.isOpen).toBe(true)
  expect(failure).not.toHaveBeenCalled()
  expect(microphone.processingSettings).toBeUndefined()
  microphone.close()
})

it('does not publish settings or keep a track from an open operation cancelled during resume', async () => {
  let finishResume!: () => void
  const resume = vi.fn(() => new Promise<void>(resolve => { finishResume = resolve }))
  const { track } = capture({ resume, settings: { noiseSuppression: true } })
  const microphone = new Microphone()
  const opening = microphone.open(vi.fn(), vi.fn())
  await vi.waitFor(() => expect(resume).toHaveBeenCalled())
  microphone.close()
  finishResume()
  await opening
  expect(microphone.isOpen).toBe(false)
  expect(microphone.processingSettings).toBeUndefined()
  expect(track.stop).toHaveBeenCalled()
})


it('keeps tracks disabled until enabled and does not flush processing on duplicate enable calls', async () => {
  const { track, processors } = capture()
  const microphone = new Microphone()
  await microphone.open(vi.fn(), vi.fn())
  expect(track.enabled).toBe(false)
  expect(microphone.contextSampleRate).toBe(48000)
  microphone.setEnabled(true)
  microphone.setEnabled(true)
  expect(track.enabled).toBe(true)
  microphone.setEnabled(false)
  microphone.setEnabled(false)
  expect(processors[0]!.port.postMessage.mock.calls).toEqual([[{ enabled: true }], [{ enabled: false }]])
  expect(track.enabled).toBe(false)
  microphone.close()
})

it('rejects queued PCM and ended callbacks from a microphone operation that was replaced', async () => {
  const { processors, track } = capture()
  const microphone = new Microphone()
  const oldFrame = vi.fn(), currentFrame = vi.fn(), failure = vi.fn()
  await microphone.open(oldFrame, failure)
  microphone.setEnabled(true)
  const oldPort = processors[0]!.port.onmessage!
  const oldEnded = track.onended as unknown as () => void
  await microphone.open(currentFrame, failure)
  microphone.setEnabled(true)
  const event = { data: new ArrayBuffer(640) } as MessageEvent<ArrayBuffer>
  oldPort(event)
  oldEnded()
  processors[1]!.port.onmessage!(event)
  expect(oldFrame).not.toHaveBeenCalled()
  expect(failure).not.toHaveBeenCalled()
  expect(currentFrame).toHaveBeenCalledTimes(1)
  microphone.close()
})


it.each([16000, 44100, 48000, 96000])('matches a reported %i Hz input rate before resampling in the worklet', async rate => {
  const { contextOptions, contexts, workletOptions } = capture({ settings: { sampleRate: rate } })
  const microphone = new Microphone()
  await microphone.open(vi.fn(), vi.fn())
  expect(contextOptions).toEqual([{ latencyHint: 'interactive', sampleRate: rate }])
  expect(microphone.contextSampleRate).toBe(rate)
  expect(contexts[0]!.createMediaStreamSource).toHaveBeenCalledTimes(1)
  expect(workletOptions[0]).toMatchObject({ numberOfInputs: 1, channelCountMode: 'max' })
  microphone.close()
})

it.each([undefined, 0, -1, NaN, Infinity, '48000'])('uses the browser default when the track rate is unavailable or invalid (%s)', async sampleRate => {
  const { contextOptions } = capture({ settings: { sampleRate } })
  const microphone = new Microphone()
  await microphone.open(vi.fn(), vi.fn())
  expect(contextOptions).toEqual([{ latencyHint: 'interactive' }])
  expect(microphone.contextSampleRate).toBe(48000)
  microphone.close()
})

it('releases a mismatched context before retrying the same stream at the browser default', async () => {
  const { contexts, contextOptions, getUserMedia, track } = capture({ settings: { sampleRate: 44100 }, sourceError: init =>
    init.sampleRate ? new DOMException('Connecting AudioNodes from AudioContexts with different sample-rate is currently not supported', 'NotSupportedError') : undefined,
  })
  const microphone = new Microphone()
  await microphone.open(vi.fn(), vi.fn())
  expect(contextOptions).toEqual([{ latencyHint: 'interactive', sampleRate: 44100 }, { latencyHint: 'interactive' }])
  expect(contexts[0]!.close).toHaveBeenCalledTimes(1)
  expect(contexts[1]!.close).not.toHaveBeenCalled()
  expect(getUserMedia).toHaveBeenCalledTimes(1)
  expect(track.stop).not.toHaveBeenCalled()
  expect(microphone.contextSampleRate).toBe(48000)
  microphone.close()
})

it('falls back to the browser default when the explicit input rate is not supported by AudioContext', async () => {
  const { contextOptions } = capture({ settings: { sampleRate: 8000 }, contextError: init =>
    init.sampleRate ? new DOMException('Unsupported sampleRate', 'NotSupportedError') : undefined,
  })
  const microphone = new Microphone()
  await microphone.open(vi.fn(), vi.fn())
  expect(contextOptions).toEqual([{ latencyHint: 'interactive', sampleRate: 8000 }, { latencyHint: 'interactive' }])
  expect(microphone.contextSampleRate).toBe(48000)
  microphone.close()
})

it('refreshes the input rate once if the default context also mismatches after a device change', async () => {
  const harness = capture({ settings: { sampleRate: 44100 }, sourceError: (_init, index) =>
    index < 2 ? new DOMException('different sample-rate', 'NotSupportedError') : undefined,
  })
  harness.track.getSettings!.mockImplementationOnce(() => ({ sampleRate: 44100 })).mockImplementation(() => ({ sampleRate: 48000 }))
  const microphone = new Microphone()
  await microphone.open(vi.fn(), vi.fn())
  expect(harness.contextOptions).toEqual([{ latencyHint: 'interactive', sampleRate: 44100 }, { latencyHint: 'interactive' }, { latencyHint: 'interactive', sampleRate: 48000 }])
  expect(harness.contexts.slice(0, 2).every(context => context.close.mock.calls.length === 1)).toBe(true)
  expect(microphone.contextSampleRate).toBe(48000)
  microphone.close()
})

it('bounds incompatible-rate retries and releases every context and track even if close rejects', async () => {
  const { contexts, contextOptions, track } = capture({ settings: { sampleRate: 44100 }, closeError: true, sourceError: () =>
    new DOMException('different sample-rate', 'NotSupportedError'),
  })
  const microphone = new Microphone()
  await expect(microphone.open(vi.fn(), vi.fn())).rejects.toThrow('无法匹配麦克风采样率')
  expect(contextOptions).toHaveLength(2)
  expect(contexts.every(context => context.close.mock.calls.length === 1)).toBe(true)
  expect(track.stop).toHaveBeenCalled()
  expect(microphone.isOpen).toBe(false)
  expect(microphone.processingSettings).toBeUndefined()
})

it('does not retry unrelated source failures or worklet loading errors as sampling-rate failures', async () => {
  for (const options of [{ sourceError: () => new DOMException('stream invalid', 'InvalidStateError') }, { workletError: new DOMException('module unavailable', 'NotSupportedError') }]) {
    const { contexts, contextOptions, track } = capture(options)
    const microphone = new Microphone()
    await expect(microphone.open(vi.fn(), vi.fn())).rejects.toBeInstanceOf(DOMException)
    expect(contextOptions).toHaveLength(1)
    expect(contexts[0]!.close).toHaveBeenCalledTimes(1)
    expect(track.stop).toHaveBeenCalled()
    expect(microphone.isOpen).toBe(false)
  }
})

it.each(['suspended', 'interrupted', 'closed'])('stops frames, releases capture and reports %s exactly once', async state => {
  const { contexts, processors, track } = capture()
  const microphone = new Microphone(), onFrame = vi.fn(), failure = vi.fn()
  await microphone.open(onFrame, failure)
  microphone.setEnabled(true)
  const oldPort = processors[0]!.port.onmessage!, change = contexts[0]!.onstatechange!
  contexts[0]!.state = state
  change()
  change()
  oldPort({ data: new ArrayBuffer(640) } as MessageEvent<ArrayBuffer>)
  expect(failure).toHaveBeenCalledTimes(1)
  expect(onFrame).not.toHaveBeenCalled()
  expect(track.stop).toHaveBeenCalled()
  expect(microphone.isOpen).toBe(false)
})

it.each(['ended', 'muted', 'processor'])('releases capture on %s instead of continuing to send stale input', async event => {
  const { track, processors } = capture()
  const microphone = new Microphone(), failure = vi.fn()
  await microphone.open(vi.fn(), failure)
  microphone.setEnabled(true)
  const handler = event === 'ended' ? track.onended! : event === 'muted' ? track.onmute! : processors[0]!.onprocessorerror!
  handler()
  handler()
  expect(failure).toHaveBeenCalledTimes(1)
  expect(track.stop).toHaveBeenCalled()
  expect(microphone.isOpen).toBe(false)
})

it('does not treat suspension while capture is disabled as a new device failure', async () => {
  const { contexts } = capture()
  const microphone = new Microphone(), failure = vi.fn()
  await microphone.open(vi.fn(), failure)
  contexts[0]!.state = 'suspended'
  contexts[0]!.onstatechange!()
  expect(failure).not.toHaveBeenCalled()
  microphone.close()
})

it.each([{ noTracks: true }, { ended: true }])('rejects an absent or ended input track and releases the returned stream (%o)', async options => {
  const { track, contexts } = capture(options)
  const microphone = new Microphone()
  await expect(microphone.open(vi.fn(), vi.fn())).rejects.toThrow('没有可用音轨')
  expect(contexts).toHaveLength(0)
  expect(track.stop).toHaveBeenCalled()
})

it('closes a graph immediately when cancelled during resume, before the resume promise settles', async () => {
  let complete!: () => void
  const resume = vi.fn(() => new Promise<void>(resolve => { complete = resolve }))
  const { contexts, track } = capture({ resume })
  const microphone = new Microphone()
  const opening = microphone.open(vi.fn(), vi.fn())
  await vi.waitFor(() => expect(resume).toHaveBeenCalled())
  microphone.close()
  expect(track.stop).toHaveBeenCalled()
  expect(contexts[0]!.close).toHaveBeenCalledTimes(1)
  complete()
  await opening
  expect(microphone.isOpen).toBe(false)
  expect(contexts[0]!.close).toHaveBeenCalledTimes(1)
})


it('bounds a never-settling browser resume and releases the stream when startup expires', async () => {
  vi.useFakeTimers()
  const { track, contexts } = capture({ resume: () => new Promise<void>(() => {}) })
  const microphone = new Microphone()
  const opening = microphone.open(vi.fn(), vi.fn())
  const rejected = expect(opening).rejects.toThrow('启动麦克风音频超时')
  await vi.advanceTimersByTimeAsync(5000)
  await rejected
  expect(track.stop).toHaveBeenCalled()
  expect(contexts[0]!.close).toHaveBeenCalledTimes(1)
  expect(microphone.isOpen).toBe(false)
})

it('cancels a never-settling resume without waiting for its timeout or reporting a device failure', async () => {
  const resume = vi.fn(() => new Promise<void>(() => {}))
  const { track, contexts } = capture({ resume })
  const microphone = new Microphone(), failure = vi.fn()
  const opening = microphone.open(vi.fn(), failure)
  await vi.waitFor(() => expect(resume).toHaveBeenCalled())
  microphone.close()
  await expect(opening).resolves.toBeUndefined()
  expect(failure).not.toHaveBeenCalled()
  expect(track.stop).toHaveBeenCalled()
  expect(contexts[0]!.close).toHaveBeenCalledTimes(1)
})


it('bounds an already-open context resume and allows cancellation without a late failure', async () => {
  const { contexts } = capture()
  const microphone = new Microphone()
  await microphone.open(vi.fn(), vi.fn())
  contexts[0]!.resume = () => new Promise<void>(() => {})
  vi.useFakeTimers()
  const resumed = microphone.resume()
  const rejected = expect(resumed).rejects.toThrow('恢复麦克风音频超时')
  await vi.advanceTimersByTimeAsync(5000)
  await rejected
  const cancelled = microphone.resume()
  microphone.close()
  await expect(cancelled).resolves.toBeUndefined()
})

it('bounds a never-resolving worklet module load and releases its graph', async () => {
  vi.useFakeTimers()
  const { contexts, track } = capture({ loadModule: () => new Promise<void>(() => {}) })
  const microphone = new Microphone()
  const opening = microphone.open(vi.fn(), vi.fn())
  const rejected = expect(opening).rejects.toThrow('模块加载超时')
  await vi.advanceTimersByTimeAsync(5000)
  await rejected
  expect(contexts[0]!.close).toHaveBeenCalledTimes(1)
  expect(track.stop).toHaveBeenCalled()
})

it('cancels an unfinished module load immediately and does not install a stale worklet', async () => {
  let complete!: () => void
  const loadModule = vi.fn(() => new Promise<void>(resolve => { complete = resolve }))
  const { contexts, processors, track } = capture({ loadModule })
  const microphone = new Microphone(), failure = vi.fn()
  const opening = microphone.open(vi.fn(), failure)
  await vi.waitFor(() => expect(loadModule).toHaveBeenCalled())
  microphone.close()
  await expect(opening).resolves.toBeUndefined()
  complete()
  await Promise.resolve()
  expect(processors).toHaveLength(0)
  expect(contexts[0]!.close).toHaveBeenCalledTimes(1)
  expect(track.stop).toHaveBeenCalled()
  expect(failure).not.toHaveBeenCalled()
})
