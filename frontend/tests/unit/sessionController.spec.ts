import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { SessionController } from '../../src/services/sessionController'
import { PlaybackCancelled } from '../../src/audio/player'
import { useSessionStore } from '../../src/stores/session'
import { useHealthStore } from '../../src/stores/health'
import type { SessionSnapshot, SessionServerEvent } from '../../src/api/session'

function harness(sttReady?: boolean) {
  const store = useSessionStore()
  const microphone = { isOpen: false, open: vi.fn(async () => { microphone.isOpen = true }), close: vi.fn(() => { microphone.isOpen = false }), resume: vi.fn(async () => {}), setEnabled: vi.fn() }
  const player = { unlock: vi.fn(async () => {}), stop: vi.fn(), close: vi.fn(), play: vi.fn(async () => {}) }
  const waiters: { predicate: (s: SessionSnapshot) => boolean; resolve: (s: SessionSnapshot) => void }[] = []
  let onEvent: (event: SessionServerEvent) => void = () => {}
  let current = { id: 'session-1', script_id: 's', script_name: '测试稿', state: 'READY', generation: 0, silence_timeout_ms: 3000, reading_position: 2, prompt_position: 2, reliable: true, prompt: null, feedback_id: 0, units: [], raw_transcript: '', aligned_transcript: '', stt_ready: sttReady } as SessionSnapshot
  function receive(snapshot: SessionSnapshot) { onEvent({ type: 'snapshot', session: snapshot }) }
  function emit(snapshot: SessionSnapshot) {
    current = snapshot
    receive(snapshot)
    for (const waiter of [...waiters]) if (waiter.predicate(snapshot)) { waiters.splice(waiters.indexOf(waiter), 1); waiter.resolve(snapshot) }
  }
  const client = {
    connected: false, connect: vi.fn(async () => { client.connected = true }), close: vi.fn(() => { client.connected = false }),
    waitFor: vi.fn((predicate: (s: SessionSnapshot) => boolean) => new Promise<SessionSnapshot>(resolve => waiters.push({ predicate, resolve }))),
    send: vi.fn((command: Record<string, unknown>) => {
      if (command.type === 'audio_ready' || command.type === 'configure') return
      const states: Record<string, SessionSnapshot['state']> = { start: 'LISTENING', resume: 'LISTENING', pause: 'PAUSED', terminate: 'TERMINATED' }
      if (typeof command.type === 'string' && states[command.type]) emit({ ...current, state: states[command.type]!, generation: current.generation + 1 })
    }),
    prepare: vi.fn(() => emit(current)), attach: vi.fn(() => emit({ ...current, state: 'PAUSED' })), sendAudio: vi.fn(),
  }
  const controller = new SessionController(store, { microphone, player, clientFactory: (event) => { onEvent = event; return client } })
  controller.enterStudio()
  store.selectedScript = { id: 's', name: '测试稿', content: '大家好。', character_count: 4, created_at: '' }
  return { store, microphone, player, client, controller, emit, receive, receiveEvent: (event: SessionServerEvent) => onEvent(event) }
}

beforeEach(() => { setActivePinia(createPinia()); sessionStorage.clear() })
describe('application session runtime', () => {
  it('pauses on navigation, keeps socket and positions, and only resumes by user action', async () => {
    const { store, microphone, client, controller } = harness()
    await controller.start()
    await controller.leaveStudio()
    expect(store.snapshot?.state).toBe('PAUSED')
    expect(store.snapshot?.reading_position).toBe(2)
    expect(client.close).not.toHaveBeenCalled()
    expect(microphone.isOpen).toBe(false)
    controller.enterStudio()
    expect(store.state).toBe('PAUSED')
    const opens = microphone.open.mock.calls.length
    await controller.resume()
    expect(store.state).toBe('LISTENING')
    expect(microphone.open).toHaveBeenCalledTimes(opens + 1)
    controller.dispose()
  })
  it('discards late older-generation snapshot and audio after pause', async () => {
    const { store, controller, emit, player } = harness()
    await controller.start()
    const old = { ...store.snapshot! }
    controller.pause()
    emit({ ...old, state: 'PROMPTING', prompt: { id: 'old', text: '好', start: 2, end: 3, next_position: 3 } })
    expect(store.state).toBe('PAUSED')
    expect(player.play).not.toHaveBeenCalled()
    controller.dispose()
  })
  it('does not start after a delayed microphone permission resolves on another page', async () => {
    const { controller, microphone, client } = harness()
    let resolve!: () => void
    microphone.open.mockImplementation(() => new Promise<void>(done => { resolve = done }))
    const starting = controller.start()
    await vi.waitFor(() => expect(microphone.open).toHaveBeenCalled())
    await controller.leaveStudio()
    resolve()
    await starting
    expect(client.prepare).not.toHaveBeenCalled()
    expect(microphone.isOpen).toBe(false)
    controller.dispose()
  })
  it('can attach a lower-generation different session after terminating the old one', async () => {
    const { store, controller, client, emit } = harness()
    await controller.start()
    emit({ ...store.snapshot!, generation: 20 })
    controller.terminate()
    expect(store.state).toBe('TERMINATED')
    client.attach.mockImplementation(() => emit({ ...store.snapshot!, id: 'session-2', generation: 2, state: 'PAUSED' }))
    await controller.reconnect('session-2')
    expect(store.snapshot?.id).toBe('session-2')
    expect(store.state).toBe('PAUSED')
    controller.dispose()
  })
  it('serializes recovery while the first attach is waiting for acknowledgement', async () => {
    const { store, controller, client, emit } = harness()
    client.connected = true
    client.attach.mockImplementation(() => {})
    const first = controller.reconnect('session-1')
    expect(store.busy).toBe(true)
    await controller.reconnect('session-2')
    expect(client.attach).toHaveBeenCalledTimes(1)
    expect(client.attach).toHaveBeenCalledWith('session-1')
    expect(store.recoverySessionId).toBe('session-1')
    emit({ id: 'session-1', state: 'PAUSED', generation: 0, silence_timeout_ms: 3000, feedback_id: 0 } as SessionSnapshot)
    await first
    expect(store.busy).toBe(false)
    expect(store.snapshot?.id).toBe('session-1')
    controller.dispose()
  })
  it('releases recovery protection after a connection failure so the user can retry', async () => {
    const { store, controller, client } = harness()
    client.connect.mockRejectedValueOnce(new Error('连接失败'))
    await controller.reconnect('session-1')
    expect(store.busy).toBe(false)
    expect(store.error).toBe('连接失败')
    await controller.reconnect('session-1')
    expect(client.attach).toHaveBeenCalledTimes(1)
    expect(store.snapshot?.id).toBe('session-1')
    expect(store.busy).toBe(false)
    controller.dispose()
  })
  it('does not re-enable capture when a delayed AudioContext resume finishes after pause', async () => {
    const { store, controller, microphone, client } = harness()
    let finish!: () => void
    microphone.resume.mockImplementation(() => new Promise<void>(resolve => { finish = resolve }))
    await controller.start()
    expect(store.state).toBe('LISTENING')
    controller.pause()
    finish()
    await Promise.resolve()
    expect(store.microphone).toBe('idle')
    expect(client.send).not.toHaveBeenCalledWith({ type: 'audio_ready' })
    controller.dispose()
  })
  it('stores only the session ID, not transcript or microphone audio', async () => {
    const { controller } = harness()
    await controller.start()
    expect(Object.keys(sessionStorage)).toEqual(['whisper-guide:session-id'])
    expect(sessionStorage.getItem('whisper-guide:session-id')).toBe('session-1')
    controller.dispose()
  })
})

it('waits for speech readiness, opens only one microphone and forwards each PCM callback once', async () => {
  const { controller, microphone, client, store, emit } = harness(false)
  await controller.start()
  const onFrame = microphone.open.mock.calls[0]![0] as (frame: ArrayBuffer) => void
  const frame = new ArrayBuffer(640)
  onFrame(frame)
  expect(client.sendAudio).not.toHaveBeenCalled()
  expect(microphone.setEnabled).not.toHaveBeenCalledWith(true)
  expect(client.send).not.toHaveBeenCalledWith({ type: 'audio_ready' })
  const ready = { ...store.snapshot!, stt_ready: true }
  for (let index = 0; index < 6; index++) emit(ready)
  await vi.waitFor(() => expect(store.microphone).toBe('active'))
  expect(microphone.open).toHaveBeenCalledTimes(1)
  expect(microphone.resume).toHaveBeenCalledTimes(1)
  expect(microphone.setEnabled.mock.calls.filter(([enabled]) => enabled)).toHaveLength(1)
  expect(client.send.mock.calls.filter(([command]) => command.type === 'audio_ready')).toHaveLength(1)
  for (let index = 0; index < 3; index++) onFrame(frame)
  expect(client.sendAudio).toHaveBeenCalledTimes(3)
  expect(client.sendAudio.mock.calls.every(([sent]) => sent === frame)).toBe(true)
  controller.pause()
  onFrame(frame)
  expect(client.sendAudio).toHaveBeenCalledTimes(3)
  controller.dispose()
})

it('ignores ready snapshots from before pause, older generations and another session', async () => {
  const { controller, microphone, client, store, emit, receive } = harness(false)
  await controller.start()
  const old = { ...store.snapshot!, stt_ready: true }
  controller.pause()
  receive(old)
  receive({ ...old, id: 'obsolete-session', generation: 100 })
  await Promise.resolve()
  expect(store.state).toBe('PAUSED')
  expect(microphone.setEnabled).not.toHaveBeenCalledWith(true)
  await controller.resume()
  expect(store.snapshot!.stt_ready).toBe(false)
  receive(old)
  expect(microphone.setEnabled).not.toHaveBeenCalledWith(true)
  emit({ ...store.snapshot!, stt_ready: true })
  await vi.waitFor(() => expect(store.microphone).toBe('active'))
  expect(client.send.mock.calls.filter(([command]) => command.type === 'audio_ready')).toHaveLength(1)
  controller.dispose()
})

it('revokes a pending capture enable if speech readiness changes during AudioContext resume', async () => {
  const { controller, microphone, client, store, emit } = harness(false)
  await controller.start()
  let finish!: () => void
  microphone.resume.mockImplementationOnce(() => new Promise<void>(resolve => { finish = resolve }))
  emit({ ...store.snapshot!, stt_ready: true })
  await vi.waitFor(() => expect(microphone.resume).toHaveBeenCalledTimes(1))
  emit({ ...store.snapshot!, stt_ready: false })
  finish()
  await Promise.resolve()
  expect(microphone.setEnabled).not.toHaveBeenCalledWith(true)
  expect(client.send).not.toHaveBeenCalledWith({ type: 'audio_ready' })
  emit({ ...store.snapshot!, stt_ready: true })
  await vi.waitFor(() => expect(store.microphone).toBe('active'))
  expect(microphone.open).toHaveBeenCalledTimes(1)
  expect(client.send.mock.calls.filter(([command]) => command.type === 'audio_ready')).toHaveLength(1)
  controller.dispose()
})

it('shares one in-flight microphone open across repeated ready snapshots and generation changes', async () => {
  const { controller, microphone, client, store, emit } = harness(false)
  client.connected = true
  let finish!: () => void
  microphone.open.mockImplementation(() => new Promise<void>(resolve => { finish = () => { microphone.isOpen = true; resolve() } }))
  const current = { id: 'pending-open', state: 'LISTENING', generation: 1, stt_ready: true, silence_timeout_ms: 3000, feedback_id: 0 } as SessionSnapshot
  emit(current)
  emit({ ...current, generation: 2 })
  emit({ ...current, generation: 2 })
  expect(microphone.open).toHaveBeenCalledTimes(1)
  finish()
  await vi.waitFor(() => expect(store.microphone).toBe('active'))
  expect(store.snapshot?.generation).toBe(2)
  expect(microphone.resume).toHaveBeenCalledTimes(1)
  expect(client.send.mock.calls.filter(([command]) => command.type === 'audio_ready')).toHaveLength(1)
  controller.dispose()
})

it('stops forwarding PCM while a new ready generation is enabling capture', async () => {
  const { controller, microphone, client, store, emit } = harness(true)
  await controller.start()
  await vi.waitFor(() => expect(store.microphone).toBe('active'))
  const onFrame = microphone.open.mock.calls[0]![0] as (frame: ArrayBuffer) => void
  const frame = new ArrayBuffer(640)
  onFrame(frame)
  emit({ ...store.snapshot!, generation: store.snapshot!.generation + 1 })
  onFrame(frame)
  expect(client.sendAudio).toHaveBeenCalledTimes(1)
  await vi.waitFor(() => expect(store.microphone).toBe('active'))
  onFrame(frame)
  expect(client.sendAudio).toHaveBeenCalledTimes(2)
  expect(microphone.open).toHaveBeenCalledTimes(1)
  expect(client.send.mock.calls.filter(([command]) => command.type === 'audio_ready')).toHaveLength(2)
  controller.dispose()
})

it('starts capture without waiting for output unlock and records its failure separately', async () => {
  const { controller, microphone, player, store } = harness()
  let failOutput!: (reason: Error) => void
  player.unlock.mockImplementationOnce(() => new Promise<void>((_resolve, reject) => { failOutput = reject }))
  await controller.start()
  await vi.waitFor(() => expect(store.microphone).toBe('active'))
  expect(store.busy).toBe(false)
  expect(microphone.open).toHaveBeenCalledTimes(1)
  failOutput(new Error('音频输出不可用'))
  await vi.waitFor(() => expect(useHealthStore().playback).toBe('failed'))
  expect(useHealthStore().playbackDetail).toBe('音频输出不可用')
  expect(store.state).toBe('LISTENING')
  controller.dispose()
})

it('retries output from the resume gesture and resets the previous playback result', async () => {
  const { controller, player, store } = harness()
  useHealthStore().playback = 'success'
  await controller.start()
  expect(useHealthStore().playback).toBe('unknown')
  controller.pause()
  player.unlock.mockRejectedValueOnce(new Error('系统暂停了输出'))
  await controller.resume()
  await vi.waitFor(() => expect(useHealthStore().playback).toBe('failed'))
  expect(store.state).toBe('LISTENING')
  expect(store.microphone).toBe('active')
  controller.dispose()
})

it('retains microphone failure state after pausing and releases resources', async () => {
  const { controller, microphone, store } = harness()
  await controller.start()
  await vi.waitFor(() => expect(store.microphone).toBe('active'))
  const fail = microphone.open.mock.calls[0]![1] as (reason: string) => void
  fail('麦克风已中断')
  expect(store.state).toBe('PAUSED')
  expect(store.microphone).toBe('error')
  expect(store.error).toBe('麦克风已中断')
  expect(microphone.isOpen).toBe(false)
  controller.dispose()
})

it('shows a microphone opening error without leaving the session busy', async () => {
  const { controller, microphone, store } = harness()
  microphone.open.mockRejectedValueOnce(new Error('输入设备采样率不兼容'))
  await controller.start()
  expect(store.busy).toBe(false)
  expect(store.microphone).toBe('error')
  expect(store.error).toBe('输入设备采样率不兼容')
  controller.dispose()
})

it('reports playback failure with a reason and never sends playback_finished', async () => {
  const { controller, player, store, receiveEvent, emit, client } = harness()
  await controller.start()
  const prompt = { id: 'p-1', text: '大家好', start: 0, end: 3, next_position: 3 }
  emit({ ...store.snapshot!, state: 'PROMPTING', generation: store.snapshot!.generation + 1, prompt })
  receiveEvent({ type: 'audio', prompt_id: prompt.id, mime_type: 'audio/mpeg', data: 'YQ==' })
  expect(useHealthStore().playback).toBe('pending')
  const fail = player.play.mock.calls[0]![2] as (reason: string) => void
  fail('浏览器已暂停音频输出，请返回页面后继续。')
  expect(useHealthStore().playback).toBe('failed')
  expect(useHealthStore().playbackDetail).toContain('浏览器已暂停')
  expect(client.send).toHaveBeenCalledWith({ type: 'playback_failed', prompt_id: prompt.id })
  expect(client.send).not.toHaveBeenCalledWith({ type: 'playback_finished', prompt_id: prompt.id })
  controller.dispose()
})

it('marks interrupted playback as cancelled and ignores late completion', async () => {
  const { controller, player, store, receiveEvent, emit, client } = harness()
  await controller.start()
  const prompt = { id: 'p-2', text: '大家好', start: 0, end: 3, next_position: 3 }
  emit({ ...store.snapshot!, state: 'PROMPTING', generation: store.snapshot!.generation + 1, prompt })
  receiveEvent({ type: 'audio', prompt_id: prompt.id, mime_type: 'audio/mpeg', data: 'YQ==' })
  const done = player.play.mock.calls[0]![1] as () => void
  controller.pause()
  done()
  expect(useHealthStore().playback).toBe('stopped')
  expect(client.send).not.toHaveBeenCalledWith({ type: 'playback_finished', prompt_id: prompt.id })
  controller.dispose()
})

it('does not reuse a cancelled microphone opening when immediately resuming', async () => {
  const { controller, microphone, store, emit } = harness(false)
  await controller.start()
  microphone.isOpen = false
  let finishOld!: () => void
  microphone.open.mockImplementationOnce(() => new Promise<void>(resolve => { finishOld = resolve }))
  emit({ ...store.snapshot!, stt_ready: true })
  expect(microphone.open).toHaveBeenCalledTimes(2)
  controller.pause()
  const resuming = controller.resume()
  expect(microphone.open).toHaveBeenCalledTimes(3)
  finishOld()
  await resuming
  await vi.waitFor(() => expect(store.microphone).toBe('active'))
  expect(store.state).toBe('LISTENING')
  controller.dispose()
})

it('preserves input failure after a delayed server pause acknowledgement', async () => {
  const { controller, microphone, store, client, emit } = harness()
  await controller.start()
  await vi.waitFor(() => expect(store.microphone).toBe('active'))
  const listening = { ...store.snapshot! }
  client.send.mockImplementation(() => {})
  const fail = microphone.open.mock.calls[0]![1] as (reason: string) => void
  fail('输入设备已断开')
  expect(store.microphone).toBe('error')
  emit({ ...listening, state: 'PAUSED', generation: listening.generation + 1 })
  expect(store.microphone).toBe('error')
  expect(store.error).toBe('输入设备已断开')
  controller.dispose()
})

it('does not acknowledge capture if enabling it synchronously reports a device failure', async () => {
  const { controller, microphone, client, store } = harness()
  microphone.setEnabled.mockImplementation(enabled => {
    if (!enabled) return
    const fail = microphone.open.mock.calls[0]![1] as (reason: string) => void
    fail('麦克风音频中断')
  })
  await controller.start()
  await vi.waitFor(() => expect(store.state).toBe('PAUSED'))
  expect(store.microphone).toBe('error')
  expect(client.send).not.toHaveBeenCalledWith({ type: 'audio_ready' })
  controller.dispose()
})

it('does not report routine output unlock cancellation as a device failure', async () => {
  const { controller, player } = harness()
  player.unlock.mockRejectedValueOnce(new PlaybackCancelled())
  await controller.start()
  expect(useHealthStore().playback).toBe('unknown')
  expect(useHealthStore().playbackDetail).toBe('')
  controller.dispose()
})

it('pauses instead of acknowledging an input graph closed while resume was waiting', async () => {
  const { controller, microphone, client, store } = harness()
  microphone.resume.mockImplementationOnce(async () => { microphone.isOpen = false })
  await controller.start()
  await vi.waitFor(() => expect(store.state).toBe('PAUSED'))
  expect(store.microphone).toBe('error')
  expect(client.send).not.toHaveBeenCalledWith({ type: 'audio_ready' })
  controller.dispose()
})

it('publishes only input rate and channel metadata while capture is open', async () => {
  const { controller, microphone } = harness()
  Object.assign(microphone, { contextSampleRate: 48000, processingSettings: { sampleRate: 48000, channelCount: 2 } })
  await controller.start()
  expect(useHealthStore().captureInputRate).toBe(48000)
  expect(useHealthStore().captureContextRate).toBe(48000)
  expect(useHealthStore().captureChannels).toBe(2)
  controller.pause()
  expect(useHealthStore().captureInputRate).toBe(0)
  controller.dispose()
})

it('accepts only current-session listening diagnostics and ignores stale heartbeat events', async () => {
  const { controller, store, receiveEvent } = harness()
  await controller.start()
  const heartbeat = { type: 'audio_diagnostics' as const, session_id: store.snapshot!.id, generation: store.snapshot!.generation,
    diagnostics: { prompt_wait_reason: 'silence_wait' as const, silence_elapsed_ms: 600, audio_queued_ms: 0 } }
  receiveEvent(heartbeat)
  expect(store.snapshot!.silence_elapsed_ms).toBe(600)
  receiveEvent({ ...heartbeat, generation: heartbeat.generation - 1, diagnostics: { silence_elapsed_ms: 900 } })
  receiveEvent({ ...heartbeat, session_id: 'another', diagnostics: { silence_elapsed_ms: 1000 } })
  expect(store.snapshot!.silence_elapsed_ms).toBe(600)
  controller.pause()
  receiveEvent({ ...heartbeat, generation: store.snapshot!.generation, diagnostics: { silence_elapsed_ms: 3000 } })
  expect(store.snapshot!.silence_elapsed_ms).toBe(600)
  controller.dispose()
})
