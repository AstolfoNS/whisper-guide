export interface SpeechProviderOption {
  id: string
  label: string
  kind: string
  available: boolean
  configured: boolean
  privacy: string
  model: string
  supports_streaming: boolean
  requires_opt_in: boolean
}

export interface SpeechProviderCatalog {
  default_provider: string
  providers: SpeechProviderOption[]
  configuration_source: string
}

const fallbackProvider: SpeechProviderOption = {
  id: 'local',
  label: '本地语音识别',
  kind: 'local',
  available: false,
  configured: true,
  privacy: '音频只在本地服务处理',
  model: '',
  supports_streaming: true,
  requires_opt_in: false,
}

function asString(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback
}

function asBoolean(value: unknown, fallback = false) {
  return typeof value === 'boolean' ? value : fallback
}

/** Keep the browser tolerant of older servers while the provider catalog evolves. */
export function normalizeProviderCatalog(payload: unknown): SpeechProviderCatalog {
  const object = typeof payload === 'object' && payload !== null ? payload as Record<string, unknown> : {}
  const rawProviders = Array.isArray(object.providers) ? object.providers : []
  const providers = rawProviders.flatMap((raw) => {
    if (typeof raw !== 'object' || raw === null) return []
    const item = raw as Record<string, unknown>
    const id = asString(item.id).trim()
    if (!id) return []
    return [{
      id,
      label: asString(item.label, id),
      kind: asString(item.kind, id === 'local' ? 'local' : 'cloud'),
      available: asBoolean(item.available),
      configured: asBoolean(item.configured, asBoolean(item.available)),
      privacy: asString(item.privacy, '请查看服务端隐私配置。'),
      model: asString(item.model),
      supports_streaming: asBoolean(item.supports_streaming, true),
      requires_opt_in: asBoolean(item.requires_opt_in),
    } satisfies SpeechProviderOption]
  })
  const normalizedProviders = providers.length ? providers : [fallbackProvider]
  const defaultProvider = asString(object.default_provider, normalizedProviders.find(item => item.available)?.id ?? 'local')
  return {
    default_provider: defaultProvider,
    providers: normalizedProviders,
    configuration_source: asString(object.configuration_source, 'server'),
  }
}

export function apiBase() {
  return (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
}

export async function fetchSpeechProviders(signal?: AbortSignal): Promise<SpeechProviderCatalog> {
  const response = await fetch(apiBase() + '/api/speech/providers', { signal })
  if (!response.ok) throw new Error(`无法获取语音 Provider（HTTP ${response.status}）`)
  return normalizeProviderCatalog(await response.json())
}
