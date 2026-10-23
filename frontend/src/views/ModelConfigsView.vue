<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { NAlert, NButton, NModal, NPopconfirm, NTag } from 'naive-ui'
import ModelConfigList from '../components/models/ModelConfigList.vue'
import ModelConfigForm from '../components/models/ModelConfigForm.vue'
import { modelConfigDraft, validateModelConfigDraft } from '../components/models/modelConfigForm.ts'
import {
  activateModelConfig, createModelConfig, deleteModelConfig, duplicateModelConfig,
  discoverModels, fetchModelConfig, ModelConfigError, testModelConfig, updateModelConfig,
  type ModelConfig, type ModelConfigTestResult, type ModelDiscoveryResult,
} from '../api/modelConfigs.ts'
import { useModelConfigsStore } from '../stores/modelConfigs'
import { useSessionStore } from '../stores/session'

const store = useModelConfigsStore()
const session = useSessionStore()
const selected = ref<ModelConfig>()
const draft = ref(modelConfigDraft())
const baseline = ref(JSON.stringify(draft.value))
const editorVisible = ref(false)
const busy = ref(false)
const testingTarget = ref<'stt' | 'llm'>()
const error = ref('')
const notice = ref('')
const testResult = ref<ModelConfigTestResult>()
const discoveryBusy = ref(false)
const discoveryResult = ref<ModelDiscoveryResult>()
const discoveryError = ref('')
let discoveryController: AbortController | undefined
let discoveryGeneration = 0
const showUnsaved = ref(false)
let pendingDiscard: ((value: boolean) => void) | undefined
let discardPromise: Promise<boolean> | undefined
let controller: AbortController | undefined
const dirty = computed(() => JSON.stringify(draft.value) !== baseline.value)
const locked = computed(() => busy.value || testingTarget.value !== undefined || store.loading)
const alreadyActive = computed(() => selected.value?.is_active && !selected.value.pending_activation)
const canActivate = computed(() => selected.value && !dirty.value && !locked.value && !session.active && !alreadyActive.value && store.storeAvailable)
const testVerification = computed(() => testResult.value?.ok === false ? '连通性测试失败' : testResult.value?.verified === 'network' ? '网络请求验证' : testResult.value?.verified === 'capability' ? '本地能力检查，未执行云端网络请求' : '未完成连通验证')

function apply(profile?: ModelConfig) {
  cancelDiscovery()
  selected.value = profile
  draft.value = modelConfigDraft(profile)
  baseline.value = JSON.stringify(draft.value)
  editorVisible.value = true
  testResult.value = undefined
}

function explain(reason: unknown) {
  error.value = reason instanceof ModelConfigError && reason.code === 'REVISION_CONFLICT'
    ? '此配置已在其他页面修改。请刷新配置后重新编辑，当前草稿尚未覆盖服务器内容。'
    : reason instanceof Error ? reason.message : '操作失败，请重试。'
}

function confirmDiscard(): Promise<boolean> {
  if (!dirty.value) return Promise.resolve(true)
  if (discardPromise) return Promise.resolve(false)
  showUnsaved.value = true
  discardPromise = new Promise((resolve) => { pendingDiscard = resolve })
  return discardPromise
}

function settleDiscard(discard: boolean) {
  showUnsaved.value = false
  pendingDiscard?.(discard)
  pendingDiscard = undefined
  discardPromise = undefined
}

async function selectProfile(id: string) {
  if (locked.value || id === selected.value?.id || !await confirmDiscard()) return
  busy.value = true
  error.value = ''
  notice.value = ''
  cancelDiscovery()
  controller?.abort()
  controller = new AbortController()
  try { apply(await fetchModelConfig(id, controller.signal)) }
  catch (reason) { if (!controller.signal.aborted) explain(reason) }
  finally { busy.value = false }
}

async function newProfile() {
  if (locked.value || !store.storeAvailable || !await confirmDiscard()) return
  apply()
  error.value = ''
  notice.value = '填写后保存为独立方案。新建与保存不会改变当前生效配置。'
}

async function refresh() {
  if (busy.value || testingTarget.value || !await confirmDiscard()) return
  busy.value = true
  error.value = ''
  notice.value = ''
  try {
    await store.load()
    const id = store.profiles.some((item) => item.id === selected.value?.id) ? selected.value?.id : store.activeId || store.profiles[0]?.id
    if (id) apply(await fetchModelConfig(id))
    else { selected.value = undefined; editorVisible.value = false }
  } catch (reason) { explain(reason) }
  finally { busy.value = false }
}

async function save() {
  if (locked.value || !store.storeAvailable || selected.value?.is_default) return
  error.value = validateModelConfigDraft(draft.value)
  if (error.value) return
  cancelDiscovery()
  busy.value = true
  notice.value = ''
  try {
    const saved = selected.value
      ? await updateModelConfig(selected.value.id, draft.value)
      : await createModelConfig(draft.value)
    apply(saved)
    await store.load()
    notice.value = saved.is_active && saved.pending_activation ? '修改已保存。结束当前会话后，重新激活此方案以应用修改。' : '配置已保存，当前生效方案未改变。'
  } catch (reason) { explain(reason) }
  finally { busy.value = false }
}

async function duplicate() {
  if (!selected.value || locked.value || !store.storeAvailable || !await confirmDiscard()) return
  busy.value = true
  error.value = ''
  try {
    apply(await duplicateModelConfig(selected.value.id))
    await store.load()
    notice.value = '已复制非敏感参数。请重新填写所需凭据，再保存和激活。'
  } catch (reason) { explain(reason) }
  finally { busy.value = false }
}

async function remove() {
  if (!selected.value || selected.value.is_active || selected.value.is_default || locked.value || !store.storeAvailable) return
  if (!await confirmDiscard()) return
  busy.value = true
  error.value = ''
  try {
    await deleteModelConfig(selected.value.id)
    await store.load()
    const nextId = store.activeId || store.profiles[0]?.id
    if (nextId) apply(await fetchModelConfig(nextId))
    else { selected.value = undefined; editorVisible.value = false }
    notice.value = '配置已删除。'
  } catch (reason) { explain(reason) }
  finally { busy.value = false }
}

async function activate() {
  if (!canActivate.value || !selected.value) return
  busy.value = true
  error.value = ''
  try {
    apply(await activateModelConfig(selected.value.id, selected.value.revision))
    await store.load()
    window.dispatchEvent(new CustomEvent('model-config-activated'))
    notice.value = '配置已激活，下一次创建的会话将使用此版本。'
  } catch (reason) { explain(reason) }
  finally { busy.value = false }
}

async function testConnection(target: 'stt' | 'llm') {
  if (locked.value) return
  error.value = ''
  const input = draft.value
  if (target === 'llm' && (input.settings.llm_align_mode === 'off' || !input.settings.llm_base_url.trim() || !input.settings.llm_model.trim())) {
    error.value = '请启用语义对齐，并填写 Base URL 和模型名称后再测试。'
    return
  }
  if (target === 'stt' && input.settings.stt_provider === 'cloud' && !input.settings.cloud_opt_in) {
    error.value = '请先授权发送音频片段，再测试云端语音接口。'
    return
  }
  cancelDiscovery()
  testingTarget.value = target
  testResult.value = undefined
  try { testResult.value = await testModelConfig(input, target, selected.value?.id) }
  catch (reason) { explain(reason) }
  finally { testingTarget.value = undefined }
}

function cancelDiscovery() {
  discoveryGeneration += 1
  discoveryController?.abort()
  discoveryController = undefined
  discoveryBusy.value = false
  discoveryResult.value = undefined
  discoveryError.value = ''
}

async function discover() {
  if (locked.value || discoveryBusy.value || selected.value?.is_default || !store.storeAvailable) return
  cancelDiscovery()
  const requestController = new AbortController()
  discoveryController = requestController
  const generation = discoveryGeneration
  discoveryBusy.value = true
  try {
    const result = await discoverModels(draft.value, selected.value?.id, requestController.signal)
    if (generation !== discoveryGeneration || requestController.signal.aborted) return
    discoveryResult.value = result
  } catch (reason) {
    if (generation !== discoveryGeneration || requestController.signal.aborted) return
    discoveryError.value = reason instanceof Error ? reason.message : '无法获取模型列表，请重试或手动填写模型名称。'
  } finally {
    if (generation === discoveryGeneration) {
      discoveryBusy.value = false
      discoveryController = undefined
    }
  }
}

watch(() => [selected.value?.id, draft.value.settings.llm_base_url, draft.value.settings.llm_api_key, draft.value.settings.clear_llm_api_key, draft.value.settings.llm_provider_preset], cancelDiscovery, { flush: 'sync' })
watch(draft, () => { testResult.value = undefined }, { deep: true })

function beforeUnload(event: BeforeUnloadEvent) {
  if (!dirty.value && !locked.value) return
  event.preventDefault()
  event.returnValue = ''
}

onBeforeRouteLeave(async () => {
  if (locked.value) { error.value = '操作正在进行，请等待完成后再切换页面。'; return false }
  const discard = await confirmDiscard()
  if (discard) cancelDiscovery()
  return discard
})
onMounted(() => { window.addEventListener('beforeunload', beforeUnload); void refresh() })
onBeforeUnmount(() => {
  cancelDiscovery()
  controller?.abort()
  window.removeEventListener('beforeunload', beforeUnload)
  pendingDiscard?.(false)
  draft.value.settings.llm_api_key = ''
  draft.value.settings.cloud_stt_access_token = ''
})
</script>

<template>
  <section class="model-page" aria-labelledby="page-title">
    <header class="page-heading model-page__heading">
      <div><h1 id="page-title" tabindex="-1">模型配置</h1><p>保存独立方案，按需激活；凭据仅由后端保管。</p></div>
      <NButton :disabled="locked" @click="refresh">刷新配置</NButton>
    </header>
    <NAlert v-if="error || store.error" type="error" :show-icon="false" role="alert" class="model-page__notice">{{ error || store.error }}</NAlert>
    <NAlert v-if="notice" type="success" :show-icon="false" role="status" class="model-page__notice">{{ notice }}</NAlert>
    <div class="model-page__layout">
      <ModelConfigList :profiles="store.profiles" :selected-id="selected?.id" :loading="store.loading" :busy="locked" :create-disabled="!store.storeAvailable" @select="selectProfile" @create="newProfile" />
      <article v-if="editorVisible" class="profile-editor" :aria-busy="locked" aria-labelledby="profile-editor-title">
        <header class="profile-editor__heading">
          <div><h2 id="profile-editor-title">{{ selected ? selected.name : '新建配置' }}</h2><p v-if="selected" class="profile-meta">版本 {{ selected.revision }}<span v-if="selected.is_active"> · 生效版本 {{ selected.active_revision }}</span></p></div>
          <div class="profile-editor__tags"><NTag v-if="dirty" size="small" :bordered="false" type="warning">未保存</NTag><NTag v-if="selected?.is_active" size="small" :bordered="false" type="success">当前生效</NTag></div>
        </header>
        <NAlert v-if="selected?.is_default" type="info" :show-icon="false" class="profile-session-notice">内置本地方案用于恢复离线识别。请复制后编辑自定义方案。</NAlert><NAlert v-if="!store.storeAvailable" type="error" :show-icon="false" class="profile-session-notice">配置存储当前不可写。原文件已保留，请检查服务端日志并恢复配置后刷新。</NAlert><NAlert v-if="session.active" type="info" :show-icon="false" class="profile-session-notice">当前会话尚未结束，可编辑和测试方案。结束会话后才能激活配置。</NAlert>
        <ModelConfigForm v-model="draft" :profile="selected" :presets="store.presets" :busy="locked || !store.storeAvailable" :discovery-busy="discoveryBusy" :discovery-result="discoveryResult" :discovery-error="discoveryError" @save="save" @discover="discover" />
        <div class="profile-test" aria-label="配置连通性测试">
          <div class="profile-test__buttons"><NButton :disabled="locked" :loading="testingTarget === 'stt'" @click="testConnection('stt')">{{ draft.settings.stt_provider === 'local' ? '检查本地 STT' : '测试云端 STT' }}</NButton><NButton :disabled="locked || draft.settings.llm_align_mode === 'off'" :loading="testingTarget === 'llm'" @click="testConnection('llm')">测试大模型连通性</NButton></div>
          <p class="profile-meta">测试使用当前表单，不保存或激活配置。</p>
          <NAlert v-if="testResult" :type="testResult.ok ? 'success' : 'error'" :show-icon="false" :role="testResult.ok ? 'status' : 'alert'"><strong>{{ testResult.target === 'llm' ? '大模型' : 'STT' }} · {{ testVerification }}</strong><p>{{ testResult.message }}<span v-if="testResult.latency_ms !== null">（{{ testResult.latency_ms }} ms）</span></p></NAlert>
        </div>
        <footer class="profile-actions">
          <div><NButton v-if="selected" :disabled="locked || !store.storeAvailable" @click="duplicate">复制配置</NButton><NPopconfirm v-if="selected && !selected.is_default && !selected.is_active" positive-text="删除配置" negative-text="取消" @positive-click="remove"><template #trigger><NButton type="error" secondary :disabled="locked || !store.storeAvailable">删除</NButton></template>删除“{{ selected.name }}”？此操作不会影响当前生效配置。</NPopconfirm></div>
          <div><NButton :disabled="!canActivate" @click="activate">{{ alreadyActive ? '已生效' : '设为生效配置' }}</NButton><NButton type="primary" attr-type="submit" form="model-profile-form" :disabled="locked || !store.storeAvailable || selected?.is_default || (selected !== undefined && !dirty)" :loading="busy">保存修改</NButton></div>
        </footer>
        <p v-if="dirty && selected" class="profile-meta">请先保存修改，再设为生效配置。</p>
      </article>
      <div v-else class="profile-empty"><p>{{ store.loading ? '正在读取配置…' : '选择配置方案，或新建一套配置。' }}</p><NButton v-if="store.error" :disabled="locked" @click="refresh">重新读取</NButton></div>
    </div>
    <NModal v-model:show="showUnsaved" preset="dialog" title="存在未保存的修改" positive-text="放弃修改" negative-text="继续编辑" :mask-closable="false" :closable="false" @positive-click="settleDiscard(true)" @negative-click="settleDiscard(false)" @esc="settleDiscard(false)">切换后将丢弃当前表单，已保存的配置不受影响。</NModal>
  </section>
</template>

<style scoped>
.model-page { max-width:1304px; margin:0 auto; padding:32px; }
.model-page__heading { display:flex; align-items:center; justify-content:space-between; gap:16px; margin-bottom:26px; }
.model-page__heading h1 { margin:0 0 7px; }
.model-page__heading p { color:var(--muted); margin:0; font-size:14px; }
.model-page__layout { display:grid; grid-template-columns:300px minmax(0,1fr); gap:24px; align-items:start; }
.model-page__notice { margin-bottom:16px; }
.profile-editor { min-width:0; padding:26px; border:1px solid var(--line); background:var(--surface); border-radius:12px; }
.profile-editor__heading { display:flex; justify-content:space-between; align-items:flex-start; gap:16px; margin-bottom:24px; }
.profile-editor__heading h2 { font-size:18px; margin:0 0 8px; overflow-wrap:anywhere; }
.profile-editor__tags { display:flex; gap:6px; flex-wrap:wrap; flex-shrink:0; }
.profile-meta { color:var(--muted); font-size:12px; line-height:1.7; margin:8px 0 0; }
.profile-session-notice { margin-bottom:24px; }
.profile-test { border-top:1px solid var(--line); margin-top:24px; padding-top:20px; }
.profile-test__buttons { display:flex; gap:10px; flex-wrap:wrap; }
.profile-test :deep(.n-alert) { margin-top:14px; }
.profile-test p { margin-bottom:0; }
.profile-actions { display:flex; justify-content:space-between; gap:14px; flex-wrap:wrap; border-top:1px solid var(--line); margin-top:22px; padding-top:20px; }
.profile-actions>div { display:flex; flex-wrap:wrap; gap:8px; }
.profile-empty { display:grid; place-content:center; min-height:320px; color:var(--muted); background:var(--surface); border:1px dashed var(--line); border-radius:12px; text-align:center; }
@media (max-width:900px) { .model-page__layout { grid-template-columns:250px minmax(0,1fr); gap:16px; } .profile-editor { padding:20px; } }
@media (max-width:700px) { .model-page{padding:20px 16px;} .model-page__layout { grid-template-columns:1fr; } .profile-editor { padding:18px; } .model-page__heading { align-items:flex-start; } }
</style>
