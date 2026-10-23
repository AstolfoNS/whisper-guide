import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import ModelConfigsView from '../../src/views/ModelConfigsView.vue'
import { useSessionStore } from '../../src/stores/session'
import type { SessionSnapshot } from '../../src/api/session'
import * as api from '../../src/api/modelConfigs'

let mounted: VueWrapper | undefined
function sample() {
  return api.normalizeModelConfig({ id: 'profile-a', name: '演讲配置', revision: 3, is_active: true, active_revision: 2, pending_activation: true, settings: { stt_provider: 'local', llm_align_mode: 'off' } })
}

async function setup(activeSession = false) {
  const selected = sample()
  vi.spyOn(api, 'listModelConfigs').mockResolvedValue({ configs: [selected], active_id: selected.id, active_revision: 2, presets: [] })
  vi.spyOn(api, 'fetchModelConfig').mockResolvedValue(selected)
  const saved = { ...selected, name: '修改后配置', revision: 4 }
  const update = vi.spyOn(api, 'updateModelConfig').mockResolvedValue(saved)
  const activate = vi.spyOn(api, 'activateModelConfig').mockResolvedValue({ ...saved, pending_activation: false, active_revision: 4 })
  const pinia = createPinia()
  const session = useSessionStore(pinia)
  if (activeSession) session.snapshot = { state: 'PAUSED' } as SessionSnapshot
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', component: { template: '<h1>概览</h1>' } }, { path: '/settings/models', component: ModelConfigsView }] })
  await router.push('/settings/models')
  await router.isReady()
  mounted = mount({ template: '<router-view />' }, { attachTo: document.body, global: { plugins: [pinia, router] } })
  await flushPromises()
  return { wrapper: mounted, router, update, activate }
}

afterEach(() => { mounted?.unmount(); mounted = undefined; vi.restoreAllMocks(); document.body.innerHTML = '' })

describe('model profile routed editor', () => {
  it('saves a draft with its revision without activating it and requires explicit activation', async () => {
    const { wrapper, update, activate } = await setup()
    await wrapper.get('#config-name').setValue('修改后配置')
    await wrapper.get('#model-profile-form').trigger('submit')
    await flushPromises()
    expect(update).toHaveBeenCalledWith('profile-a', expect.objectContaining({ name: '修改后配置', revision: 3 }))
    expect(activate).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('重新激活')
    const activateButton = wrapper.findAll('button').find((button) => button.text() === '设为生效配置')!
    await activateButton.trigger('click')
    await flushPromises()
    expect(activate).toHaveBeenCalledWith('profile-a', 4)
  })

  it('blocks activation while a paused session still exists', async () => {
    const { wrapper, activate } = await setup(true)
    expect(wrapper.text()).toContain('结束会话后才能激活配置')
    const button = wrapper.findAll('button').find((item) => item.text() === '设为生效配置')!
    expect(button.attributes('disabled')).not.toBeUndefined()
    await button.trigger('click')
    expect(activate).not.toHaveBeenCalled()
  })

  it('keeps unsaved edits on cancelled navigation and leaves only after explicit discard', async () => {
    const { wrapper, router } = await setup()
    await wrapper.get('#config-name').setValue('未保存草稿')
    const firstNavigation = router.push('/')
    await flushPromises()
    expect(document.body.textContent).toContain('存在未保存的修改')
    const cancel = [...document.body.querySelectorAll('button')].find((button) => button.textContent?.trim() === '继续编辑')!
    cancel.click()
    await firstNavigation
    expect(router.currentRoute.value.path).toBe('/settings/models')
    expect(wrapper.get<HTMLInputElement>('#config-name').element.value).toBe('未保存草稿')
    const secondNavigation = router.push('/')
    await flushPromises()
    const discard = [...document.body.querySelectorAll('button')].find((button) => button.textContent?.trim() === '放弃修改')!
    discard.click()
    await secondNavigation
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/')
    expect(wrapper.text()).toBe('概览')
  })
})
