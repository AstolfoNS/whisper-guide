import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { PromptPlayer } from '../src/audio/player.ts'

test('stopping an audio decode prevents stale playback and completion', async () => {
  let finishDecode
  let started = 0
  let ended = 0
  globalThis.AudioContext = class {
    async resume() {}
    decodeAudioData() { return new Promise(resolve => { finishDecode = resolve }) }
    createBufferSource() { return { connect() {}, disconnect() {}, start() { started++ }, stop() {}, onended: null } }
    close() { return Promise.resolve() }
  }
  const player = new PromptPlayer()
  const pending = player.play('YQ==', 'old', () => ended++, () => {})
  await new Promise(resolve => setImmediate(resolve))
  player.stop()
  finishDecode({})
  await pending
  assert.equal(started, 0)
  assert.equal(ended, 0)
})

test('manual stop detaches completion callback', async () => {
  let source
  let completed = 0
  globalThis.AudioContext = class {
    async resume() {}
    async decodeAudioData() { return {} }
    createBufferSource() {
      source = { connect() {}, disconnect() {}, start() {}, stop() { this.onended?.() }, onended: null }
      return source
    }
    close() { return Promise.resolve() }
  }
  const player = new PromptPlayer()
  await player.play('YQ==', 'current', () => completed++, () => {})
  player.stop()
  assert.equal(completed, 0)
  assert.equal(source.onended, null)
})

test('AudioWorklet resamples 48 kHz into 20 ms PCM16 frames and flushes on disable', () => {
  let Processor
  const frames = []
  const context = vm.createContext({
    sampleRate: 48000, ArrayBuffer, DataView, Math,
    AudioWorkletProcessor: class { port = { postMessage(data) { frames.push(data) }, onmessage: null } },
    registerProcessor(_name, implementation) { Processor = implementation },
  })
  vm.runInContext(fs.readFileSync(new URL('../public/pcm-capture.js', import.meta.url), 'utf8'), context)
  const processor = new Processor()
  processor.port.onmessage({ data: { enabled: true } })
  for (let frame = 0; frame < 30; frame++) processor.process([[new Float32Array(128).fill(0.5)]])
  assert.equal(frames.length, 4)
  assert.ok(frames.every(frame => frame.byteLength === 640))
  assert.equal(new DataView(frames[0]).getInt16(0, true), 16384)
  processor.port.onmessage({ data: { enabled: false } })
  processor.process([[new Float32Array(128).fill(1)]])
  assert.equal(frames.length, 4)
})
