<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchHealth } from './api/health'
import ScriptLibrary from './components/ScriptLibrary.vue'
import SessionPanel from './components/SessionPanel.vue'
import type { ScriptDocument } from './api/scripts'

type ConnectionState = 'checking' | 'connected' | 'unavailable'

const connectionState = ref<ConnectionState>('checking')
const connectionMessage = ref('正在检查后端服务…')
const serviceVersion = ref('')
const selectedScript = ref<ScriptDocument>()
const sessionActive = ref(false)
let healthRequest: AbortController | undefined
let healthTimeout: ReturnType<typeof setTimeout> | undefined

const sessionStates = [
  { name: '就绪', description: '准备讲稿，等待开始' },
  { name: '识别', description: '追踪朗读进度与停顿' },
  { name: '播报', description: '提示至下一个分段标点' },
  { name: '暂停', description: '保留位置，等待继续' },
  { name: '终止', description: '结束当前演讲会话' },
]

async function checkConnection() {
  healthRequest?.abort()
  clearTimeout(healthTimeout)
  const controller = new AbortController()
  healthRequest = controller
  connectionState.value = 'checking'
  connectionMessage.value = '正在检查后端服务…'
  serviceVersion.value = ''
  healthTimeout = setTimeout(() => controller.abort(), 5000)

  try {
    const health = await fetchHealth(controller.signal)
    if (controller.signal.aborted) return
    connectionState.value = 'connected'
    connectionMessage.value = '后端服务已连接'
    serviceVersion.value = health.version ? `版本 ${health.version}` : ''
  } catch (error) {
    if (healthRequest !== controller) return
    connectionState.value = 'unavailable'
    connectionMessage.value = controller.signal.aborted
      ? '连接超时，请确认后端服务已启动。'
      : error instanceof Error
        ? `连接失败：${error.message}。请确认后端服务已启动。`
        : '无法连接后端，请稍后重试。'
  } finally {
    if (healthRequest === controller) clearTimeout(healthTimeout)
  }
}

onMounted(checkConnection)
onBeforeUnmount(() => {
  healthRequest?.abort()
  healthRequest = undefined
  clearTimeout(healthTimeout)
})
</script>

<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>

  <div class="app-shell">
    <header class="site-header">
      <span class="brand-mark" aria-hidden="true">声</span>
      <span class="brand-name">Whisper Guide</span>
      <span class="version-badge">讲稿准备 · v0.1</span>
    </header>

    <main id="main-content" tabindex="-1">
      <section class="intro" aria-labelledby="page-title">
        <p class="eyebrow">演讲语音追踪与提示</p>
        <h1 id="page-title">轻声提示，<br />接上每一句。</h1>
        <p class="intro-copy">
          为盲人演讲者提供朗读进度追踪与耳机提示。停顿达到设定时间后，
          从最后读到的内容单位开始，提示至下一个分段标点。
        </p>
      </section>

      <section class="connection-card" aria-labelledby="connection-title">
        <div>
          <h2 id="connection-title">服务连接</h2>
          <p
            class="connection-status"
            :class="`connection-status--${connectionState}`"
            role="status"
            aria-live="polite"
            aria-atomic="true"
          >
            <span class="status-dot" aria-hidden="true"></span>
            <span>{{ connectionMessage }}</span>
          </p>
          <p v-if="serviceVersion" class="service-version">{{ serviceVersion }}</p>
        </div>
        <button
          type="button"
          :disabled="connectionState === 'checking'"
          @click="checkConnection"
        >
          {{ connectionState === 'checking' ? '检查中…' : '重新检查连接' }}
        </button>
      </section>

      <ScriptLibrary :locked="sessionActive" @selected="selectedScript = $event" />
      <SessionPanel :script="selectedScript" :service-available="connectionState === 'connected'" @active="sessionActive = $event" />

      <section class="scope-card" aria-labelledby="scope-title">
        <div class="section-heading">
          <h2 id="scope-title">第一版工作流程</h2>
          <span class="timeout-label">默认静音阈值：3000 毫秒</span>
        </div>
        <ul class="state-list">
          <li v-for="state in sessionStates" :key="state.name">
            <h3>{{ state.name }}</h3>
            <p>{{ state.description }}</p>
          </li>
        </ul>
        <p class="scope-note">
            当前已支持讲稿上传、选择和预览，以及基本的会话控制。语音识别和语音合成由服务端提供。
        </p>
      </section>
    </main>

    <footer>浏览器采集与播放 · 服务端语音处理</footer>
  </div>
</template>
