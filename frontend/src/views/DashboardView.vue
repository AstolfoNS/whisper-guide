<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { NTag } from 'naive-ui'
import { RouterLink } from 'vue-router'
import { useScriptsStore } from '../stores/scripts'
import { useSessionStore } from '../stores/session'
import { useModelConfigsStore } from '../stores/modelConfigs'

const scripts = useScriptsStore()
const session = useSessionStore()
const models = useModelConfigsStore()
const recentScripts = computed(() => [...scripts.scripts].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5))
const activeProfile = computed(() => models.activeProfile)
const alignLabels = { off: '关闭语义校准', fallback: '低分语义兜底', hybrid: '混合语义校准' }
function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}
onMounted(() => { void Promise.allSettled([scripts.refresh(), models.load()]) })
</script>

<template>
  <div class="dashboard page-container">
    <header class="page-heading">
      <div>
        <p class="page-eyebrow">演讲空间</p>
        <h1 id="page-title" tabindex="-1">概览</h1>
        <p class="page-description">讲稿双游标追踪 · 可选语义校准 · 本服务不保存录音</p>
      </div>
      <RouterLink to="/studio" class="action-link action-link--primary">{{ session.active ? '返回工作台' : '进入工作台' }}<span aria-hidden="true">↗</span></RouterLink>
    </header>
    <div class="dashboard-grid">
      <section class="surface-panel dashboard-scripts" aria-labelledby="recent-scripts-title">
        <div class="panel-heading">
          <div><h2 id="recent-scripts-title">讲稿</h2><p class="text-muted">选择讲稿，在工作台开始演讲。</p></div>
          <RouterLink to="/studio?panel=scripts" class="text-link">管理讲稿<span aria-hidden="true"> →</span></RouterLink>
        </div>
        <p v-if="scripts.error" class="inline-message inline-message--error" role="alert">{{ scripts.error }}</p>
        <div v-if="scripts.loading && !recentScripts.length" class="dashboard-empty" role="status">正在读取讲稿…</div>
        <ul v-else-if="recentScripts.length" class="recent-scripts">
          <li v-for="script in recentScripts" :key="script.id">
            <RouterLink :to="{ path: '/studio', query: { script: script.id } }" class="recent-script">
              <span class="document-icon" aria-hidden="true"><svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8l-5-5Zm0 0v5h5M9 12h6m-6 4h4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></span>
              <span class="recent-script__body"><span class="recent-script__name">{{ script.name }}</span><span class="recent-script__meta">{{ script.character_count.toLocaleString('zh-CN') }} 字<span aria-hidden="true"> · </span><time :datetime="script.created_at">{{ formatDate(script.created_at) }}</time></span></span>
              <NTag v-if="scripts.selected?.id === script.id" size="small" :bordered="false" type="success">已选择</NTag>
              <span class="recent-script__arrow" aria-hidden="true">↗</span>
            </RouterLink>
          </li>
        </ul>
        <div v-else-if="!scripts.error" class="dashboard-empty"><h3>还没有讲稿</h3><p>上传纯文本讲稿后，即可开始提词和语音追踪。</p><RouterLink to="/studio?panel=scripts" class="action-link action-link--secondary">上传讲稿<span aria-hidden="true">＋</span></RouterLink></div>
      </section>
      <aside class="dashboard-sidebar">
        <section class="surface-panel dashboard-profile" aria-labelledby="active-profile-title">
          <div class="panel-heading"><h2 id="active-profile-title">当前模型方案</h2><span class="section-kicker">默认方案</span></div>
          <p v-if="models.error" class="inline-message inline-message--error" role="alert">{{ models.error }}</p>
          <template v-else-if="activeProfile">
            <h3 class="profile-name">{{ activeProfile.name }}</h3>
            <dl class="profile-summary"><div><dt>语音识别</dt><dd>{{ activeProfile.settings.stt_provider === 'local' ? '本地 sherpa-onnx' : '云端语音接口' }}</dd></div><div><dt>讲稿对齐</dt><dd>{{ alignLabels[activeProfile.settings.llm_align_mode] }}</dd></div></dl>
            <p v-if="activeProfile.pending_activation" class="inline-message">方案有待激活的修改。</p>
          </template>
          <p v-else class="text-muted">{{ models.loading ? '正在读取配置…' : '尚未读取到生效方案。' }}</p>
          <RouterLink to="/settings/models" class="action-link action-link--secondary dashboard-profile__link">管理模型配置<span aria-hidden="true">→</span></RouterLink>
        </section>
        <section class="dashboard-note" aria-labelledby="privacy-note-title"><span class="privacy-icon" aria-hidden="true"><svg width="19" height="19" viewBox="0 0 24 24" fill="none"><path d="m12 3 8 3v6c0 4.5-5.5 8-8 9-2.5-1-8-4.5-8-9V6l8-3Z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round" /><path d="m8.5 12 2.5 2.5 4.5-5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" /></svg></span><div><h2 id="privacy-note-title">数据与隐私</h2><p>讲稿保存在本机数据库。录音仅在有界内存中处理；启用云端能力前需明确授权。</p></div></section>
      </aside>
    </div>
  </div>
</template>
