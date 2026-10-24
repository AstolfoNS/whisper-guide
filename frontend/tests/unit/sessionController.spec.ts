import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { SessionController } from '../../src/services/sessionController'
import { useSessionStore } from '../../src/stores/session'
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
  return { store, microphone, player, client, controller, emit, receive }
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
