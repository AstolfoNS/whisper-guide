import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'

vi.mock('../../src/services/healthMonitor', () => ({ startHealthMonitor: vi.fn(), stopHealthMonitor: vi.fn() }))
vi.mock('../../src/services/sessionController', () => ({ getSessionController: vi.fn(), disposeSessionController: vi.fn() }))
let wrapper: VueWrapper | undefined
beforeEach(() => { vi.resetModules(); document.title = 'whisper-guide' })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.unstubAllGlobals(); vi.restoreAllMocks() })

it('loads the public title once with a bounded timeout and fresh runtime configuration', async () => {
  const fetch = vi.fn(async () => Response.json({ app_title: '  讲稿助手  ' }))
  vi.stubGlobal('fetch', fetch)
  const timeout = vi.spyOn(AbortSignal, 'timeout')
  const { appTitle, loadAppConfig } = await import('../../src/services/appConfig')
  expect(appTitle.value).toBe('whisper-guide')
  const first = loadAppConfig()
  expect(loadAppConfig()).toBe(first)
  await first
  await loadAppConfig()
  expect(appTitle.value).toBe('讲稿助手')
  expect(fetch).toHaveBeenCalledTimes(1)
  expect(fetch.mock.calls[0]![0]).toBe('/api/app-config')
  expect(fetch.mock.calls[0]![1]).toMatchObject({ cache: 'no-store', signal: expect.any(AbortSignal) })
  expect(timeout).toHaveBeenCalledWith(5000)
})

it.each([
  ['blank', () => Response.json({ app_title: ' \t\n ' })],
  ['malformed', () => Response.json({ app_title: 123 })],
  ['old server', () => new Response(null, { status: 404 })],
  ['network failure', () => Promise.reject(new TypeError('offline'))],
  ['timeout', () => Promise.reject(new DOMException('timed out', 'TimeoutError'))],
] as const)('keeps the default title for %s without rejecting startup', async (_name, response) => {
  vi.stubGlobal('fetch', vi.fn(response))
  const { appTitle, loadAppConfig } = await import('../../src/services/appConfig')
  await expect(loadAppConfig()).resolves.toBeUndefined()
  expect(appTitle.value).toBe('whisper-guide')
})

it('updates the brand, accessible name and routed browser title when runtime configuration arrives', async () => {
  let finish!: (response: Response) => void
  const fetch = vi.fn(() => new Promise<Response>(resolve => { finish = resolve }))
  vi.stubGlobal('fetch', fetch)
  const App = (await import('../../src/App.vue')).default
  const router = createRouter({ history: createMemoryHistory(), routes: [
    { path: '/', component: { template: '<p>概览页面</p>' }, meta: { title: '运行概览' } },
    { path: '/studio', component: { template: '<p>工作台页面</p>' }, meta: { title: '演讲工作台' } },
    { path: '/settings/models', component: { template: '<p>模型页面</p>' }, meta: { title: '模型配置' } },
  ] })
  await router.push('/studio')
  await router.isReady()
  wrapper = mount(App, { global: { plugins: [createPinia(), router], stubs: { ServiceHealthBar: true, RecoverableSessions: true } } })
  expect(wrapper.get('.app-brand').text()).toBe('whisper-guide')
  expect(wrapper.get('.app-brand').attributes('aria-label')).toBe('whisper-guide 概览')
  expect(document.title).toBe('演讲工作台 · whisper-guide')

  const customTitle = '<img src=x onerror="window.injected=true"> 研发讲稿'
  finish(Response.json({ app_title: customTitle }))
  await flushPromises()
  expect(wrapper.get('.app-brand').text()).toBe(customTitle)
  expect(wrapper.get('.app-brand').attributes('aria-label')).toBe(`${customTitle} 概览`)
  expect(wrapper.get('.app-brand').find('img').exists()).toBe(false)
  expect(document.title).toBe(`演讲工作台 · ${customTitle}`)
  await router.push('/settings/models')
  await flushPromises()
  expect(document.title).toBe(`模型配置 · ${customTitle}`)
  expect(fetch).toHaveBeenCalledTimes(1)
})
