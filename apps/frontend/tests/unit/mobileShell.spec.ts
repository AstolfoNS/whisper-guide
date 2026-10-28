import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent } from 'vue'
import AppLayout from '../../src/layouts/AppLayout.vue'
import ModelConfigsView from '../../src/views/ModelConfigsView.vue'
import RecoverableSessions from '../../src/components/health/RecoverableSessions.vue'
import { useMediaQuery } from '../../src/composables/useMediaQuery'
import { installPageLifecycle } from '../../src/services/pageLifecycle'
import { useSessionStore } from '../../src/stores/session'
import * as api from '../../src/api/modelConfigs'

let wrappers: VueWrapper[] = []
const changes = new Set<() => void>()
let narrow = true
beforeEach(() => {
  narrow = true
  vi.stubGlobal('matchMedia', vi.fn((query: string) => ({
    get matches() { return query.includes('700px') && narrow }, media: query,
    addEventListener: (_: string, listener: () => void) => changes.add(listener),
    removeEventListener: (_: string, listener: () => void) => changes.delete(listener),
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
  })))
})
afterEach(() => { wrappers.forEach(wrapper => wrapper.unmount()); wrappers = []; changes.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); document.body.innerHTML = '' })

it('reacts to viewport changes and removes the media listener on unmount', async () => {
  const component = defineComponent({ setup: () => ({ narrow: useMediaQuery('(max-width: 700px)') }), template: '<p>{{ narrow }}</p>' })
  const wrapper = mount(component); wrappers.push(wrapper)
  expect(wrapper.text()).toBe('true')
  narrow = false; changes.forEach(listener => listener())
  await wrapper.vm.$nextTick()
  expect(wrapper.text()).toBe('false')
  wrapper.unmount(); wrappers = []
  expect(changes.size).toBe(0)
})

it('uses a navigation menu on small screens and reveals the same destinations', async () => {
  const router = createRouter({ history: createMemoryHistory(), routes: ['/', '/studio', '/settings/models'].map(path => ({ path, component: { template: '<p>页面</p>' } })) })
  await router.push('/'); await router.isReady()
  const wrapper = mount(AppLayout, { attachTo: document.body, global: { plugins: [createPinia(), router], stubs: { ServiceHealthBar: true, RecoverableSessions: true } } })
  wrappers.push(wrapper)
  expect(wrapper.find('nav[aria-label="主导航"]').exists()).toBe(false)
  await wrapper.get('button[aria-label="打开导航菜单"]').trigger('click')
  await flushPromises()
  const navigation = document.querySelector('nav[aria-label="手机主导航"]')
  expect(navigation?.textContent).toContain('模型配置')
  expect(navigation?.querySelector('a[href="/studio"]')).toBeTruthy()
})

it('collapses recoverable sessions on a phone without ending them', async () => {
  const pinia = createPinia()
  const session = useSessionStore(pinia)
  session.recoverableSessions = [{ session_id: 'old', script_name: '保留的讲稿', state: 'PAUSED', config_name: 'local' }]
  const wrapper = mount(RecoverableSessions, { global: { plugins: [pinia] } }); wrappers.push(wrapper)
  expect(wrapper.find('.recovery-list').exists()).toBe(false)
  await wrapper.get('.recovery-toggle').trigger('click')
  expect(wrapper.find('.recovery-list').exists()).toBe(true)
  expect(session.recoverableSessions).toHaveLength(1)
})

it('shows the model list first on a phone and preserves a draft when returning to it', async () => {
  const profile = api.normalizeModelConfig({ id: 'mobile-profile', name: '手机演讲配置', revision: 1, is_active: true, settings: { stt_provider: 'local', llm_align_mode: 'off' } })
  vi.spyOn(api, 'listModelConfigs').mockResolvedValue({ configs: [profile], active_id: profile.id, active_revision: 1, presets: [] })
  vi.spyOn(api, 'fetchModelConfig').mockResolvedValue(profile)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: ModelConfigsView }] })
  await router.push('/'); await router.isReady()
  const wrapper = mount({ template: '<router-view />' }, { attachTo: document.body, global: { plugins: [createPinia(), router] } }); wrappers.push(wrapper)
  await flushPromises()
  expect(wrapper.get('.profile-list').isVisible()).toBe(true)
  expect(wrapper.get('.profile-editor').isVisible()).toBe(false)
  await wrapper.get('.profile-item').trigger('click')
  expect(wrapper.get('.profile-editor').isVisible()).toBe(true)
  expect(wrapper.get('.profile-list').isVisible()).toBe(false)
  await wrapper.get('#config-name').setValue('尚未保存的修改')
  await wrapper.get('.profile-back').trigger('click')
  expect(wrapper.get('.profile-list').isVisible()).toBe(true)
  await wrapper.get('.profile-item').trigger('click')
  expect(wrapper.get<HTMLInputElement>('#config-name').element.value).toBe('尚未保存的修改')
})

it('suspends on pagehide or hidden visibility and never resumes when becoming visible', () => {
  const suspend = vi.fn()
  const remove = installPageLifecycle(suspend)
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  document.dispatchEvent(new Event('visibilitychange'))
  expect(suspend).toHaveBeenCalledTimes(1)
  vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('visible')
  document.dispatchEvent(new Event('visibilitychange'))
  expect(suspend).toHaveBeenCalledTimes(1)
  window.dispatchEvent(new Event('pagehide'))
  expect(suspend).toHaveBeenCalledTimes(2)
  remove()
  window.dispatchEvent(new Event('pagehide'))
  expect(suspend).toHaveBeenCalledTimes(2)
})
