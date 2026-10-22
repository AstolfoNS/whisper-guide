<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ScriptDocument } from '../api/scripts'
import { SessionClient, type SessionSnapshot, type SessionServerEvent } from '../api/session'
import { Microphone } from '../audio/microphone'
import { PromptPlayer } from '../audio/player'

const props = defineProps<{ script?: ScriptDocument; serviceAvailable: boolean }>()
const emit = defineEmits<{ active: [value: boolean] }>()
const snapshot = ref<SessionSnapshot>()
const timeout = ref(3000)
const position = ref(0)
const busy = ref(false)
const error = ref('')
const localPaused = ref(false)
const speech = ref({ stt: 'unavailable', tts: 'unavailable', detail: '正在检查语音服务…' })
const microphone = new Microphone()
const player = new PromptPlayer()
const state = computed(() => localPaused.value ? 'PAUSED' : snapshot.value?.state ?? 'READY')
const labels = { READY: '就绪', LISTENING: '识别', PROMPTING: '播报', PAUSED: '暂停', TERMINATED: '终止' }
const canConfigure = computed(() => ['READY', 'PAUSED'].includes(state.value))
const validTimeout = computed(() => Number.isInteger(timeout.value) && timeout.value > 0)
const canStart = computed(() => !busy.value && props.script && props.serviceAvailable &&
  speech.value.stt === 'ready' && speech.value.tts === 'ready' &&
  ['READY', 'TERMINATED'].includes(state.value))
let destroyed = false
let captureGeneration = -1
let captureAttempt = 0
let lastFeedback = -1
let expectedPrompt = ''
let operation = 0
let minimumGeneration = -1
let pendingState: SessionSnapshot['state'] | undefined
const client = new SessionClient(onEvent, disconnected)

function beginOperation() { return ++operation }
function invalidateOperations() { operation++ }
function ensureOperation(current: number) {
  if (current !== operation || destroyed) throw new Error('操作已取消。')
}

async function refreshSpeech() {
  try {
    const base = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
    const response = await fetch(base + '/api/speech/status', { signal: AbortSignal.timeout(5000) })
    if (!response.ok) throw new Error('无法获取语音服务状态')
    const result = await response.json()
    speech.value = { stt: result.stt, tts: result.tts, detail: result.detail ?? '' }
  } catch {
    speech.value = { stt: 'unavailable', tts: 'unavailable', detail: '语音服务连接失败，请检查后重试。' }
  }
}

function stopCapture() {
  captureAttempt++
  captureGeneration = -1
  microphone.close()
}
function stopPrompt() {
  expectedPrompt = ''
  player.stop()
}
function disconnected() {
  stopCapture(); stopPrompt()
  if (snapshot.value && snapshot.value.state !== 'TERMINATED') {
    localPaused.value = true
    error.value = '连接已中断。保留进度，检查网络后点击继续。'
    emit('active', true)
  }
}
async function enableCapture(next: SessionSnapshot) {
  if (captureGeneration === next.generation || destroyed) return
  captureGeneration = next.generation
  const attempt = ++captureAttempt
  try {
    if (!microphone.isOpen) await microphone.open(frame => {
      if (state.value === 'LISTENING') client.sendAudio(frame)
    }, reason => pause(reason))
    if (attempt !== captureAttempt || destroyed || state.value !== 'LISTENING') return
    await microphone.resume()
    microphone.setEnabled(true)
    client.send({ type: 'audio_ready' })
  } catch (reason) {
    if (attempt === captureAttempt) pause(reason instanceof Error ? reason.message : '无法启用麦克风。')
  }
}
function onEvent(event: SessionServerEvent) {
  if (destroyed) return
  if (event.type === 'error') { error.value = event.message; return }
  if (event.type === 'snapshot') {
    const next = event.session
    if (next.generation < minimumGeneration) return
    if (pendingState && next.generation === minimumGeneration && next.state !== pendingState) return
    snapshot.value = next
    if (pendingState && next.state === pendingState && next.generation >= minimumGeneration) {
      pendingState = undefined
    }
    localPaused.value = pendingState === 'PAUSED'
    timeout.value = next.silence_timeout_ms
    emit('active', next.state !== 'TERMINATED')
    if (next.feedback_id !== lastFeedback) {
      lastFeedback = next.feedback_id
      if (next.message) error.value = next.message
    }
    if (next.state === 'LISTENING') {
      stopPrompt()
      void enableCapture(next)
    } else {
      microphone.setEnabled(false)
      captureGeneration = -1
      if (next.state !== 'PROMPTING') {
        stopPrompt()
        if (next.state === 'PAUSED' || next.state === 'TERMINATED') stopCapture()
      } else if (next.prompt) {
        expectedPrompt = next.prompt.id
      }
    }
    return
  }
  if (event.type === 'audio' && state.value === 'PROMPTING' && event.prompt_id === expectedPrompt) {
    const id = event.prompt_id
    void player.play(event.data, id, () => {
      if (id === expectedPrompt && client.connected) {
        expectedPrompt = ''
        client.send({ type: 'playback_finished', prompt_id: id })
      }
    }, () => {
      if (id === expectedPrompt && client.connected) {
        expectedPrompt = ''
        client.send({ type: 'playback_failed', prompt_id: id })
      }
    })
  }
}
async function start() {
  if (!canStart.value || !props.script) return
  if (!validTimeout.value) { error.value = '静音阈值必须为正整数。'; return }
  const currentOperation = beginOperation()
  busy.value = true; error.value = ''
  try {
    await player.unlock()
    ensureOperation(currentOperation)
    await microphone.open(frame => {
      if (state.value === 'LISTENING') client.sendAudio(frame)
    }, reason => pause(reason))
    ensureOperation(currentOperation)
    if (!client.connected) await client.connect()
    ensureOperation(currentOperation)
    if (!snapshot.value || snapshot.value.state === 'TERMINATED') {
      minimumGeneration = -1
      pendingState = undefined
      const ready = client.waitFor(s => s.state === 'READY')
      client.prepare(props.script.id, timeout.value)
      await ready
    } else {
      client.send({ type: 'configure', silence_timeout_ms: timeout.value })
    }
    ensureOperation(currentOperation)
    const listening = client.waitFor(s => s.state === 'LISTENING')
    client.send({ type: 'start' })
    await listening
  } catch (reason) {
    stopCapture()
    if (currentOperation === operation && !destroyed) {
      error.value = reason instanceof Error ? reason.message : '无法开始会话。'
    }
  } finally { busy.value = false }
}
function pause(reason = '') {
  if (!['LISTENING', 'PROMPTING'].includes(state.value)) return
  invalidateOperations()
  if (snapshot.value) {
    minimumGeneration = Math.max(minimumGeneration, snapshot.value.generation + 1)
    pendingState = 'PAUSED'
  }
  stopCapture(); stopPrompt()
  localPaused.value = true
  if (reason) error.value = reason
  if (client.connected) client.send({ type: 'pause' })
}
async function resume() {
  if (state.value !== 'PAUSED' || !snapshot.value || busy.value) return
  if (!validTimeout.value) { error.value = '静音阈值必须为正整数。'; return }
  const currentOperation = beginOperation()
  busy.value = true; error.value = ''
  try {
    await player.unlock()
    ensureOperation(currentOperation)
    await microphone.open(frame => {
      if (state.value === 'LISTENING') client.sendAudio(frame)
    }, reason => pause(reason))
    ensureOperation(currentOperation)
    if (!client.connected) {
      await client.connect()
      ensureOperation(currentOperation)
      const attached = client.waitFor(s => s.id === snapshot.value?.id)
      client.attach(snapshot.value.id)
      await attached
    }
    ensureOperation(currentOperation)
    client.send({ type: 'configure', silence_timeout_ms: timeout.value })
    client.send({ type: 'resume' })
  } catch (reason) {
    stopCapture()
    if (currentOperation === operation && !destroyed) {
      error.value = reason instanceof Error ? reason.message : '无法继续，请重新准备会话。'
    }
  } finally { busy.value = false }
}
function interrupt() {
  if (state.value !== 'PROMPTING') return
  invalidateOperations()
  if (snapshot.value) {
    minimumGeneration = Math.max(minimumGeneration, snapshot.value.generation + 1)
    pendingState = 'LISTENING'
  }
  stopPrompt()
  if (client.connected) client.send({ type: 'interrupt' })
}
function terminate() {
  invalidateOperations()
  if (snapshot.value) {
    minimumGeneration = Math.max(minimumGeneration, snapshot.value.generation + 1)
    pendingState = 'TERMINATED'
  }
  stopCapture(); stopPrompt()
  if (client.connected) client.send({ type: 'terminate' })
  if (snapshot.value) snapshot.value = { ...snapshot.value, state: 'TERMINATED', prompt: null }
  localPaused.value = false
  emit('active', false)
}
function configure() {
  if (!validTimeout.value) { error.value = '静音阈值必须为正整数。'; return }
  if (snapshot.value && client.connected) client.send({ type: 'configure', silence_timeout_ms: timeout.value })
}
function reposition() {
  if (client.connected) client.send({ type: 'reposition', unit_index: position.value })
}
function shortcuts(event: KeyboardEvent) {
  if (!event.altKey || !event.shiftKey || event.repeat) return
  const actions: Record<string, () => void> = {
    p: () => state.value === 'PAUSED' ? void resume() : pause(),
    i: interrupt, e: terminate,
    h: () => client.connected && client.send({ type: 'manual_prompt' }),
  }
  const action = actions[event.key.toLowerCase()]
  if (action) { event.preventDefault(); action() }
}
onMounted(() => { void refreshSpeech(); window.addEventListener('keydown', shortcuts) })
onBeforeUnmount(() => {
  destroyed = true; stopCapture(); stopPrompt(); client.close(); player.close()
  window.removeEventListener('keydown', shortcuts)
})
</script>

<template>
  <section class="session-card script-card" aria-labelledby="session-title">
    <div class="section-heading">
      <h2 id="session-title">演讲控制</h2>
      <span role="status" aria-live="polite">当前状态：{{ labels[state] }}</span>
    </div>
    <p>当前讲稿：{{ snapshot && state !== 'TERMINATED' ? snapshot.script_name : script?.name ?? '尚未选择' }}</p>
    <p role="status">识别服务：{{ speech.stt === 'ready' ? '已就绪' : '未就绪' }}；
      合成服务：{{ speech.tts === 'ready' ? '已安装，播放时检查网络' : '未就绪' }}</p>
    <p v-if="speech.detail">{{ speech.detail }}</p>
    <button type="button" @click="refreshSpeech">检查语音服务</button>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <div class="session-controls">
      <label for="silence-timeout">静音阈值（毫秒）</label>
      <input id="silence-timeout" v-model.number="timeout" type="number" min="1" step="1" :disabled="!canConfigure" />
      <button type="button" :disabled="!canConfigure || !validTimeout" @click="configure">应用阈值</button>
      <button type="button" :disabled="!canStart || !validTimeout" @click="start">{{ state === 'TERMINATED' ? '开始新演讲' : '开始演讲' }}</button>
      <button type="button" :disabled="!['LISTENING','PROMPTING'].includes(state)" @click="() => pause()">暂停</button>
      <button type="button" :disabled="state !== 'PAUSED' || busy" @click="resume">继续</button>
      <button type="button" :disabled="state !== 'PROMPTING'" @click="interrupt">打断播报</button>
      <button type="button" :disabled="state !== 'LISTENING'" @click="client.send({type:'manual_prompt'})">手动提示</button>
      <button type="button" :disabled="!snapshot || state === 'TERMINATED'" @click="terminate">结束</button>
    </div>
    <fieldset v-if="snapshot" :disabled="!['READY','PAUSED','LISTENING'].includes(state)">
      <legend>重新定位讲稿</legend>
      <label for="position">选择接下来提示的起点</label>
      <select id="position" v-model.number="position">
        <option v-for="(unit, index) in snapshot.units" :key="index" :value="index">{{ index + 1 }} · {{ unit.text }}</option>
      </select>
      <button type="button" @click="reposition">应用位置</button>
    </fieldset>
    <p v-if="snapshot?.last_transcript">最近识别：{{ snapshot.last_transcript }}</p>
    <p v-if="snapshot && !snapshot.reliable" role="status">正在重新定位，自动提示暂时停用。</p>
    <p v-if="snapshot?.prompt">当前提示：{{ snapshot.prompt.text }}</p>
    <p v-if="snapshot?.at_end" role="status">系统已提示到讲稿末尾，自动续播停止；仍可朗读、重新定位或结束。</p>
    <p class="input-help">Alt+Shift+P 暂停或继续，Alt+Shift+I 打断，Alt+Shift+E 结束，Alt+Shift+H 手动提示。
      播报期间不识别，请先打断再继续说话。用户录音不保存。</p>
  </section>
</template>
