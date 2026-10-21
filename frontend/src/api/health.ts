export interface HealthResponse {
  status: 'ok'
  service?: string
  version?: string
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

export async function fetchHealth(signal: AbortSignal): Promise<HealthResponse> {
  const response = await fetch(`${apiBaseUrl}/api/health`, {
    headers: { Accept: 'application/json' },
    cache: 'no-store',
    signal,
  })

  if (!response.ok) {
    throw new Error(`后端返回 HTTP ${response.status}`)
  }

  const data: unknown = await response.json()
  if (typeof data !== 'object' || data === null || !('status' in data) || data.status !== 'ok') {
    throw new Error('后端健康检查响应格式不正确')
  }

  return {
    status: 'ok',
    service: 'service' in data && typeof data.service === 'string' ? data.service : undefined,
    version: 'version' in data && typeof data.version === 'string' ? data.version : undefined,
  }
}
