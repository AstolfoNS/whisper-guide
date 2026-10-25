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
  const data: unknown = response.status === 204
    ? undefined
    : await response.json().catch(() => undefined)
  if (!response.ok) {
    const detail = typeof data === 'object' && data !== null && 'detail' in data
      ? data.detail
      : undefined
    const message = typeof detail === 'string' ? detail
      : typeof detail === 'object' && detail !== null &&
        'message' in detail && typeof detail.message === 'string' ? detail.message
      : '请求失败（HTTP ' + response.status + '）'
    if (options.method === 'DELETE' && response.status === 409) {
      throw new Error('讲稿正在会话中使用，请结束后再删除')
    }
    if (options.method === 'DELETE' && response.status === 404) {
      throw new Error('讲稿不存在，可能已被其他页面删除，请刷新列表。')
    }
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


export async function deleteScript(id: string, signal: AbortSignal): Promise<void> {
  await request('/api/scripts/' + encodeURIComponent(id), { method: 'DELETE', signal })
}
