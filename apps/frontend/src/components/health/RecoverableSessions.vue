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
  <section v-if="session.recoverableSessions.length" class="recovery-panel" aria-label="可恢复会话">
    <NAlert title="有可恢复的会话" type="info" :show-icon="false">
      <div class="recovery-list">
        <div v-for="item in session.recoverableSessions" :key="item.session_id" class="recovery-row">
          <span class="recovery-summary">{{ item.script_name }} · {{ item.state === 'PAUSED' ? '已暂停' : '待开始' }}</span>
          <div class="recovery-actions">
            <NButton size="small" :disabled="busy || session.busy || session.active" @click="getSessionController().reconnect(item.session_id)">恢复连接</NButton>
            <NPopconfirm @positive-click="finish(item.session_id)"><template #trigger><NButton size="small" :disabled="busy || session.busy">结束此会话</NButton></template>结束后将释放该会话进度；讲稿文件保留。确认结束？</NPopconfirm>
          </div>
        </div>
      </div>
      <p v-if="error" class="recovery-error" role="alert">{{ error }}</p>
    </NAlert>
  </section>
</template>
<style scoped>
.recovery-panel{max-width:1760px;margin:0 auto;padding:24px 32px 0}
.recovery-list{display:grid;gap:8px;margin-top:12px}
.recovery-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:12px 24px;padding:8px 0;min-width:0}
.recovery-row+.recovery-row{border-top:1px solid var(--line);padding-top:16px}
.recovery-row:last-child{padding-bottom:0}
.recovery-summary{min-width:0;overflow-wrap:anywhere;line-height:1.7}
.recovery-actions{display:flex;align-items:center;flex-wrap:wrap;gap:10px;max-width:100%}
.recovery-error{margin:12px 0 0}
@media(max-width:700px){.recovery-panel{padding:20px 16px 0}}
@media(max-width:600px){.recovery-row{grid-template-columns:minmax(0,1fr);gap:12px}.recovery-actions{justify-content:flex-start}}
</style>
