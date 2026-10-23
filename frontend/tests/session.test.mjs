import test from 'node:test'
import assert from 'node:assert/strict'
import { SessionClient } from '../src/api/session.ts'

class Socket {
  static OPEN = 1
  readyState = 0
  bufferedAmount = 0
  sent = []
  listeners = {}
  constructor(url) { this.url = url; Socket.last = this; queueMicrotask(() => { this.readyState = 1; this.onopen?.() }) }
  addEventListener(name, callback) { this.listeners[name] = callback }
  send(data) { this.sent.push(data) }
  close() { this.readyState = 3; this.onclose?.(); this.listeners.close?.() }
  message(data) { this.onmessage?.({ data: JSON.stringify(data) }) }
}
globalThis.WebSocket = Socket
globalThis.window = globalThis
globalThis.location = { href: 'http://localhost:5173/' }

test('session client prepares fixed script, waits for acknowledgement and uses expected socket URL', async () => {
  const events = []
  const client = new SessionClient(event => events.push(event), () => {})
  await client.connect()
  assert.equal(Socket.last.url, 'ws://localhost:5173/api/session')
  const waiting = client.waitFor(session => session.state === 'READY')
  client.prepare('script-id', 1250)
  assert.deepEqual(JSON.parse(Socket.last.sent[0]), { type: 'prepare', script_id: 'script-id', silence_timeout_ms: 1250, stt_provider: 'local' })
  Socket.last.message({ type: 'snapshot', session: { state: 'READY', id: 'session-id' } })
  assert.equal((await waiting).id, 'session-id')
  client.close()
})

test('server errors reject pending state acknowledgement', async () => {
  const client = new SessionClient(() => {}, () => {})
  await client.connect()
  const waiting = client.waitFor(() => false)
  Socket.last.message({ type: 'error', message: '模型未就绪' })
  await assert.rejects(waiting, /模型未就绪/)
  client.close()
})

test('events from old connection do not modify a replacement connection', async () => {
  const events = []
  const client = new SessionClient(event => events.push(event), () => {})
  await client.connect()
  const old = Socket.last
  const oldCallback = old.onmessage
  await client.connect()
  oldCallback({ data: JSON.stringify({ type: 'error', message: 'stale' }) })
  assert.equal(events.length, 0)
  client.close()
})
