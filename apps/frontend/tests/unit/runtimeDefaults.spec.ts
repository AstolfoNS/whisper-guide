import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import type { SessionSnapshot } from '../../src/api/session'

beforeEach(() => { vi.resetModules(); setActivePinia(createPinia()) })
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('loads 1500ms and configurable upload limits before validating or creating a session', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ app_title: '配置验证', default_silence_timeout_ms: 1500,
    script_max_file_bytes: 4194304, script_max_upload_files: 15 })))
  const { useSessionStore } = await import('../../src/stores/session')
  const { runtimeDefaults } = await import('../../src/services/appConfig')
  const session = useSessionStore()
  expect(session.silenceTimeout).toBe(3000)
  await session.initializeDefaults()
  expect(session.silenceTimeout).toBe(1500)
  expect(runtimeDefaults.value.scriptMaxFileBytes).toBe(4194304)
  expect(runtimeDefaults.value.scriptMaxUploadFiles).toBe(15)
})

it('does not replace a user edit with a late default, including an explicit edit to 3000ms', async () => {
  let respond!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { respond = resolve })))
  const { useSessionStore } = await import('../../src/stores/session')
  const session = useSessionStore()
  const loading = session.initializeDefaults()
  session.silenceTimeout = 3000
  session.markSilenceTimeoutEdited()
  respond(Response.json({ default_silence_timeout_ms: 1500 }))
  await loading
  expect(session.silenceTimeout).toBe(3000)
})

it('does not overwrite restored or active session options with the environment default', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ default_silence_timeout_ms: 1500 })))
  const { useSessionStore } = await import('../../src/stores/session')
  const session = useSessionStore()
  session.snapshot = { id: 'restored', state: 'PAUSED', silence_timeout_ms: 4200 } as SessionSnapshot
  session.silenceTimeout = 4200
  await session.initializeDefaults()
  expect(session.silenceTimeout).toBe(4200)
})

it.each([999, 1.5, '1500', null, -1])('retains valid compatibility defaults for malformed timeout %s', async value => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ default_silence_timeout_ms: value, script_max_file_bytes: 0, script_max_upload_files: -1 })))
  const { useSessionStore } = await import('../../src/stores/session')
  const { runtimeDefaults } = await import('../../src/services/appConfig')
  const session = useSessionStore()
  await session.initializeDefaults()
  expect(session.silenceTimeout).toBe(3000)
  expect(runtimeDefaults.value.scriptMaxFileBytes).toBe(2097152)
  expect(runtimeDefaults.value.scriptMaxUploadFiles).toBe(10)
})

it('allows a larger upload and batch configured by the server instead of the old 2MiB/10 limits', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ script_max_file_bytes: 4194304, script_max_upload_files: 15 })))
  const api = await import('../../src/api/scripts')
  const upload = vi.spyOn(api, 'uploadScripts').mockResolvedValue([])
  vi.spyOn(api, 'listScripts').mockResolvedValue([])
  const { useScriptsStore } = await import('../../src/stores/scripts')
  const file = new File([new Uint8Array(3145728)], 'large.txt', { type: 'text/plain' })
  await useScriptsStore().upload(Array.from({ length: 11 }, () => file))
  expect(upload).toHaveBeenCalledTimes(1)
})

it('uses configured smaller upload limits in validation and messages', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ script_max_file_bytes: 1024, script_max_upload_files: 2 })))
  const api = await import('../../src/api/scripts')
  const upload = vi.spyOn(api, 'uploadScripts')
  const { useScriptsStore } = await import('../../src/stores/scripts')
  const file = new File(['text'], 'script.txt')
  await expect(useScriptsStore().upload([file, file, file])).rejects.toThrow('1–2')
  await expect(useScriptsStore().upload([new File([new Uint8Array(2048)], 'large.txt')])).rejects.toThrow('1 KiB')
  expect(upload).not.toHaveBeenCalled()
})


it('omits a UI default from a new session so the server remains authoritative even if public config failed', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline') }))
  const { useSessionStore } = await import('../../src/stores/session')
  const session = useSessionStore()
  await session.initializeDefaults()
  expect(session.newSessionTimeout()).toBeUndefined()
  session.silenceTimeout = 1700
  session.markSilenceTimeoutEdited()
  expect(session.newSessionTimeout()).toBe(1700)
})
