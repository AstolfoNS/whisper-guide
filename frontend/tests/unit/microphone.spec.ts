import { afterEach, expect, it, vi } from 'vitest'
import { Microphone } from '../../src/audio/microphone'

function capture(options: {
  supported?: Record<string, boolean>
  settings?: Record<string, unknown>
  missingOptionalApis?: boolean
  settingsError?: boolean
  resume?: () => Promise<void>
} = {}) {
  const track = {
    enabled: true, contentHint: '', onended: null, stop: vi.fn(),
    ...(options.missingOptionalApis ? {} : { getSettings: vi.fn(() => {
      if (options.settingsError) throw new Error('Settings unavailable')
      return options.settings ?? {}
    }) }),
  }
  const stream = { active: true, getTracks: () => [track], getAudioTracks: () => [track] }
  const getUserMedia = vi.fn(async () => stream)
  const processors: { port: { onmessage: ((event: MessageEvent<ArrayBuffer>) => void) | null; postMessage: ReturnType<typeof vi.fn> } }[] = []
  vi.stubGlobal('navigator', { mediaDevices: {
    getUserMedia,
    ...(options.missingOptionalApis ? {} : { getSupportedConstraints: () => options.supported ?? {} }),
  } })
  const node = () => ({ connect: vi.fn(), disconnect: vi.fn() })
  vi.stubGlobal('AudioContext', class {
    audioWorklet = { addModule: vi.fn(async () => {}) }
    destination = {}
    state = 'running'
    sampleRate = 48000
    onstatechange = null
    createMediaStreamSource = node
    createGain = () => ({ ...node(), gain: { value: 1 } })
    resume = options.resume ?? (async () => {})
    close = vi.fn(async () => {})
  })
  vi.stubGlobal('AudioWorkletNode', class {
    port = { onmessage: null as ((event: MessageEvent<ArrayBuffer>) => void) | null, postMessage: vi.fn() }
    constructor() { processors.push(this) }
    connect = vi.fn()
    disconnect = vi.fn()
  })
  return { track, getUserMedia, processors }
}

afterEach(() => vi.unstubAllGlobals())

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
