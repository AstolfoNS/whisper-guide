<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import RecoverableSessions from '../components/health/RecoverableSessions.vue'
import ServiceHealthBar from '../components/health/ServiceHealthBar.vue'
import { usePreferencesStore } from '../stores/preferences'
import { useSessionStore } from '../stores/session'
import { appTitle } from '../services/appConfig'

const route = useRoute()
const preferences = usePreferencesStore()
const session = useSessionStore()
const sessionLabel = computed(() => {
  const labels = { READY: '会话已就绪', LISTENING: '正在识别', PROMPTING: '正在播报', PAUSED: '会话已暂停', TERMINATED: '会话已结束' }
  return session.snapshot ? labels[session.snapshot.state] : ''
})
function applyTheme(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value === 'light' || value === 'dark' || value === 'system') preferences.setTheme(value)
}

</script>

<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <div class="app-shell">
    <header class="app-header">
      <div class="app-header__inner">
        <RouterLink to="/" class="app-brand" :aria-label="`${appTitle} 概览`">
          <span class="app-brand__mark" aria-hidden="true"><svg width="22" height="22" viewBox="0 0 24 24" fill="none"><path d="M5 10v4m4-8v12m4-15v18m4-15v12m4-8v4" stroke="currentColor" stroke-width="2" stroke-linecap="round" /></svg></span>
          <span>{{ appTitle }}</span>
        </RouterLink>
        <nav class="app-navigation" aria-label="主导航">
          <RouterLink to="/" active-class="is-active">概览</RouterLink>
          <RouterLink to="/studio" active-class="is-active">工作台</RouterLink>
          <RouterLink to="/settings/models" active-class="is-active">模型配置</RouterLink>
        </nav>
        <div class="app-header__tools">
          <RouterLink v-if="session.active && route.path !== '/studio'" to="/studio" class="session-return"><span class="status-dot" aria-hidden="true"></span>{{ sessionLabel }}<span aria-hidden="true">↗</span></RouterLink>
          <ServiceHealthBar />
          <label class="theme-control"><span class="sr-only">界面主题</span><select :value="preferences.theme" aria-label="界面主题" @change="applyTheme"><option value="light">亮色</option><option value="dark">暗色</option><option value="system">跟随系统</option></select></label>
        </div>
      </div>
    </header>
    <main id="main-content" class="app-main" tabindex="-1"><RecoverableSessions /><slot /></main>
  </div>
</template>

<style scoped>.theme-control select{color:var(--text);background:var(--soft);border:1px solid var(--line);border-radius:6px;padding:6px 8px;min-height:32px;font-size:12px}</style>
