<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NTag, NAlert, NDrawer, NDrawerContent, NCollapse, NCollapseItem } from 'naive-ui'
import ScriptLibraryPanel from '../components/scripts/ScriptLibraryPanel.vue'
import TeleprompterView from '../components/studio/TeleprompterView.vue'
import SessionControls from '../components/studio/SessionControls.vue'
import TranscriptComparison from '../components/studio/TranscriptComparison.vue'
import StateMachineMonitor from '../components/studio/StateMachineMonitor.vue'
import { useSessionStore } from '../stores/session'
import { useScriptsStore } from '../stores/scripts'
import { getSessionController } from '../services/sessionController'
const route = useRoute()
const session = useSessionStore()
const scripts = useScriptsStore()
const libraryOpen = ref(false)
const labels = { READY: '就绪', LISTENING: '识别中', PROMPTING: '提示播报', PAUSED: '已暂停', TERMINATED: '已结束' }
const text = computed(() => session.active ? session.snapshot?.script_text ?? '' : scripts.selected?.content ?? '')
const recoveredFeedback = ref<{ sessionId: string; feedbackId: number }>()
watch(() => [session.snapshot?.id, session.snapshot?.reliable, session.snapshot?.feedback_id] as const, ([sessionId, reliable, feedbackId]) => {
  if (sessionId && reliable && feedbackId != null) recoveredFeedback.value = { sessionId, feedbackId }
}, { immediate: true })
const sessionFeedback = computed(() => {
  const snapshot = session.snapshot
  if (!snapshot) return ''
  if (snapshot.state === 'PAUSED' && snapshot.transition_trigger !== 'user_paused') {
    return snapshot.pause_reason || snapshot.message || session.notice
  }
  if (snapshot.state === 'LISTENING' && snapshot.alignment_status) {
    if (snapshot.alignment_status !== 'mismatch') return ''
    return snapshot.message || session.notice || '最近一次有效识别未能可靠匹配讲稿，自动提示暂缓；请继续朗读或手动定位。'
  }
  if (snapshot.state === 'LISTENING' && !snapshot.reliable && !snapshot.speaking && snapshot.last_transcript?.trim()) {
    const recovered = recoveredFeedback.value
    if (recovered?.sessionId === snapshot.id && snapshot.feedback_id <= recovered.feedbackId) return ''
    return snapshot.message || session.notice
  }
  return ''
})
async function queryChanged() {
  if (route.query.panel === 'scripts') libraryOpen.value = true
  const id = route.query.script
  if (typeof id === 'string' && !session.active && id !== scripts.selected?.id) {
    await scripts.select(id); session.selectedScript = scripts.selected
  }
}
onMounted(() => { getSessionController().enterStudio(); void scripts.refresh(); void queryChanged(); if (!session.active) session.selectedScript = scripts.selected })
watch(() => route.query, () => { void queryChanged() })
watch(() => scripts.selected, script => { if (!session.active) session.selectedScript = script })
</script>
<template>
  <section class="studio-page">
  <div class="studio-heading"><div><h1 id="page-title" tabindex="-1">演讲工作台</h1><p>{{ session.active ? session.snapshot?.script_name : scripts.selected?.name ?? '选择讲稿后开始演讲' }}</p></div><div class="studio-actions"><NTag :type="session.state === 'LISTENING' ? 'success' : session.state === 'PROMPTING' ? 'warning' : 'default'" role="status">{{ labels[session.state] }}</NTag><NButton class="library-trigger" @click="libraryOpen = true">讲稿库</NButton></div></div>
  <NAlert v-if="session.error" type="error" closable :show-icon="false" @close="session.error = ''">{{ session.error }}</NAlert>
  <NAlert v-if="sessionFeedback" type="warning" :show-icon="false" role="alert">{{ sessionFeedback }}</NAlert>
  <div class="studio-grid">
    <aside class="library-column"><ScriptLibraryPanel /></aside>
    <div class="studio-main">
      <TeleprompterView :text="text" :snapshot="session.active ? session.snapshot : undefined" />
      <SessionControls />
      <TranscriptComparison :snapshot="session.snapshot" />
      <NCollapse><NCollapseItem title="状态机与流转诊断" name="state"><StateMachineMonitor :session="session.snapshot" /></NCollapseItem></NCollapse>
      <p class="keyboard-help">Alt+Shift+P 暂停/继续 · I 打断 · E 结束 · H 手动提示。页面切换会安全暂停，用户录音不保存。</p>
    </div>
  </div>
  <NDrawer v-model:show="libraryOpen" :width="360"><NDrawerContent title="讲稿管理" closable><ScriptLibraryPanel /></NDrawerContent></NDrawer>
  </section>
</template>
<style scoped>
.studio-page{max-width:1760px;margin:0 auto;padding:28px 32px}.studio-heading{display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;gap:16px}.studio-heading h1{margin:0;font-size:25px;font-weight:650}.studio-heading p{margin:5px 0 0;color:var(--muted);font-size:13px}.studio-actions{display:flex;gap:12px;align-items:center}.studio-grid{display:grid;grid-template-columns:245px minmax(0,1fr);gap:24px;margin-top:16px;align-items:start}.library-column{padding:18px;background:var(--surface);border:1px solid var(--line);border-radius:12px;position:sticky;top:90px}.studio-main{display:grid;gap:16px;min-width:0}.keyboard-help{color:var(--muted);font-size:11px;margin:0}.studio-main>.n-collapse{padding:16px;border:1px solid var(--line);border-radius:12px;background:var(--surface)}@media(max-width:700px){.studio-page{padding:20px 16px}}@media(max-width:1050px){.studio-grid{grid-template-columns:1fr}.library-column{display:none}}@media(min-width:1051px){.library-trigger{display:none}}
</style>
