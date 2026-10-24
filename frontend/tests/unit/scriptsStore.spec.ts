import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import * as api from '../../src/api/scripts'
import { useScriptsStore } from '../../src/stores/scripts'
import { useSessionStore } from '../../src/stores/session'
import type { SessionSnapshot } from '../../src/api/session'

function document(id = 'a'): api.ScriptDocument {
  return { id, name: id + '.txt', content: '讲稿' + id, character_count: 3, created_at: '2026-10-25T00:00:00Z' }
}
function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(finish => { resolve = finish })
  return { promise, resolve }
}

beforeEach(() => setActivePinia(createPinia()))
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

it('deletes an encoded script ID and accepts an empty 204 without parsing JSON', async () => {
  const response = new Response(null, { status: 204 })
  const json = vi.spyOn(response, 'json')
  const fetch = vi.fn().mockResolvedValue(response)
  vi.stubGlobal('fetch', fetch)
  const signal = new AbortController().signal
  await api.deleteScript('稿/a b', signal)
  expect(fetch).toHaveBeenCalledWith('/api/scripts/' + encodeURIComponent('稿/a b'), { method: 'DELETE', signal })
  expect(json).not.toHaveBeenCalled()
})

it.each([
  [409, { detail: { code: 'SCRIPT_IN_USE', message: '另一个会话占用' } }, '讲稿正在会话中使用，请结束后再删除'],
  [404, { detail: '未找到讲稿' }, '讲稿不存在，可能已被其他页面删除，请刷新列表。'],
  [503, { detail: { message: '暂时无法删除讲稿' } }, '暂时无法删除讲稿'],
] as const)('keeps a useful delete error for HTTP %s', async (status, body, message) => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json(body, { status })))
  await expect(api.deleteScript('a', new AbortController().signal)).rejects.toThrow(message)
})

it('refreshes from the backend after deletion and clears the deleted preview and prepared selection', async () => {
  const a = document(), b = document('b')
  const store = useScriptsStore(), session = useSessionStore()
  store.scripts = [a, b]; store.selected = a; session.selectedScript = a
  const remove = vi.spyOn(api, 'deleteScript').mockResolvedValue(undefined)
  const list = vi.spyOn(api, 'listScripts').mockResolvedValue([b])
  expect(await store.remove('a')).toBe(true)
  expect(remove).toHaveBeenCalledWith('a', expect.any(AbortSignal))
  expect(list).toHaveBeenCalledTimes(1)
  expect(store.scripts).toEqual([b])
  expect(store.selected).toBeUndefined()
  expect(session.selectedScript).toBeUndefined()
  expect(store.deletingId).toBe('')
})

it('retains every entry and selection when deletion fails', async () => {
  const a = document()
  const store = useScriptsStore(), session = useSessionStore()
  store.scripts = [a]; store.selected = a; session.selectedScript = a
  vi.spyOn(api, 'deleteScript').mockRejectedValue(new Error('讲稿正在会话中使用，请结束后再删除'))
  const list = vi.spyOn(api, 'listScripts')
  expect(await store.remove('a')).toBe(false)
  expect(store.scripts).toEqual([a])
  expect(store.selected).toEqual(a)
  expect(session.selectedScript).toEqual(a)
  expect(list).not.toHaveBeenCalled()
  expect(store.error).toBe('讲稿正在会话中使用，请结束后再删除')
  expect(store.deletingId).toBe('')
})

it.each(['READY', 'LISTENING', 'PROMPTING', 'PAUSED'] as const)('protects the current bound script in %s', async state => {
  const a = document()
  const store = useScriptsStore(), session = useSessionStore()
  store.scripts = [a]; store.selected = a; session.selectedScript = a
  session.snapshot = { id: 'session', script_id: 'a', script_text: a.content, state } as SessionSnapshot
  const remove = vi.spyOn(api, 'deleteScript')
  expect(await store.remove('a')).toBe(false)
  expect(remove).not.toHaveBeenCalled()
  expect(store.selected).toEqual(a)
  expect(session.snapshot.script_text).toBe(a.content)
  expect(session.selectedScript).toEqual(a)
})

it('can remove another script without changing the current session document', async () => {
  const a = document(), b = document('b')
  const store = useScriptsStore(), session = useSessionStore()
  store.scripts = [a, b]; store.selected = a; session.selectedScript = a
  session.snapshot = { id: 'session', script_id: 'a', script_text: a.content, state: 'PAUSED' } as SessionSnapshot
  const snapshot = { ...session.snapshot }
  vi.spyOn(api, 'deleteScript').mockResolvedValue(undefined)
  vi.spyOn(api, 'listScripts').mockResolvedValue([a])
  expect(await store.remove('b')).toBe(true)
  expect(store.selected).toEqual(a)
  expect(session.selectedScript).toEqual(a)
  expect(session.snapshot).toEqual(snapshot)
})

it('deduplicates deletion while its request is pending', async () => {
  const pending = deferred<void>(), store = useScriptsStore()
  store.scripts = [document()]
  const remove = vi.spyOn(api, 'deleteScript').mockReturnValue(pending.promise)
  vi.spyOn(api, 'listScripts').mockResolvedValue([])
  const first = store.remove('a')
  expect(store.deletingId).toBe('a')
  expect(await store.remove('a')).toBe(false)
  expect(remove).toHaveBeenCalledTimes(1)
  pending.resolve()
  expect(await first).toBe(true)
})

it('ignores an old selected-document response after that script was deleted', async () => {
  const pending = deferred<api.ScriptDocument>(), store = useScriptsStore()
  store.scripts = [document()]
  vi.spyOn(api, 'getScript').mockReturnValue(pending.promise)
  vi.spyOn(api, 'deleteScript').mockResolvedValue(undefined)
  vi.spyOn(api, 'listScripts').mockResolvedValue([])
  const selection = store.select('a')
  expect(await store.remove('a')).toBe(true)
  pending.resolve(document())
  await selection
  expect(store.selected).toBeUndefined()
  expect(store.scripts).toEqual([])
})

it('ignores stale lists that complete after the post-delete refresh', async () => {
  const old = deferred<api.ScriptSummary[]>(), store = useScriptsStore()
  const list = vi.spyOn(api, 'listScripts').mockReturnValueOnce(old.promise).mockResolvedValueOnce([])
  vi.spyOn(api, 'deleteScript').mockResolvedValue(undefined)
  store.scripts = [document()]
  const refresh = store.refresh()
  expect(await store.remove('a')).toBe(true)
  old.resolve([document()])
  await refresh
  expect(list).toHaveBeenCalledTimes(2)
  expect(store.scripts).toEqual([])
})

it('reports a refresh failure truthfully after the server has deleted a selected script', async () => {
  const a = document(), store = useScriptsStore()
  store.scripts = [a]; store.selected = a
  vi.spyOn(api, 'deleteScript').mockResolvedValue(undefined)
  vi.spyOn(api, 'listScripts').mockRejectedValue(new Error('offline'))
  expect(await store.remove('a')).toBe(true)
  expect(store.selected).toBeUndefined()
  expect(store.scripts).toEqual([a])
  expect(store.error).toContain('讲稿已删除，但列表刷新失败')
})
