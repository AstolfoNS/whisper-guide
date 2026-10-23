import { normalizeModelSettings } from '../../api/models.ts'
import type { ModelConfig, ModelConfigInput } from '../../api/modelConfigs.ts'

export interface ModelConfigDraft extends ModelConfigInput {
  settings: ModelConfigInput['settings']
}

/** Keep drafts and their secrets in the mounted editor, outside shared/persisted stores. */
export function modelConfigDraft(profile?: ModelConfig): ModelConfigDraft {
  const settings = profile?.settings ?? normalizeModelSettings({})
  return {
    name: profile?.name ?? '',
    description: profile?.description ?? '',
    revision: profile?.revision,
    settings: {
      stt_provider: settings.stt_provider === 'cloud' ? 'cloud' : 'local',
      cloud_opt_in: settings.cloud_opt_in,
      cloud_stt_endpoint: settings.cloud_stt_endpoint,
      cloud_stt_protocol: settings.cloud_stt_protocol,
      cloud_stt_auth_mode: settings.cloud_stt_auth_mode,
      cloud_stt_resource_id: settings.cloud_stt_resource_id,
      cloud_stt_timeout_ms: settings.cloud_stt_timeout_ms,
      cloud_stt_enable_nonstream: settings.cloud_stt_enable_nonstream,
      cloud_stt_app_id: settings.cloud_stt_app_id,
      cloud_stt_access_token: '',
      clear_cloud_stt_access_token: false,
      llm_align_mode: settings.llm_align_mode,
      llm_provider_preset: settings.llm_provider_preset,
      llm_base_url: settings.llm_base_url,
      llm_model: settings.llm_model,
      llm_api_key: '',
      clear_llm_api_key: false,
      llm_timeout_ms: settings.llm_timeout_ms,
    },
  }
}

/** Cloud ASR can be tested before a draft has a name or is saved. */
export function validateCloudSttDraft(draft: ModelConfigDraft, profile?: ModelConfig): string {
  const settings = draft.settings
  if (settings.stt_provider !== 'cloud') return ''
  if (!settings.cloud_opt_in) return '启用云端识别前，请授权发送麦克风音频片段。'
  if (!settings.cloud_stt_endpoint.trim()) return '请填写云端语音 Endpoint。'
  let endpoint: URL
  try { endpoint = new URL(settings.cloud_stt_endpoint.trim()) }
  catch { return '语音 Endpoint 格式无效，请填写完整地址。' }
  const websocket = settings.cloud_stt_protocol === 'volcengine-ws'
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(endpoint.hostname.toLowerCase())
  if (endpoint.username || endpoint.password) return '语音 Endpoint 不能包含用户名或密码，请使用独立凭据字段。'
  if (endpoint.search || endpoint.hash) return '语音 Endpoint 不能包含查询参数或片段，请使用独立凭据字段。'
  if (websocket && endpoint.protocol !== 'wss:' && !(endpoint.protocol === 'ws:' && loopback)) return '火山语音 WebSocket 协议需要 wss:// 加密地址；本机调试可用 ws:// 环回地址。'
  if (!websocket && endpoint.protocol !== 'https:' && !(endpoint.protocol === 'http:' && loopback)) return '通用 HTTP JSON 协议需要 https:// 加密地址；本机调试可用 http:// 环回地址。'
  if (!Number.isInteger(settings.cloud_stt_timeout_ms) || settings.cloud_stt_timeout_ms < 200 || settings.cloud_stt_timeout_ms > 30000) return '语音请求超时须为 200 至 30000 毫秒的整数。'
  if (websocket && !settings.cloud_stt_resource_id.trim()) return '请填写语音服务 Resource ID，或使用推荐设置。'
  if (websocket && settings.cloud_stt_auth_mode === 'app-token' && !settings.cloud_stt_app_id.trim()) return '旧版 App ID + Access Token 鉴权需要填写 App ID。'
  const hasStoredCredential = profile?.settings.cloud_stt_token_configured && !settings.clear_cloud_stt_access_token
  const hasDraftCredential = settings.cloud_stt_access_token?.trim() && !/^.*\*{3,}/.test(settings.cloud_stt_access_token) && !settings.clear_cloud_stt_access_token
  if (!hasDraftCredential && !hasStoredCredential) return websocket && settings.cloud_stt_auth_mode === 'api-key' ? '请填写语音服务 API Key；火山方舟文本模型 Key 不会自动用于语音识别。' : '请填写云端语音 Access Token。'
  return ''
}

export function validateModelConfigDraft(draft: ModelConfigDraft, profile?: ModelConfig): string {
  if (!draft.name.trim()) return '请填写配置名称。'
  if (draft.name.trim().length > 100) return '配置名称不能超过 100 个字符。'
  if (!Number.isInteger(draft.settings.llm_timeout_ms) || draft.settings.llm_timeout_ms < 1) return '超时阈值必须是大于 0 的整数毫秒。'
  const cloudError = validateCloudSttDraft(draft, profile)
  if (cloudError) return cloudError
  if (draft.settings.llm_align_mode !== 'off') {
    if (!draft.settings.llm_base_url.trim()) return '请填写大模型 Base URL。'
    if (!draft.settings.llm_model.trim()) return '请填写模型名称。'
  }
  return ''
}
