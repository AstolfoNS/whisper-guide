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

it('loads all progress defaults without sending unchanged UI choices over server defaults', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ default_prompt_mode: 'progress',
    default_progress_timeout_ms: 6500, default_continuous_prompt: true })))
  const { useSessionStore } = await import('../../src/stores/session')
  const session = useSessionStore()
  await session.initializeDefaults()
  expect(session.promptMode).toBe('progress')
  expect(session.progressTimeout).toBe(6500)
  expect(session.continuousPrompt).toBe(true)
  expect(session.newSessionOptions()).toEqual({})
})

it('preserves independent edits while late progress defaults update untouched options', async () => {
  let respond!: (response: Response) => void
  vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { respond = resolve })))
  const { useSessionStore } = await import('../../src/stores/session')
  const session = useSessionStore()
  const loading = session.initializeDefaults()
  session.promptMode = 'silence'
  session.markPromptOptionEdited('mode')
  session.continuousPrompt = false
  session.markPromptOptionEdited('continuous')
  respond(Response.json({ default_prompt_mode: 'progress', default_progress_timeout_ms: 6000, default_continuous_prompt: true }))
  await loading
  expect(session.promptMode).toBe('silence')
  expect(session.progressTimeout).toBe(6000)
  expect(session.continuousPrompt).toBe(false)
  expect(session.newSessionOptions()).toEqual({ prompt_mode: 'silence', continuous_prompt: false })
})

it('restores progress options and does not replace them with startup configuration', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ default_prompt_mode: 'silence', default_progress_timeout_ms: 4000 })))
  const { useSessionStore } = await import('../../src/stores/session')
  const session = useSessionStore()
  const restored = { id: 'restore', state: 'PAUSED', silence_timeout_ms: 2400,
    prompt_mode: 'progress', progress_timeout_ms: 8500, continuous_prompt: true } as SessionSnapshot
  session.snapshot = restored
  session.restoreOptions(restored)
  await session.initializeDefaults()
  expect(session.currentSessionOptions()).toEqual({ prompt_mode: 'progress', progress_timeout_ms: 8500, continuous_prompt: true })
  expect(session.silenceTimeout).toBe(2400)
})

it.each([1999, 10001, 2.5, '4000', null])('rejects malformed progress default %s and non-boolean continuous flag', async value => {
  vi.stubGlobal('fetch', vi.fn(async () => Response.json({ default_prompt_mode: 'unknown',
    default_progress_timeout_ms: value, default_continuous_prompt: 'true' })))
  const { useSessionStore } = await import('../../src/stores/session')
  const session = useSessionStore()
  await session.initializeDefaults()
  expect(session.currentSessionOptions()).toEqual({ prompt_mode: 'silence', progress_timeout_ms: 4000, continuous_prompt: false })
  expect(session.newSessionOptions()).toEqual({})
})

it('preserves paused option drafts through a delayed pause acknowledgement and accepts their confirmation', async () => {
  const { useSessionStore } = await import('../../src/stores/session')
  const session = useSessionStore()
  const previous = { id: 'paused-draft', state: 'LISTENING', silence_timeout_ms: 3000,
    prompt_mode: 'silence', progress_timeout_ms: 4000, continuous_prompt: false } as SessionSnapshot
  session.snapshot = previous
  session.restoreOptions(previous)
  session.localPaused = true
  session.silenceTimeout = 1800; session.markSilenceTimeoutEdited()
  session.promptMode = 'progress'; session.markPromptOptionEdited('mode')
  session.progressTimeout = 6500; session.markPromptOptionEdited('timeout')
  session.continuousPrompt = true; session.markPromptOptionEdited('continuous')
  const delayed = { ...previous, state: 'PAUSED' } as SessionSnapshot
  session.snapshot = delayed
  session.restoreOptions(delayed)
  expect(session.silenceTimeout).toBe(1800)
  expect(session.currentSessionOptions()).toEqual({ prompt_mode: 'progress', progress_timeout_ms: 6500, continuous_prompt: true })
  const acknowledged = { ...delayed, silence_timeout_ms: 1800,
    prompt_mode: 'progress', progress_timeout_ms: 6500, continuous_prompt: true } as SessionSnapshot
  session.restoreOptions(acknowledged)
  // Once acknowledged, a later authoritative update must no longer be treated as stale.
  session.restoreOptions({ ...acknowledged, silence_timeout_ms: 2100, progress_timeout_ms: 7000 })
  expect(session.silenceTimeout).toBe(2100)
  expect(session.progressTimeout).toBe(7000)
})

it('keeps only unacknowledged paused fields when a partial option acknowledgement arrives', async () => {
  const { useSessionStore } = await import('../../src/stores/session')
  const session = useSessionStore()
  const paused = { id: 'partial-draft', state: 'PAUSED', silence_timeout_ms: 3000,
    prompt_mode: 'silence', progress_timeout_ms: 4000, continuous_prompt: false } as SessionSnapshot
  session.snapshot = paused
  session.restoreOptions(paused)
  session.promptMode = 'progress'; session.markPromptOptionEdited('mode')
  session.progressTimeout = 6500; session.markPromptOptionEdited('timeout')
  session.restoreOptions({ ...paused, prompt_mode: 'progress' })
  session.restoreOptions({ ...paused, prompt_mode: 'silence' })
  expect(session.promptMode).toBe('silence')
  expect(session.progressTimeout).toBe(6500)
})

it('does not carry a paused draft into another session or override the started server options', async () => {
  const { useSessionStore } = await import('../../src/stores/session')
  const session = useSessionStore()
  const paused = { id: 'old-session', state: 'PAUSED', silence_timeout_ms: 3000,
    prompt_mode: 'silence', progress_timeout_ms: 4000, continuous_prompt: false } as SessionSnapshot
  session.snapshot = paused
  session.restoreOptions(paused)
  session.promptMode = 'progress'; session.markPromptOptionEdited('mode')
  session.restoreOptions({ ...paused, id: 'another-session', progress_timeout_ms: 8000 })
  expect(session.currentSessionOptions()).toEqual({ prompt_mode: 'silence', progress_timeout_ms: 8000, continuous_prompt: false })
  session.snapshot = paused
  session.localPaused = true
  session.promptMode = 'progress'; session.markPromptOptionEdited('mode')
  session.restoreOptions({ ...paused, state: 'LISTENING', progress_timeout_ms: 5000 })
  expect(session.currentSessionOptions()).toEqual({ prompt_mode: 'silence', progress_timeout_ms: 5000, continuous_prompt: false })
})
