import { apiBase, normalizeModelSettings, type LlmPreset, type ModelSettings, type ModelSettingsInput } from './models.ts'

export interface ModelConfig {
  id: string
  name: string
  description: string
  revision: number
  created_at: string
  updated_at: string
  is_default: boolean
  is_active: boolean
  active_revision: number | null
  pending_activation: boolean
  settings: ModelSettings
}

export interface ModelConfigInput {
  name: string
  description: string
  revision?: number
  settings: ModelSettingsInput & {
    clear_llm_api_key?: boolean
    clear_cloud_stt_access_token?: boolean
  }
}

export interface ModelConfigList {
  store_available?: boolean
  configs: ModelConfig[]
  active_profile?: { id: string; name: string; revision: number; settings: ModelSettings }
  active_id: string
  active_revision: number | null
  presets: LlmPreset[]
}

export interface ModelConfigTestResult {
  ok: boolean
  latency_ms: number | null
  message: string
  target: 'stt' | 'llm'
  verified: 'network' | 'capability' | 'none'
  error_code?: 'CONFIG_INCOMPLETE' | 'TIMEOUT' | 'HTTP_ERROR' | 'NETWORK_ERROR' | 'INVALID_RESPONSE' | 'UNKNOWN_ERROR'
  http_status?: number
}

export class ModelConfigError extends Error {
  code: string
  status: number

  constructor(message: string, status: number, code = '') {
    super(message)
    this.name = 'ModelConfigError'
    this.code = code
    this.status = status
  }
}

export function normalizeModelConfig(payload: unknown): ModelConfig {
  const item = payload as Partial<ModelConfig>
  return {
    id: String(item.id ?? ''),
    name: String(item.name ?? ''),
    description: String(item.description ?? ''),
    revision: Number(item.revision ?? 1),
    created_at: String(item.created_at ?? ''),
    updated_at: String(item.updated_at ?? ''),
    is_default: item.is_default === true,
    is_active: item.is_active === true,
    active_revision: item.active_revision == null ? null : Number(item.active_revision),
    pending_activation: item.pending_activation === true,
    settings: normalizeModelSettings(item.settings),
  }
}

/** Blank credential controls preserve a secret. Clear flags are separate user actions. */
export function sanitizeModelConfigInput(input: ModelConfigInput): ModelConfigInput {
  const settings = { ...input.settings }
  if (!settings.llm_api_key?.trim() || /^.*\*{3,}/.test(settings.llm_api_key)) delete settings.llm_api_key
  if (!settings.cloud_stt_access_token?.trim() || /^.*\*{3,}/.test(settings.cloud_stt_access_token)) delete settings.cloud_stt_access_token
  if (settings.clear_llm_api_key) delete settings.llm_api_key
  if (settings.clear_cloud_stt_access_token) delete settings.cloud_stt_access_token
  return { ...input, name: input.name.trim(), description: input.description.trim(), settings }
}

async function request(path = '', init?: RequestInit): Promise<unknown> {
  const response = await fetch(`${apiBase()}/api/settings/model-configs${path}`, init)
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    const detail = body.detail
    throw new ModelConfigError(
      typeof detail === 'string' ? detail : detail?.message ?? `配置请求失败（HTTP ${response.status}）`,
      response.status,
      typeof detail?.code === 'string' ? detail.code : '',
    )
  }
  return response.status === 204 ? undefined : response.json()
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }
}

export async function listModelConfigs(signal?: AbortSignal): Promise<ModelConfigList> {
  const payload = await request('', { signal }) as ModelConfigList
  return {
    ...payload,
    configs: payload.configs.map(normalizeModelConfig),
    active_profile: payload.active_profile ? { ...payload.active_profile, settings: normalizeModelSettings(payload.active_profile.settings) } : undefined,
  }
}

export async function fetchModelConfig(id: string, signal?: AbortSignal): Promise<ModelConfig> {
  return normalizeModelConfig(await request(`/${encodeURIComponent(id)}`, { signal }))
}

export async function createModelConfig(input: ModelConfigInput): Promise<ModelConfig> {
  return normalizeModelConfig(await request('', json('POST', sanitizeModelConfigInput(input))))
}

export async function updateModelConfig(id: string, input: ModelConfigInput): Promise<ModelConfig> {
  return normalizeModelConfig(await request(`/${encodeURIComponent(id)}`, json('PUT', sanitizeModelConfigInput(input))))
}

export async function duplicateModelConfig(id: string): Promise<ModelConfig> {
  return normalizeModelConfig(await request(`/${encodeURIComponent(id)}/duplicate`, json('POST', {})))
}

export async function deleteModelConfig(id: string): Promise<void> {
  await request(`/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

export async function activateModelConfig(id: string, revision: number): Promise<ModelConfig> {
  return normalizeModelConfig(await request(`/${encodeURIComponent(id)}/activate`, json('POST', { revision })))
}

export async function testModelConfig(input: ModelConfigInput, target: 'stt' | 'llm', id?: string): Promise<ModelConfigTestResult> {
  const payload = sanitizeModelConfigInput(input)
  return await request('/test', json('POST', { id, target, settings: payload.settings })) as ModelConfigTestResult
}
