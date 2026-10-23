<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  defaultLlmPresets,
  fetchModelSettings,
  saveModelSettings,
  testModelSettings,
  type LlmPreset,
  type ModelSettings,
  type ModelSettingsInput,
} from '../api/models'

const props = withDefaults(defineProps<{ disabled?: boolean }>(), { disabled: false })
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const error = ref('')
const message = ref('')
const testMessage = ref('')
const testOk = ref<boolean | null>(null)
const presets = ref<LlmPreset[]>(defaultLlmPresets())
const loaded = ref<ModelSettings>()
const requestController = ref<AbortController>()

const form = reactive<ModelSettingsInput>({
  stt_provider: 'local',
  cloud_opt_in: false,
  cloud_stt_endpoint: '',
  cloud_stt_app_id: '',
  cloud_stt_access_token: '',
  llm_align_mode: 'off',
  llm_provider_preset: 'custom',
  llm_base_url: '',
  llm_model: '',
  llm_api_key: '',
  llm_timeout_ms: 1500,
})

const selectedPreset = computed(() => presets.value.find((preset) => preset.id === form.llm_provider_preset))
const cloudEnabled = computed(() => form.stt_provider === 'cloud')
const validTimeout = computed(() => Number.isInteger(form.llm_timeout_ms) && form.llm_timeout_ms > 0)
const canSubmit = computed(() => !props.disabled && !loading.value && !saving.value && validTimeout.value)

function applySettings(settings: ModelSettings) {
  loaded.value = settings
  presets.value = settings.llm_presets.length ? settings.llm_presets : defaultLlmPresets()
  form.stt_provider = settings.stt_provider === 'cloud' ? 'cloud' : 'local'
  form.cloud_opt_in = settings.cloud_opt_in
  form.cloud_stt_endpoint = settings.cloud_stt_endpoint
  form.cloud_stt_app_id = settings.cloud_stt_app_id
  form.cloud_stt_access_token = ''
  form.llm_align_mode = settings.llm_align_mode
  form.llm_provider_preset = settings.llm_provider_preset || 'custom'
  form.llm_base_url = settings.llm_base_url
  form.llm_model = settings.llm_model
  form.llm_api_key = ''
  form.llm_timeout_ms = settings.llm_timeout_ms
}

function inputPayload(): ModelSettingsInput {
  return {
    stt_provider: form.stt_provider,
    cloud_opt_in: form.cloud_opt_in,
    cloud_stt_endpoint: form.cloud_stt_endpoint.trim(),
    cloud_stt_app_id: form.cloud_stt_app_id.trim(),
    cloud_stt_access_token: form.cloud_stt_access_token?.trim(),
    llm_align_mode: form.llm_align_mode,
    llm_provider_preset: form.llm_provider_preset,
    llm_base_url: form.llm_base_url.trim(),
    llm_model: form.llm_model.trim(),
    llm_api_key: form.llm_api_key?.trim(),
    llm_timeout_ms: form.llm_timeout_ms,
  }
}

async function refresh() {
  requestController.value?.abort()
  const controller = new AbortController()
  requestController.value = controller
  loading.value = true
  error.value = ''
  message.value = ''
  try {
    applySettings(await fetchModelSettings(controller.signal))
  } catch (reason) {
    if (!controller.signal.aborted) error.value = reason instanceof Error ? reason.message : '无法读取模型配置。'
  } finally {
    if (requestController.value === controller) loading.value = false
  }
}

function choosePreset(event: Event) {
  const id = (event.target as HTMLSelectElement).value
  form.llm_provider_preset = id
  const preset = presets.value.find((item) => item.id === id)
  if (!preset) return
  form.llm_base_url = preset.base_url
  form.llm_model = preset.model
}

async function save() {
  if (!canSubmit.value) return
  if (!form.llm_base_url.trim() && form.llm_align_mode !== 'off') {
    error.value = '启用大模型对齐时必须填写 Base URL。'
    return
  }
  saving.value = true
  error.value = ''
  message.value = ''
  try {
    applySettings(await saveModelSettings(inputPayload()))
    message.value = '模型配置已保存，新会话将使用最新设置。'
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '保存模型配置失败。'
  } finally { saving.value = false }
}

async function testConnection() {
  if (!canSubmit.value) return
  if (form.llm_align_mode !== 'off' && (!form.llm_base_url.trim() || !form.llm_model.trim())) {
    testOk.value = false
    testMessage.value = '请先填写大模型 Base URL 和模型名称。'
    return
  }
  testing.value = true
  testOk.value = null
  testMessage.value = ''
  try {
    const result = await testModelSettings(inputPayload())
    testOk.value = result.ok
    testMessage.value = result.latency_ms == null ? result.message : `${result.message}（${result.latency_ms} ms）`
  } catch (reason) {
    testOk.value = false
    testMessage.value = reason instanceof Error ? reason.message : '连通性测试失败。'
  } finally { testing.value = false }
}

onMounted(() => { void refresh() })
onBeforeUnmount(() => requestController.value?.abort())
defineExpose({ refresh })
</script>

<template>
  <section class="model-config" aria-labelledby="model-config-title" :aria-busy="loading || saving || testing">
    <div class="section-heading">
      <div>
        <h2 id="model-config-title">模型与识别引擎</h2>
        <p class="model-config__intro">凭据只提交到后端并由后端保存；接口返回的密钥始终脱敏。</p>
      </div>
      <button type="button" class="secondary-button" :disabled="props.disabled || loading" @click="refresh">
        {{ loading ? '读取中…' : '刷新配置' }}
      </button>
    </div>

    <form class="model-config__form" @submit.prevent="save">
      <fieldset :disabled="props.disabled || loading || saving || testing">
        <legend>语音识别源</legend>
        <label for="stt-provider">STT Provider</label>
        <select id="stt-provider" v-model="form.stt_provider">
          <option value="local">本地 sherpa-onnx（离线）</option>
          <option value="cloud">云端语音接口（需授权）</option>
        </select>
        <label class="model-config__check"><input v-model="form.cloud_opt_in" type="checkbox" :disabled="!cloudEnabled" /> 我同意将麦克风音频片段发送至云端处理（不持久化录音）</label>
        <div v-if="cloudEnabled" class="model-config__grid">
          <label>Cloud Endpoint<input v-model="form.cloud_stt_endpoint" type="url" autocomplete="url" placeholder="https://…" /></label>
          <label>App ID<input v-model="form.cloud_stt_app_id" type="text" autocomplete="off" /></label>
          <label class="model-config__wide">Access Token<input v-model="form.cloud_stt_access_token" type="password" autocomplete="new-password" :placeholder="loaded?.cloud_stt_token_configured ? (loaded.cloud_stt_token_hint || '已配置，留空以保留') : '未配置'" /></label>
        </div>
      </fieldset>

      <fieldset :disabled="props.disabled || loading || saving || testing">
        <legend>大模型语义对齐</legend>
        <label for="llm-align-mode">对齐模式</label>
        <select id="llm-align-mode" v-model="form.llm_align_mode">
          <option value="off">关闭</option>
          <option value="fallback">低分兜底（本地低置信度时调用）</option>
          <option value="hybrid">混合校准（句末异步协同）</option>
        </select>
        <div class="model-config__grid">
          <label>服务商预设<select :value="form.llm_provider_preset" @change="choosePreset"><option v-for="preset in presets" :key="preset.id" :value="preset.id">{{ preset.label }}</option></select></label>
          <label>模型名称<input v-model="form.llm_model" type="text" autocomplete="off" placeholder="由服务商提供的模型名" /></label>
          <label class="model-config__wide">Base URL<input v-model="form.llm_base_url" type="url" autocomplete="url" placeholder="https://host/v1" /></label>
          <label class="model-config__wide">API Key<input v-model="form.llm_api_key" type="password" autocomplete="new-password" :placeholder="loaded?.llm_api_key_configured ? (loaded.llm_api_key_hint || '已配置，留空以保留') : '未配置'" /></label>
          <label>超时阈值（ms）<input v-model.number="form.llm_timeout_ms" type="number" min="1" step="1" inputmode="numeric" /></label>
        </div>
        <p v-if="selectedPreset?.description" class="input-help">{{ selectedPreset.description }}</p>
      </fieldset>

      <p v-if="error" class="form-error" role="alert">{{ error }}</p>
      <p v-if="message" class="form-message" role="status">{{ message }}</p>
      <p v-if="testMessage" class="model-config__test" :class="{ 'model-config__test--error': testOk === false }" role="status">{{ testing ? '测试中…' : testMessage }}</p>
      <div class="model-config__actions">
        <button type="button" class="secondary-button" :disabled="!canSubmit || testing" @click="testConnection">{{ testing ? '测试中…' : '测试连通性' }}</button>
        <button type="submit" :disabled="!canSubmit">{{ saving ? '保存中…' : '保存配置' }}</button>
      </div>
    </form>
  </section>
</template>

