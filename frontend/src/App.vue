<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { fetchHealth } from './api/health'
import ScriptLibrary from './components/ScriptLibrary.vue'
import SessionPanel from './components/SessionPanel.vue'
import ModelConfigPanel from './components/ModelConfigPanel.vue'
import StateMachineMonitor from './components/StateMachineMonitor.vue'
import type { SessionSnapshot } from './api/session'
import type { ModelSettings } from './api/models'
import type { ScriptDocument } from './api/scripts'

type ConnectionState = 'checking' | 'connected' | 'unavailable'

const connectionState = ref<ConnectionState>('checking')
const connectionMessage = ref('正在检查后端服务…')
const serviceVersion = ref('')
const selectedScript = ref<ScriptDocument>()
const sessionActive = ref(false)
const sessionSnapshot = ref<SessionSnapshot>()
const selectedProvider = ref('local')
const settingsRevision = ref(0)
function applyModelSettings(settings: ModelSettings) {
  selectedProvider.value = settings.stt_provider
  settingsRevision.value++
}

let healthRequest: AbortController | undefined
let healthTimeout: ReturnType<typeof setTimeout> | undefined


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
      <span class="brand-name">Whisper Guide</span>
      <div class="connection-toolbar" aria-label="服务连接">
        <div>
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
      </div>
    </header>

    <main id="main-content" tabindex="-1">

      <ScriptLibrary :locked="sessionActive" @selected="selectedScript = $event" />
      <SessionPanel :script="selectedScript" :stt-provider="selectedProvider" :settings-revision="settingsRevision" :service-available="connectionState === 'connected'" @active="sessionActive = $event" @snapshot="sessionSnapshot = $event" />
      <StateMachineMonitor :session="sessionSnapshot" />
      <details class="model-config-disclosure"><summary>模型与识别引擎配置</summary><ModelConfigPanel :disabled="sessionActive || connectionState !== 'connected'" @loaded="applyModelSettings" @saved="applyModelSettings" /></details>

    </main>

  </div>
</template>
