<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { fetchSpeechProviders, type SpeechProviderOption } from '../api/providers'

const props = withDefaults(defineProps<{
  modelValue: string
  disabled?: boolean
}>(), { disabled: false })
const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const providers = ref<SpeechProviderOption[]>([])
const loading = ref(false)
const error = ref('')
const selected = computed(() => providers.value.find(provider => provider.id === props.modelValue))

function readSavedProvider() {
  try { return localStorage.getItem('whisper-guide:stt-provider') ?? '' }
  catch { return '' }
}
function saveProvider(id: string) {
  try { localStorage.setItem('whisper-guide:stt-provider', id) }
  catch { /* local preference is optional */ }
}
function choose(id: string) {
  if (!providers.value.some(provider => provider.id === id && provider.available)) return
  emit('update:modelValue', id)
  saveProvider(id)
}
function chooseFromEvent(event: Event) {
  choose((event.target as HTMLSelectElement).value)
}
async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const catalog = await fetchSpeechProviders(AbortSignal.timeout(5000))
    providers.value = catalog.providers
    const preferred = [props.modelValue, readSavedProvider(), catalog.default_provider, 'local']
      .find(id => catalog.providers.some(provider => provider.id === id && provider.available))
    if (preferred && preferred !== props.modelValue) choose(preferred)
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : '无法读取 Provider 服务目录。'
  } finally {
    loading.value = false
  }
}
watch(() => props.modelValue, value => { if (value) saveProvider(value) })
onMounted(() => { void refresh() })
defineExpose({ refresh })
</script>

<template>
  <section class="provider-settings" aria-labelledby="provider-settings-title">
    <div class="provider-settings__heading">
      <div>
        <h3 id="provider-settings-title">识别模型服务</h3>
        <p class="provider-settings__intro">选择后端已配置的 Provider。浏览器只接收能力和隐私说明，不会接触 API 密钥。</p>
      </div>
      <button type="button" class="secondary-button" :disabled="disabled || loading" @click="refresh">
        {{ loading ? '读取中…' : '刷新服务目录' }}
      </button>
    </div>
    <label class="provider-select" for="configured-provider">本次会话使用</label>
    <select
      id="configured-provider"
      :value="modelValue"
      :disabled="disabled || loading"
      @change="chooseFromEvent"
    >
      <option v-if="loading" value="" disabled>正在读取服务目录…</option>
      <option v-for="provider in providers" :key="provider.id" :value="provider.id" :disabled="!provider.available">
        {{ provider.label }}{{ provider.available ? '' : '（未配置）' }}
      </option>
      <option v-if="!providers.length && !loading" value="" disabled>暂无可用 Provider</option>
    </select>
    <div v-if="selected" class="provider-settings__details" aria-live="polite">
      <div class="provider-settings__status">
        <strong>{{ selected.label }}</strong>
        <span class="provider-badge" :class="selected.available ? 'provider-badge--ready' : 'provider-badge--off'">
          {{ selected.available ? '可用' : '未配置' }}
        </span>
      </div>
      <p v-if="selected.privacy">隐私：{{ selected.privacy }}</p>
      <p v-if="selected.model">模型：{{ selected.model }}</p>
      <p>{{ selected.supports_streaming ? '支持流式识别' : '不支持流式识别' }}<span v-if="selected.requires_opt_in"> · 需要明确云端授权</span></p>
    </div>
    <p v-if="error" class="form-error" role="alert">{{ error }}</p>
    <p class="input-help">新增 Provider、endpoint、模型和密钥请在服务端环境变量中配置；前端不会持久化或上传密钥。</p>
  </section>
</template>


