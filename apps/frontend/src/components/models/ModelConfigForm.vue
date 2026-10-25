<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { NAlert, NButton } from 'naive-ui'
import { DEFAULT_STT_RESOURCE_ID, VOLCENGINE_STT_ENDPOINT, type LlmPreset } from '../../api/models.ts'
import type { ModelConfig, ModelConfigInput, ModelDiscoveryResult } from '../../api/modelConfigs.ts'

const draft = defineModel<ModelConfigInput>({ required: true })
const props = defineProps<{
  profile?: ModelConfig
  presets: LlmPreset[]
  busy?: boolean
  discoveryBusy?: boolean
  discoveryResult?: ModelDiscoveryResult
  discoveryError?: string
}>()
const emit = defineEmits<{ save: []; discover: [] }>()
const modelSearch = ref('')
const websocketStt = computed(() => draft.value.settings.cloud_stt_protocol === 'volcengine-ws')
const apiKeyAuth = computed(() => websocketStt.value && draft.value.settings.cloud_stt_auth_mode === 'api-key')
const optimizedEndpoint = computed(() => {
  try { return new URL(draft.value.settings.cloud_stt_endpoint).pathname.replace(/\/+$/, '').endsWith('/bigmodel_async') }
  catch { return false }
})
watch(() => draft.value.settings.cloud_stt_protocol, (protocol) => {
  if (protocol === 'volcengine-ws' && !draft.value.settings.cloud_stt_endpoint.trim()) draft.value.settings.cloud_stt_endpoint = VOLCENGINE_STT_ENDPOINT
})

function fillSttDefaults() {
  draft.value.settings.cloud_stt_endpoint = VOLCENGINE_STT_ENDPOINT
  draft.value.settings.cloud_stt_auth_mode = 'api-key'
  draft.value.settings.cloud_stt_resource_id = DEFAULT_STT_RESOURCE_ID
  draft.value.settings.cloud_stt_timeout_ms = 5000
  draft.value.settings.cloud_stt_enable_nonstream = true
}
const filteredModels = computed(() => {
  const query = modelSearch.value.trim().toLocaleLowerCase()
  return (props.discoveryResult?.models ?? []).filter((model) => `${model.id} ${model.name ?? ''}`.toLocaleLowerCase().includes(query))
})
const canDiscover = computed(() => {
  try { return ['http:', 'https:'].includes(new URL(draft.value.settings.llm_base_url.trim()).protocol) }
  catch { return false }
})
watch(() => props.discoveryResult, () => { modelSearch.value = '' })

function chooseDiscoveredModel(event: Event) {
  const value = (event.target as HTMLSelectElement).value
  if (value) draft.value.settings.llm_model = value
}

function choosePreset(event: Event) {
  const preset = props.presets.find((item) => item.id === (event.target as HTMLSelectElement).value)
  if (!preset) return
  draft.value.settings.llm_provider_preset = preset.id
  if (preset.id !== 'custom') {
    draft.value.settings.llm_base_url = preset.base_url
    draft.value.settings.llm_model = preset.model
  }
}

function clearSecret(kind: 'llm' | 'stt') {
  if (kind === 'llm') {
    draft.value.settings.clear_llm_api_key = !draft.value.settings.clear_llm_api_key
    draft.value.settings.llm_api_key = ''
  } else {
    draft.value.settings.clear_cloud_stt_access_token = !draft.value.settings.clear_cloud_stt_access_token
    draft.value.settings.cloud_stt_access_token = ''
  }
}
</script>

<template>
  <form id="model-profile-form" class="profile-form" @submit.prevent="emit('save')">
    <fieldset :disabled="busy || profile?.is_default">
      <legend>基本信息</legend>
      <label for="config-name">配置名称 <span aria-hidden="true">*</span></label>
      <input id="config-name" v-model="draft.name" required maxlength="100" autocomplete="off" placeholder="例如：本地识别 + 语义兜底" />
      <label for="config-description">说明 <span class="optional">可选</span></label>
      <input id="config-description" v-model="draft.description" maxlength="500" autocomplete="off" placeholder="这套配置的用途" />
    </fieldset>

    <fieldset :disabled="busy || profile?.is_default">
      <legend>语音识别源</legend>
      <label for="profile-stt-provider">STT Provider</label>
      <select id="profile-stt-provider" v-model="draft.settings.stt_provider"><option value="local">本地 sherpa-onnx</option><option value="cloud">云端语音接口</option></select>
      <p v-if="draft.settings.stt_provider === 'local'" class="field-help">麦克风音频在本机识别；本服务不保存录音。</p>
      <template v-else>
        <label for="profile-stt-protocol">语音接口协议</label>
        <select id="profile-stt-protocol" v-model="draft.settings.cloud_stt_protocol"><option value="http-json">通用 HTTP JSON 网关</option><option value="volcengine-ws">火山语音 WebSocket</option></select>
        <div v-if="websocketStt" class="stt-preset"><NButton size="small" @click="fillSttDefaults">填入推荐设置</NButton><span class="field-help">流式识别 2.0 · 小时制资源</span></div>
        <label for="profile-stt-endpoint">{{ websocketStt ? '语音 WebSocket Endpoint' : '语音网关 Endpoint' }}</label>
        <input id="profile-stt-endpoint" v-model="draft.settings.cloud_stt_endpoint" type="url" autocomplete="url" :placeholder="websocketStt ? VOLCENGINE_STT_ENDPOINT : 'https://gateway.example/stt'" />
        <p class="field-help">{{ websocketStt ? '直接连接火山语音流式识别；推荐使用 wss:// 加密连接。' : '使用兼容项目语音协议的接口。' }} 聊天模型 Base URL 不能用于此处。</p>
        <template v-if="websocketStt">
          <label for="profile-stt-resource">语音服务 Resource ID</label>
          <input id="profile-stt-resource" v-model="draft.settings.cloud_stt_resource_id" list="profile-stt-resources" autocomplete="off" :placeholder="DEFAULT_STT_RESOURCE_ID" />
          <datalist id="profile-stt-resources"><option value="volc.seedasr.sauc.duration">流式识别 2.0 · 小时制</option><option value="volc.seedasr.sauc.concurrent">流式识别 2.0 · 并发制</option><option value="volc.bigasr.sauc.duration">流式识别 1.0 · 小时制</option><option value="volc.bigasr.sauc.concurrent">流式识别 1.0 · 并发制</option></datalist>
          <p class="field-help">请选择语音控制台已开通的资源，支持手动填写。</p>
          <label for="profile-stt-auth">语音鉴权方式</label>
          <select id="profile-stt-auth" v-model="draft.settings.cloud_stt_auth_mode"><option value="api-key">新版语音 API Key</option><option value="app-token">旧版 App ID + Access Token</option></select>
        </template>
        <template v-if="!apiKeyAuth">
          <label for="profile-stt-app-id">App ID <span class="optional">{{ websocketStt ? '必填，来自语音控制台' : '依服务商要求填写' }}</span></label>
          <input id="profile-stt-app-id" v-model="draft.settings.cloud_stt_app_id" :required="websocketStt" autocomplete="off" />
        </template>
        <div class="secret-label"><label for="profile-stt-token">{{ apiKeyAuth ? '语音服务 API Key' : 'Access Token' }}</label><NButton v-if="profile?.settings.cloud_stt_token_configured" text size="small" :type="draft.settings.clear_cloud_stt_access_token ? 'default' : 'error'" @click="clearSecret('stt')">{{ draft.settings.clear_cloud_stt_access_token ? '撤销清除' : '清除凭据' }}</NButton></div>
        <input id="profile-stt-token" v-model="draft.settings.cloud_stt_access_token" type="password" autocomplete="new-password" :disabled="draft.settings.clear_cloud_stt_access_token" :placeholder="profile?.settings.cloud_stt_token_configured ? '已保存，留空保留' : '尚未配置'" aria-describedby="stt-token-help" />
        <p id="stt-token-help" class="field-help">{{ draft.settings.clear_cloud_stt_access_token ? '保存后将删除已存语音凭据。' : '凭据仅保存于后端，留空保留已存值。' }} 语音服务与下方火山方舟文本模型使用各自的凭据，不会自动复用。</p>
        <label for="profile-stt-timeout">语音请求超时 (ms)</label>
        <input id="profile-stt-timeout" v-model.number="draft.settings.cloud_stt_timeout_ms" type="number" min="200" max="30000" step="1" required class="timeout-input" />
        <p class="field-help">范围 200–30000 ms；与大模型语义对齐超时独立。</p>
        <template v-if="websocketStt">
          <label class="check-field"><input id="profile-stt-nonstream" v-model="draft.settings.cloud_stt_enable_nonstream" type="checkbox" :disabled="!optimizedEndpoint" /><span>启用二遍复核（仅优化版 bigmodel_async 接口有效）</span></label>
          <p v-if="!optimizedEndpoint" class="field-help">当前 Endpoint 不支持此项，服务端将忽略二遍复核设置。</p>
        </template>
        <label class="check-field"><input v-model="draft.settings.cloud_opt_in" type="checkbox" /><span>我同意将麦克风音频片段加密发送至所配置云端处理；本服务不保存录音。云端留存规则以服务商政策为准。</span></label>
      </template>
    </fieldset>

    <fieldset :disabled="busy || profile?.is_default">
      <legend>大模型语义对齐</legend>
      <label for="profile-align-mode">对齐模式</label>
      <select id="profile-align-mode" v-model="draft.settings.llm_align_mode"><option value="off">关闭</option><option value="fallback">低分兜底</option><option value="hybrid">混合校准</option></select>
      <div class="llm-settings">
        <p v-if="draft.settings.llm_align_mode !== 'off'" class="field-help">讲稿窗口与识别文字会发送至模型接口。游标仍由本地对齐器校验后更新。</p>
        <p v-else class="field-help">当前关闭会话中的大模型调用。仍可填写接口并获取模型列表，选好模型后再启用对齐。</p>
        <div class="profile-form__columns">
          <div><label for="profile-llm-preset">服务商预设</label><select id="profile-llm-preset" :value="draft.settings.llm_provider_preset" @change="choosePreset"><option v-for="preset in presets" :key="preset.id" :value="preset.id">{{ preset.label }}</option></select></div>
          <div><label for="profile-llm-model">模型名称</label><input id="profile-llm-model" v-model="draft.settings.llm_model" autocomplete="off" placeholder="模型名或接入点 ID" /></div>
        </div>
        <label for="profile-llm-url">Base URL</label>
        <input id="profile-llm-url" v-model="draft.settings.llm_base_url" type="url" autocomplete="url" placeholder="https://host/v1" />
        <p class="field-help">获取列表时，后端会将 API Key 发送至此地址。修改地址前请确认目标服务可信。</p>
        <div class="secret-label"><label for="profile-llm-key">API Key</label><NButton v-if="profile?.settings.llm_api_key_configured" text size="small" :type="draft.settings.clear_llm_api_key ? 'default' : 'error'" @click="clearSecret('llm')">{{ draft.settings.clear_llm_api_key ? '撤销清除' : '清除凭据' }}</NButton></div>
        <input id="profile-llm-key" v-model="draft.settings.llm_api_key" type="password" autocomplete="new-password" :disabled="draft.settings.clear_llm_api_key" :placeholder="profile?.settings.llm_api_key_configured ? '已保存，留空保留' : '尚未配置'" aria-describedby="llm-key-help" />
        <p id="llm-key-help" class="field-help">{{ draft.settings.clear_llm_api_key ? '保存后将删除已存 API Key；本次获取列表也不会使用该凭据。' : '留空保留原凭据；获取列表可复用已存密钥，新输入的密钥仅在保存后替换。' }}</p>
        <div class="model-discovery">
          <NButton :disabled="busy || profile?.is_default || !canDiscover || discoveryBusy" :loading="discoveryBusy" @click="emit('discover')">获取模型列表</NButton>
          <p class="field-help">使用当前 Base URL 和凭据查询，不保存或激活配置；不支持列表接口时可手动填写模型名称。</p>
          <p v-if="discoveryBusy" role="status" class="field-help">正在获取模型列表…修改接口或凭据将取消此次请求。</p>
          <NAlert v-if="discoveryError" type="error" :show-icon="false" role="alert">{{ discoveryError }}</NAlert>
          <NAlert v-if="discoveryResult" :type="discoveryResult.ok ? 'success' : 'error'" :show-icon="false" :role="discoveryResult.ok ? 'status' : 'alert'">
            {{ discoveryResult.message }}<span v-if="discoveryResult.latency_ms !== null">（{{ discoveryResult.latency_ms }} ms）</span>
            <p v-if="discoveryResult.truncated">模型列表较长，当前仅显示部分条目；未找到时请手动填写完整模型 ID。</p>
          </NAlert>
          <template v-if="discoveryResult?.ok && discoveryResult.models.length">
            <label for="profile-llm-model-search">筛选模型</label>
            <input id="profile-llm-model-search" v-model="modelSearch" type="search" autocomplete="off" placeholder="按模型 ID 或名称搜索" />
            <label for="profile-discovered-model">从模型列表选择</label>
            <select id="profile-discovered-model" :value="draft.settings.llm_model" @change="chooseDiscoveredModel">
              <option value="">选择模型以填入名称</option>
              <option v-for="model in filteredModels" :key="model.id" :value="model.id">{{ model.name && model.name !== model.id ? `${model.name} · ${model.id}` : model.id }}</option>
            </select>
            <p class="field-help">显示 {{ filteredModels.length }} / {{ discoveryResult.models.length }} 个模型。选择后仅修改当前草稿。</p>
            <p v-if="!filteredModels.length" role="status" class="field-help">没有匹配的模型；可清空筛选或直接手动输入。</p>
          </template>
        </div>
      </div>
      <label for="profile-timeout">请求超时（ms）</label>
      <input id="profile-timeout" v-model.number="draft.settings.llm_timeout_ms" class="timeout-input" type="number" min="1" step="1" inputmode="numeric" required />
    </fieldset>
  </form>
</template>

<style scoped>
.profile-form { display:grid; gap:26px; }
fieldset { border:0; margin:0; padding:0; min-width:0; }
legend { font-size:15px; font-weight:650; padding:0 0 14px; width:100%; border-bottom:1px solid var(--line); margin-bottom:16px; }
label { display:block; font-size:13px; font-weight:550; margin:16px 0 7px; }
label:first-of-type { margin-top:0; }
input:not([type='checkbox']),select { display:block; width:100%; min-height:40px; border:1px solid var(--line); border-radius:6px; padding:8px 12px; color:var(--ink); background:var(--surface); font:inherit; box-sizing:border-box; }
input:disabled,select:disabled { opacity:.6; }
.optional { color:var(--muted); font-size:12px; font-weight:400; margin-left:6px; }
.field-help { font-size:12px; line-height:1.7; color:var(--muted); margin:7px 0 0; }
.stt-preset { display:flex; gap:10px; flex-wrap:wrap; align-items:center; margin-top:10px; }
.profile-form__columns { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:16px; }
.profile-form__columns + label { margin-top:16px; }
.secret-label { display:flex; justify-content:space-between; align-items:center; margin:16px 0 7px; }
.secret-label label { margin:0; }
.check-field { display:flex; align-items:flex-start; gap:9px; font-weight:400; line-height:1.7; }
.check-field input { margin-top:5px; accent-color:var(--accent); }
.timeout-input { max-width:220px; }
.llm-settings { margin-top:16px; }
.model-discovery { margin-top:16px; }
.model-discovery :deep(.n-alert) { margin-top:12px; }
.model-discovery :deep(.n-alert p) { margin:6px 0 0; }
@media (max-width:600px) { .profile-form__columns { grid-template-columns:1fr; } }
</style>
