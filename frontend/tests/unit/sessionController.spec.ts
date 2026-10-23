import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { SessionController } from '../../src/services/sessionController'
import { useSessionStore } from '../../src/stores/session'
import type { SessionSnapshot, SessionServerEvent } from '../../src/api/session'

function harness() {
  const store = useSessionStore()
  const microphone = { isOpen: false, open: vi.fn(async () => { microphone.isOpen = true }), close: vi.fn(() => { microphone.isOpen = false }), resume: vi.fn(async () => {}), setEnabled: vi.fn() }
  const player = { unlock: vi.fn(async () => {}), stop: vi.fn(), close: vi.fn(), play: vi.fn(async () => {}) }
  const waiters: { predicate: (s: SessionSnapshot) => boolean; resolve: (s: SessionSnapshot) => void }[] = []
  let onEvent: (event: SessionServerEvent) => void = () => {}
  let current = { id: 'session-1', script_id: 's', script_name: '测试稿', state: 'READY', generation: 0, silence_timeout_ms: 3000, reading_position: 2, prompt_position: 2, reliable: true, prompt: null, feedback_id: 0, units: [], raw_transcript: '', aligned_transcript: '' } as SessionSnapshot
  function emit(snapshot: SessionSnapshot) {
    current = snapshot
    onEvent({ type: 'snapshot', session: snapshot })
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
  return { store, microphone, player, client, controller, emit }
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
