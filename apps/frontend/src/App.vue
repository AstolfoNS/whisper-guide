<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, watchEffect } from 'vue'
import { NConfigProvider, NMessageProvider, NDialogProvider, darkTheme, zhCN, dateZhCN } from 'naive-ui'
import { RouterView, useRoute } from 'vue-router'
import AppLayout from './layouts/AppLayout.vue'
import { usePreferencesStore } from './stores/preferences'
import { useSessionStore } from './stores/session'
import { getSessionController, disposeSessionController } from './services/sessionController'
import { startHealthMonitor, stopHealthMonitor } from './services/healthMonitor'
import { appTitle, loadAppConfig } from './services/appConfig'

const preferences = usePreferencesStore()
const session = useSessionStore()
const route = useRoute()
watchEffect(() => { document.title = route.meta.title ? `${String(route.meta.title)} · ${appTitle.value}` : appTitle.value })
const theme = computed(() => preferences.isDark ? darkTheme : null)
const themeOverrides = { common: { primaryColor: '#168079', primaryColorHover: '#20958c', primaryColorPressed: '#0c655f', borderRadius: '8px', fontSize: '14px' } }
function shortcuts(event: KeyboardEvent) {
  if (route.path !== '/studio' || !event.altKey || !event.shiftKey || event.repeat) return
  const target = event.target as HTMLElement | null
  if (target?.closest('input,textarea,select,[contenteditable="true"],[role="dialog"]')) return
  const controller = getSessionController()
  const actions: Record<string, () => void> = {
    p: () => session.state === 'PAUSED' ? void controller.resume() : controller.pause(),
    i: () => controller.interrupt(), e: () => controller.terminate(), h: () => controller.manualPrompt(),
  }
  const action = actions[event.key.toLowerCase()]
  if (action) { event.preventDefault(); action() }
}
onMounted(() => { void loadAppConfig(); preferences.initialize(); startHealthMonitor(); window.addEventListener('keydown', shortcuts) })
onBeforeUnmount(() => { stopHealthMonitor(); disposeSessionController(); preferences.dispose(); window.removeEventListener('keydown', shortcuts) })
</script>
<template>
  <NConfigProvider :theme="theme" :theme-overrides="themeOverrides" :locale="zhCN" :date-locale="dateZhCN">
    <NMessageProvider><NDialogProvider><AppLayout><RouterView /></AppLayout></NDialogProvider></NMessageProvider>
  </NConfigProvider>
</template>
