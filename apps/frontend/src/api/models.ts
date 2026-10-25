/** Runtime model configuration shared by the settings panel and the API. */

export type LlmAlignMode = 'off' | 'fallback' | 'hybrid'
export type CloudSttProtocol = 'http-json' | 'volcengine-ws'
export type CloudSttAuthMode = 'api-key' | 'app-token'

export const VOLCENGINE_STT_ENDPOINT = 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async'
export const DEFAULT_STT_RESOURCE_ID = 'volc.seedasr.sauc.duration'

export interface LlmPreset {
  id: string
  label: string
  base_url: string
  model: string
  description?: string
}

export interface ModelSettings {
  stt_provider: string
  cloud_opt_in: boolean
  cloud_stt_endpoint: string
  cloud_stt_protocol: CloudSttProtocol
  cloud_stt_auth_mode: CloudSttAuthMode
  cloud_stt_resource_id: string
  cloud_stt_timeout_ms: number
  cloud_stt_enable_nonstream: boolean
  cloud_stt_app_id: string
  cloud_stt_token_configured: boolean
  cloud_stt_token_hint: string
  llm_align_mode: LlmAlignMode
  llm_provider_preset: string
  llm_base_url: string
  llm_model: string
  llm_api_key_configured: boolean
  llm_api_key_hint: string
  llm_timeout_ms: number
  llm_presets: LlmPreset[]
}

export interface ModelSettingsInput {
  stt_provider: string
  cloud_opt_in: boolean
  cloud_stt_endpoint: string
  cloud_stt_protocol: CloudSttProtocol
  cloud_stt_auth_mode: CloudSttAuthMode
  cloud_stt_resource_id: string
  cloud_stt_timeout_ms: number
  cloud_stt_enable_nonstream: boolean
  cloud_stt_app_id: string
  /** Empty values are omitted before sending so an existing secret is retained. */
  cloud_stt_access_token?: string
  llm_align_mode: LlmAlignMode
  llm_provider_preset: string
  llm_base_url: string
  llm_model: string
  /** Empty values are omitted before sending so an existing secret is retained. */
  llm_api_key?: string
  llm_timeout_ms: number
}

const DEFAULT_PRESETS: LlmPreset[] = [
  { id: 'custom', label: '自定义（OpenAI 兼容）', base_url: '', model: '', description: '填写任意兼容 /chat/completions 的服务。' },
  { id: 'deepseek', label: 'DeepSeek', base_url: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  { id: 'qwen', label: '通义千问', base_url: 'https://dashscope.aliyuncs.com/compatible-mode/v1', model: 'qwen-turbo' },
  { id: 'doubao', label: '豆包 / 火山方舟', base_url: 'https://ark.cn-beijing.volces.com/api/v3', model: 'doubao-1-5-lite-32k' },
  { id: 'openai', label: 'OpenAI', base_url: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  { id: 'ollama', label: 'Ollama（局域网）', base_url: 'http://localhost:11434/v1', model: 'qwen2.5:7b' },
]

function record(value: unknown): Record<string, unknown> {
  return typeof value === 'object' && value !== null ? value as Record<string, unknown> : {}
}

function stringValue(value: unknown, fallback = ''): string {
  return typeof value === 'string' ? value : fallback
}

function booleanValue(value: unknown, fallback = false): boolean {
  return typeof value === 'boolean' ? value : fallback
}

function positiveInteger(value: unknown, fallback: number): number {
  const parsed = typeof value === 'number' ? value : Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : fallback
}

function alignMode(value: unknown): LlmAlignMode {
  return value === 'fallback' || value === 'hybrid' ? value : 'off'
}

function normalizePreset(value: unknown): LlmPreset | undefined {
  const item = record(value)
  const id = stringValue(item.id).trim()
  if (!id) return undefined
  return {
    id,
    label: stringValue(item.label, id),
    base_url: stringValue(item.base_url ?? item.baseUrl),
    model: stringValue(item.model),
    description: stringValue(item.description) || undefined,
  }
}

export function defaultLlmPresets(): LlmPreset[] {
  return DEFAULT_PRESETS.map((preset) => ({ ...preset }))
}

/** Normalizes both the current flat response and nested STT/LLM responses. */
export function normalizeModelSettings(payload: unknown): ModelSettings {
  const root = record(payload)
  const stt = record(root.stt)
  const llm = record(root.llm)
  const rawPresets = root.llm_presets ?? root.presets ?? llm.presets
  const presets = Array.isArray(rawPresets)
    ? rawPresets.flatMap((item) => { const preset = normalizePreset(item); return preset ? [preset] : [] })
    : []
  const mergedPresets = presets.length ? presets : defaultLlmPresets()
  const tokenHint = stringValue(root.cloud_stt_access_token_hint ?? root.cloud_access_token_hint ?? root.access_token ?? stt.access_token_hint ?? stt.access_token)
  const apiKeyHint = stringValue(root.llm_api_key_hint ?? root.api_key_hint ?? root.llm_api_key ?? root.api_key ?? llm.api_key_hint ?? llm.api_key)
  return {
    stt_provider: stringValue(root.stt_provider ?? stt.provider, 'local'),
    cloud_opt_in: booleanValue(root.cloud_opt_in ?? stt.opt_in),
    cloud_stt_endpoint: stringValue(root.cloud_stt_endpoint ?? root.cloud_endpoint ?? stt.endpoint),
    cloud_stt_app_id: stringValue(root.cloud_stt_app_id ?? root.cloud_app_id ?? stt.app_id),
    cloud_stt_protocol: (root.cloud_stt_protocol ?? stt.protocol) === 'volcengine-ws' ? 'volcengine-ws' : 'http-json',
    cloud_stt_auth_mode: (root.cloud_stt_auth_mode ?? stt.auth_mode) === 'app-token' ? 'app-token' : 'api-key',
    cloud_stt_resource_id: stringValue(root.cloud_stt_resource_id ?? stt.resource_id, DEFAULT_STT_RESOURCE_ID),
    cloud_stt_timeout_ms: positiveInteger(root.cloud_stt_timeout_ms ?? stt.timeout_ms, 5000),
    cloud_stt_enable_nonstream: booleanValue(root.cloud_stt_enable_nonstream ?? stt.enable_nonstream, true),
    cloud_stt_token_configured: booleanValue(root.cloud_stt_token_configured ?? root.has_cloud_stt_access_token ?? root.has_access_token ?? stt.token_configured ?? stt.has_access_token) || Boolean(tokenHint),
    cloud_stt_token_hint: tokenHint,
    llm_align_mode: alignMode(root.llm_align_mode ?? llm.align_mode),
    llm_provider_preset: stringValue(root.llm_provider_preset ?? llm.provider_preset, 'custom'),
    llm_base_url: stringValue(root.llm_base_url ?? llm.base_url),
    llm_model: stringValue(root.llm_model ?? llm.model),
    llm_api_key_configured: booleanValue(root.llm_api_key_configured ?? root.has_llm_api_key ?? root.has_api_key ?? llm.api_key_configured ?? llm.has_api_key) || Boolean(apiKeyHint),
    llm_api_key_hint: apiKeyHint,
    llm_timeout_ms: positiveInteger(root.llm_timeout_ms ?? llm.timeout_ms, 1500),
    llm_presets: mergedPresets,
  }
}

export function apiBase() {
  return (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
}
