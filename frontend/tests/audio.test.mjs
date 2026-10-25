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
    state = 'running'
    async resume() {}
    decodeAudioData() { return new Promise(resolve => { finishDecode = resolve }) }
    createBufferSource() { return { connect() {}, disconnect() {}, start() { started++ }, stop() {}, onended: null } }
    close() { return Promise.resolve() }
  }
  const player = new PromptPlayer()
  const pending = player.play('YQ==', () => ended++, () => {})
  await new Promise(resolve => setImmediate(resolve))
  player.stop()
  finishDecode({ duration: 0.1 })
  await pending
  assert.equal(started, 0)
  assert.equal(ended, 0)
})

test('manual stop detaches completion callback', async () => {
  let source
  let completed = 0
  globalThis.AudioContext = class {
    state = 'running'
    async resume() {}
    async decodeAudioData() { return { duration: 0.1 } }
    createBufferSource() {
      source = { connect() {}, disconnect() {}, start() {}, stop() { this.onended?.() }, onended: null }
      return source
    }
    close() { return Promise.resolve() }
  }
  const player = new PromptPlayer()
  await player.play('YQ==', () => completed++, () => {})
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


function workletAt(sampleRate) {
  let Processor
  const frames = []
  const context = vm.createContext({ sampleRate, ArrayBuffer, DataView, Math,
    AudioWorkletProcessor: class { port = { postMessage(frame) { frames.push(frame) }, onmessage: null } },
    registerProcessor(_name, implementation) { Processor = implementation },
  })
  vm.runInContext(fs.readFileSync(new URL('../public/pcm-capture.js', import.meta.url), 'utf8'), context)
  const processor = new Processor()
  processor.port.onmessage({ data: { enabled: true } })
  return { processor, frames }
}

test('actual 8–96 kHz clocks each produce 50 unique mono frames per second', () => {
  for (const rate of [8000, 16000, 22050, 32000, 44100, 48000, 96000]) {
    const { processor, frames } = workletAt(rate)
    for (let start = 0; start < rate; start += 128) {
      const channel = Float32Array.from({ length: Math.min(128, rate - start) }, (_, index) => (start + index) / rate - 0.5)
      processor.process([[channel]])
    }
    assert.equal(frames.length, 50, `input clock ${rate}`)
    assert.ok(frames.every(frame => frame.byteLength === 640))
    assert.equal(new Set(frames.map(frame => Buffer.from(frame).toString('base64'))).size, 50)
    assert.equal(processor.offset, 0)
  }
})

test('duplicate enabled messages preserve a partial frame instead of discarding input', () => {
  const { processor, frames } = workletAt(16000)
  processor.process([[new Float32Array(128).fill(0.25)]])
  processor.port.onmessage({ data: { enabled: true } })
  processor.process([[new Float32Array(192).fill(0.5)]])
  assert.equal(frames.length, 1)
  assert.equal(new DataView(frames[0]).getInt16(0, true), 8192)
  assert.equal(new DataView(frames[0]).getInt16(256, true), 16384)
})


test('a stereo input with speech on the second channel is downmixed to mono instead of discarded', () => {
  const { processor, frames } = workletAt(48000)
  processor.process([[new Float32Array(960), new Float32Array(960).fill(0.5)]])
  assert.equal(frames.length, 1)
  assert.equal(frames[0].byteLength, 640)
  assert.equal(new DataView(frames[0]).getInt16(0, true), 8192)
})

test('multichannel downmix stays bounded and invalid input samples cannot poison subsequent PCM', () => {
  const { processor, frames } = workletAt(16000)
  processor.process([[new Float32Array(320).fill(1), new Float32Array(320).fill(1), new Float32Array(320).fill(1)]])
  processor.process([[new Float32Array(320).fill(NaN), new Float32Array(320).fill(0.5)]])
  assert.equal(frames.length, 2)
  assert.equal(new DataView(frames[0]).getInt16(0, true), 32767)
  assert.equal(new DataView(frames[1]).getInt16(0, true), 8192)
})
