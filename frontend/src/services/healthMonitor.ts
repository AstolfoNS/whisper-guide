import { useSessionStore } from '../stores/session'
import { useHealthStore, type EngineStatus } from '../stores/health'
const base = () => (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
let request: Promise<void> | undefined
let timer: ReturnType<typeof setTimeout> | undefined
let running = false
function status(value: unknown): EngineStatus { return value === 'ready' || value === 'fallback' || value === 'unavailable' ? value : 'unknown' }
export function refreshHealth() {
  if (request) return request
  const store = useHealthStore()
  store.checking = true
  request = (async () => {
    const [health, speech, configs, recovery] = await Promise.allSettled([
      fetch(base() + '/api/health', { signal: AbortSignal.timeout(5000) }).then(r => { if (!r.ok) throw new Error(); return r.json() }),
      fetch(base() + '/api/speech/status', { signal: AbortSignal.timeout(5000) }).then(r => { if (!r.ok) throw new Error(); return r.json() }),
      fetch(base() + '/api/settings/model-configs', { signal: AbortSignal.timeout(5000) }).then(r => { if (!r.ok) throw new Error(); return r.json() }),
      fetch(base() + '/api/sessions/recoverable', { signal: AbortSignal.timeout(5000) }).then(r => { if (!r.ok) throw new Error(); return r.json() }),
    ])
    store.backend = health.status === 'fulfilled' && health.value.status === 'ok' ? 'ready' : 'unavailable'
    if (speech.status === 'fulfilled') {
      store.stt = status(speech.value.stt); store.vad = status(speech.value.vad); store.tts = status(speech.value.tts)
      store.provider = speech.value.provider ?? 'local'; store.cloud = speech.value.cloud_enabled === true
      store.detail = speech.value.detail ?? ''
    } else { store.stt = store.vad = store.tts = 'unknown'; store.detail = '语音服务状态读取失败。' }
    if (configs.status === 'fulfilled') {
      const active = configs.value.active_profile
      store.configName = active?.name ?? ''
      store.llmMode = active?.settings?.llm_align_mode ?? 'off'
      store.model = active?.settings?.llm_model ?? ''
    }
    if (recovery.status === 'fulfilled') useSessionStore().recoverableSessions = recovery.value.sessions ?? []
    store.checkedAt = new Date().toISOString()
  })().finally(() => { store.checking = false; request = undefined })
  return request
}
function schedule() {
  if (!running) return
  timer = setTimeout(async () => { if (!document.hidden) await refreshHealth(); schedule() }, 10000)
}
const visibility = () => { if (!document.hidden) void refreshHealth() }
const activated = () => { void refreshHealth() }
export function startHealthMonitor() {
  if (running) return
  running = true; void refreshHealth(); schedule()
  document.addEventListener('visibilitychange', visibility)
  window.addEventListener('model-config-activated', activated)
}
export function stopHealthMonitor() {
  running = false; clearTimeout(timer)
  document.removeEventListener('visibilitychange', visibility)
  window.removeEventListener('model-config-activated', activated)
}
