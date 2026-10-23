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

  it.each([
    { error_code: 'HTTP_ERROR' as const, http_status: 404, message: '模型服务返回 HTTP 404：请核对模型 ID 和访问权限。' },
    { error_code: 'TIMEOUT' as const, message: '模型请求超时，请适当增大请求超时，并检查网络连接。' },
  ])('shows a failed test and its safe diagnostic instead of an unfinished verification ($error_code)', async (diagnostic) => {
    const test = vi.spyOn(api, 'testModelConfig').mockResolvedValue({ ok: false, latency_ms: 207, target: 'llm', verified: 'none', ...diagnostic })
    const { wrapper, update, activate } = await setup()
    await wrapper.get('#profile-align-mode').setValue('hybrid')
    await wrapper.get('#profile-llm-url').setValue('https://ark.cn-beijing.volces.com/api/v3')
    await wrapper.get('#profile-llm-model').setValue('configured-model')
    await wrapper.findAll('button').find((button) => button.text() === '测试大模型连通性')!.trigger('click')
    await flushPromises()
    expect(test).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('连通性测试失败')
    expect(wrapper.text()).not.toContain('未完成连通验证')
    expect(wrapper.get('[role="alert"]').text()).toContain(diagnostic.message)
    expect(update).not.toHaveBeenCalled()
    expect(activate).not.toHaveBeenCalled()
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
