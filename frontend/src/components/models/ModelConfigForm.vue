<script setup lang="ts">
import { NButton } from 'naive-ui'
import type { LlmPreset } from '../../api/models.ts'
import type { ModelConfig } from '../../api/modelConfigs.ts'
import type { ModelConfigDraft } from './modelConfigForm.ts'

const draft = defineModel<ModelConfigDraft>({ required: true })
const props = defineProps<{ profile?: ModelConfig; presets: LlmPreset[]; busy?: boolean }>()
const emit = defineEmits<{ save: [] }>()

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
        <label for="profile-stt-endpoint">语音网关 Endpoint</label>
        <input id="profile-stt-endpoint" v-model="draft.settings.cloud_stt_endpoint" type="url" autocomplete="url" placeholder="https://gateway.example/stt" />
        <p class="field-help">使用兼容项目语音协议的接口；聊天模型 Base URL 不能用于此处。</p>
        <label for="profile-stt-app-id">App ID <span class="optional">依服务商要求填写</span></label>
        <input id="profile-stt-app-id" v-model="draft.settings.cloud_stt_app_id" autocomplete="off" />
        <div class="secret-label"><label for="profile-stt-token">Access Token</label><NButton v-if="profile?.settings.cloud_stt_token_configured" text size="small" :type="draft.settings.clear_cloud_stt_access_token ? 'default' : 'error'" @click="clearSecret('stt')">{{ draft.settings.clear_cloud_stt_access_token ? '撤销清除' : '清除凭据' }}</NButton></div>
        <input id="profile-stt-token" v-model="draft.settings.cloud_stt_access_token" type="password" autocomplete="new-password" :disabled="draft.settings.clear_cloud_stt_access_token" :placeholder="profile?.settings.cloud_stt_token_configured ? '已保存，留空保留' : '尚未配置'" aria-describedby="stt-token-help" />
        <p id="stt-token-help" class="field-help">{{ draft.settings.clear_cloud_stt_access_token ? '保存后将删除已存 Token。' : '凭据仅保存于后端，页面不会读取原始值。' }}</p>
        <label class="check-field"><input v-model="draft.settings.cloud_opt_in" type="checkbox" /><span>我同意将麦克风音频片段加密发送至所配置云端处理；本服务不保存录音。云端留存规则以服务商政策为准。</span></label>
      </template>
    </fieldset>

    <fieldset :disabled="busy || profile?.is_default">
      <legend>大模型语义对齐</legend>
      <label for="profile-align-mode">对齐模式</label>
      <select id="profile-align-mode" v-model="draft.settings.llm_align_mode"><option value="off">关闭</option><option value="fallback">低分兜底</option><option value="hybrid">混合校准</option></select>
      <template v-if="draft.settings.llm_align_mode !== 'off'">
        <p class="field-help">讲稿窗口与识别文字会发送至模型接口。游标仍由本地对齐器校验后更新。</p>
        <div class="profile-form__columns">
          <div><label for="profile-llm-preset">服务商预设</label><select id="profile-llm-preset" :value="draft.settings.llm_provider_preset" @change="choosePreset"><option v-for="preset in presets" :key="preset.id" :value="preset.id">{{ preset.label }}</option></select></div>
          <div><label for="profile-llm-model">模型名称</label><input id="profile-llm-model" v-model="draft.settings.llm_model" autocomplete="off" placeholder="模型名或接入点 ID" /></div>
        </div>
        <label for="profile-llm-url">Base URL</label>
        <input id="profile-llm-url" v-model="draft.settings.llm_base_url" type="url" autocomplete="url" placeholder="https://host/v1" />
        <div class="secret-label"><label for="profile-llm-key">API Key</label><NButton v-if="profile?.settings.llm_api_key_configured" text size="small" :type="draft.settings.clear_llm_api_key ? 'default' : 'error'" @click="clearSecret('llm')">{{ draft.settings.clear_llm_api_key ? '撤销清除' : '清除凭据' }}</NButton></div>
        <input id="profile-llm-key" v-model="draft.settings.llm_api_key" type="password" autocomplete="new-password" :disabled="draft.settings.clear_llm_api_key" :placeholder="profile?.settings.llm_api_key_configured ? '已保存，留空保留' : '尚未配置'" aria-describedby="llm-key-help" />
        <p id="llm-key-help" class="field-help">{{ draft.settings.clear_llm_api_key ? '保存后将删除已存 API Key。' : '留空保留原凭据；输入新值将在保存后替换。' }}</p>
      </template>
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
.profile-form__columns { display:grid; grid-template-columns:1fr 1fr; gap:16px; margin-top:16px; }
.secret-label { display:flex; justify-content:space-between; align-items:center; margin:16px 0 7px; }
.secret-label label { margin:0; }
.check-field { display:flex; align-items:flex-start; gap:9px; font-weight:400; line-height:1.7; }
.check-field input { margin-top:5px; accent-color:var(--accent); }
.timeout-input { max-width:220px; }
@media (max-width:600px) { .profile-form__columns { grid-template-columns:1fr; } }
</style>
