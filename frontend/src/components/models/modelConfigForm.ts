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

export function validateModelConfigDraft(draft: ModelConfigDraft): string {
  if (!draft.name.trim()) return '请填写配置名称。'
  if (draft.name.trim().length > 100) return '配置名称不能超过 100 个字符。'
  if (!Number.isInteger(draft.settings.llm_timeout_ms) || draft.settings.llm_timeout_ms < 1) return '超时阈值必须是大于 0 的整数毫秒。'
  if (draft.settings.stt_provider === 'cloud') {
    if (!draft.settings.cloud_opt_in) return '启用云端识别前，请授权发送麦克风音频片段。'
    if (!draft.settings.cloud_stt_endpoint.trim()) return '请填写云端语音 Endpoint。'
  }
  if (draft.settings.llm_align_mode !== 'off') {
    if (!draft.settings.llm_base_url.trim()) return '请填写大模型 Base URL。'
    if (!draft.settings.llm_model.trim()) return '请填写模型名称。'
  }
  return ''
}
