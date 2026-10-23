<script setup lang="ts">
import { ref } from 'vue'
import { NAlert, NButton, NPopconfirm } from 'naive-ui'
import { useSessionStore } from '../../stores/session'
import { getSessionController } from '../../services/sessionController'
import { refreshHealth } from '../../services/healthMonitor'
const session = useSessionStore()
const error = ref('')
const busy = ref(false)
async function finish(id: string) {
  if (busy.value || session.busy) return
  busy.value = true; error.value = ''
  try {
    const base = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
    const response = await fetch(`${base}/api/sessions/${encodeURIComponent(id)}/terminate`, { method: 'POST', signal: AbortSignal.timeout(5000) })
    if (!response.ok) throw new Error('无法结束会话：可能已被其他页面接管，请刷新后检查。')
    if (id === session.recoverySessionId || id === session.snapshot?.id) {
      session.snapshot = undefined; session.recoverySessionId = ''; session.localPaused = false
      sessionStorage.removeItem('whisper-guide:session-id')
    }
    await refreshHealth()
  } catch (reason) { error.value = (reason as Error).message }
  finally { busy.value = false }
}
</script>
<template>
  <NAlert v-if="session.recoverableSessions.length" title="有可恢复的会话" type="info" :show-icon="false" class="recovery-panel">
    <div v-for="item in session.recoverableSessions" :key="item.session_id" class="recovery-row">
      <span>{{ item.script_name }} · {{ item.state === 'PAUSED' ? '已暂停' : '待开始' }}</span>
      <NButton size="small" :disabled="busy || session.busy || session.active" @click="getSessionController().reconnect(item.session_id)">恢复连接</NButton>
      <NPopconfirm @positive-click="finish(item.session_id)"><template #trigger><NButton size="small" :disabled="busy || session.busy">结束此会话</NButton></template>结束后将释放该会话进度；讲稿文件保留。确认结束？</NPopconfirm>
    </div>
    <p v-if="error" role="alert">{{ error }}</p>
  </NAlert>
</template>
<style scoped>.recovery-panel{margin-bottom:20px}.recovery-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.recovery-row>span{flex:1;min-width:120px}</style>
