export interface ScriptSummary {
  id: string
  name: string
  character_count: number
  created_at: string
}

export interface ScriptDocument extends ScriptSummary {
  content: string
}

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')

function isSummary(value: unknown): value is ScriptSummary {
  return (
    typeof value === 'object' && value !== null &&
    'id' in value && typeof value.id === 'string' &&
    'name' in value && typeof value.name === 'string' &&
    'character_count' in value && typeof value.character_count === 'number' &&
    'created_at' in value && typeof value.created_at === 'string'
  )
}

function isDocument(value: unknown): value is ScriptDocument {
  return isSummary(value) && 'content' in value && typeof value.content === 'string'
}

async function request(path: string, options: RequestInit): Promise<unknown> {
  const response = await fetch(`${apiBaseUrl}${path}`, options)
  const data: unknown = await response.json().catch(() => undefined)
  if (!response.ok) {
    const message = typeof data === 'object' && data !== null &&
      'detail' in data && typeof data.detail === 'string'
      ? data.detail
      : `请求失败（HTTP ${response.status}）`
    throw new Error(message)
  }
  return data
}

export async function listScripts(signal: AbortSignal): Promise<ScriptSummary[]> {
  const data = await request('/api/scripts', { signal, cache: 'no-store' })
  if (!Array.isArray(data) || !data.every(isSummary)) {
    throw new Error('讲稿列表响应格式不正确')
  }
  return data
}

export async function getScript(id: string, signal: AbortSignal): Promise<ScriptDocument> {
  const data = await request(`/api/scripts/${encodeURIComponent(id)}`, {
    signal,
    cache: 'no-store',
  })
  if (!isDocument(data)) throw new Error('讲稿内容响应格式不正确')
  return data
}

export async function uploadScripts(files: File[], signal: AbortSignal): Promise<ScriptDocument[]> {
  const body = new FormData()
  for (const file of files) body.append('files', file)
  const data = await request('/api/scripts/upload', { method: 'POST', body, signal })
  if (!Array.isArray(data) || !data.every(isDocument)) {
    throw new Error('上传结果响应格式不正确')
  }
  return data
}
