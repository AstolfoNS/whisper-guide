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
  return api.normalizeModelConfig({ id: 'profile-a', name: '演讲配置', revision: 3, is_active: true, active_revision: 2, pending_activation: true, settings: { stt_provider: 'local', llm_align_mode: 'off', llm_base_url: 'https://models.example.test/v1', has_llm_api_key: true } })
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

  it('discovers with a stored key and no model, selecting changes only the unsaved draft', async () => {
    const discovery = vi.spyOn(api, 'discoverModels').mockResolvedValue({ ok: true, models: [{ id: 'model-from-server' }], latency_ms: 15, message: '已获取模型列表', truncated: false })
    const { wrapper, update, activate } = await setup()
    await wrapper.findAll('button').find((button) => button.text() === '获取模型列表')!.trigger('click')
    await flushPromises()
    expect(discovery).toHaveBeenCalledWith(expect.objectContaining({ settings: expect.objectContaining({ llm_align_mode: 'off', llm_model: '', llm_api_key: '' }) }), 'profile-a', expect.any(AbortSignal))
    await wrapper.get('#profile-discovered-model').setValue('model-from-server')
    expect(wrapper.get<HTMLInputElement>('#profile-llm-model').element.value).toBe('model-from-server')
    expect(wrapper.text()).toContain('未保存')
    expect(update).not.toHaveBeenCalled()
    expect(activate).not.toHaveBeenCalled()
  })

  it('aborts requests when the destination changes and ignores old replies after a newer result', async () => {
    let oldResolve!: (value: api.ModelDiscoveryResult) => void
    let newResolve!: (value: api.ModelDiscoveryResult) => void
    const discovery = vi.spyOn(api, 'discoverModels')
      .mockImplementationOnce(() => new Promise((resolve) => { oldResolve = resolve }))
      .mockImplementationOnce(() => new Promise((resolve) => { newResolve = resolve }))
    const { wrapper } = await setup()
    const button = () => wrapper.findAll('button').find((item) => item.text() === '获取模型列表')!
    await button().trigger('click')
    const oldSignal = discovery.mock.calls[0]![2]!
    expect(wrapper.get<HTMLInputElement>('#profile-llm-url').element.disabled).toBe(false)
    await wrapper.get('#profile-llm-url').setValue('https://new.example.test/v1')
    expect(oldSignal.aborted).toBe(true)
    await button().trigger('click')
    newResolve({ ok: true, models: [{ id: 'new-model' }], latency_ms: 1, message: '新地址列表', truncated: false })
    await flushPromises()
    oldResolve({ ok: true, models: [{ id: 'stale-model' }], latency_ms: 100, message: '旧地址列表', truncated: false })
    await flushPromises()
    expect(wrapper.text()).toContain('new-model')
    expect(wrapper.text()).not.toContain('stale-model')
    await wrapper.get('#profile-llm-key').setValue('replacement-key')
    expect(wrapper.find('#profile-discovered-model').exists()).toBe(false)
  })

  it.each(['key', 'clear', 'preset', 'profile'] as const)('cancels an in-flight discovery when its %s changes', async (change) => {
    let resolve!: (value: api.ModelDiscoveryResult) => void
    const discovery = vi.spyOn(api, 'discoverModels').mockImplementation(() => new Promise((finish) => { resolve = finish }))
    const { wrapper } = await setup()
    await wrapper.findAll('button').find((button) => button.text() === '获取模型列表')!.trigger('click')
    const signal = discovery.mock.calls[0]![2]!
    if (change === 'key') await wrapper.get('#profile-llm-key').setValue('new-key')
    if (change === 'clear') await wrapper.findAll('button').find((button) => button.text() === '清除凭据')!.trigger('click')
    if (change === 'preset') await wrapper.get('#profile-llm-preset').setValue('deepseek')
    if (change === 'profile') await wrapper.findAll('button').find((button) => button.text() === '新建配置')!.trigger('click')
    expect(signal.aborted).toBe(true)
    resolve({ ok: true, models: [{ id: 'outdated-model' }], latency_ms: 20, message: '过期响应', truncated: false })
    await flushPromises()
    expect(wrapper.text()).not.toContain('outdated-model')
    expect(wrapper.text()).not.toContain('过期响应')
  })

  it.each([
    { ok: true, models: [], message: '接口未返回模型', latency_ms: 2, truncated: false },
    { ok: false, models: [], message: '模型列表接口不支持，HTTP 404', latency_ms: 2, truncated: false, error_code: 'HTTP_ERROR' as const, http_status: 404 },
  ])('keeps manual input available for empty or unsupported model lists ($ok)', async (result) => {
    vi.spyOn(api, 'discoverModels').mockResolvedValue(result)
    const { wrapper } = await setup()
    await wrapper.findAll('button').find((button) => button.text() === '获取模型列表')!.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain(result.message)
    expect(wrapper.find('#profile-discovered-model').exists()).toBe(false)
    await wrapper.get('#profile-llm-model').setValue('manual-model')
    expect(wrapper.get<HTMLInputElement>('#profile-llm-model').element.value).toBe('manual-model')
  })

  it('cancels a pending discovery on route exit without blocking navigation', async () => {
    const discovery = vi.spyOn(api, 'discoverModels').mockImplementation(() => new Promise(() => {}))
    const { wrapper, router } = await setup()
    await wrapper.findAll('button').find((button) => button.text() === '获取模型列表')!.trigger('click')
    const signal = discovery.mock.calls[0]![2]!
    await router.push('/')
    expect(signal.aborted).toBe(true)
    expect(router.currentRoute.value.path).toBe('/')
  })

  it('can discover from a new unnamed draft without saving or activating a profile', async () => {
    const discovery = vi.spyOn(api, 'discoverModels').mockResolvedValue({ ok: true, models: [], latency_ms: 0, message: '空列表', truncated: false })
    const create = vi.spyOn(api, 'createModelConfig')
    const { wrapper, update, activate } = await setup()
    await wrapper.findAll('button').find((button) => button.text() === '新建配置')!.trigger('click')
    await wrapper.get('#profile-llm-url').setValue('https://new.example.test/v1')
    await wrapper.get('#profile-llm-key').setValue('draft-only-key')
    await wrapper.findAll('button').find((button) => button.text() === '获取模型列表')!.trigger('click')
    await flushPromises()
    expect(discovery).toHaveBeenCalledWith(expect.objectContaining({ name: '', settings: expect.objectContaining({ llm_api_key: 'draft-only-key', llm_model: '' }) }), undefined, expect.any(AbortSignal))
    expect(create).not.toHaveBeenCalled()
    expect(update).not.toHaveBeenCalled()
    expect(activate).not.toHaveBeenCalled()
  })

  it('tests an unsaved native ASR draft with its own voice credential and no App ID', async () => {
    const test = vi.spyOn(api, 'testModelConfig').mockResolvedValue({ ok: true, target: 'stt', verified: 'network', message: '语音握手通过', latency_ms: 20 })
    const create = vi.spyOn(api, 'createModelConfig')
    const { wrapper, update, activate } = await setup()
    await wrapper.findAll('button').find((button) => button.text() === '新建配置')!.trigger('click')
    await wrapper.get('#profile-stt-provider').setValue('cloud')
    await wrapper.get('#profile-stt-protocol').setValue('volcengine-ws')
    await wrapper.get('#profile-stt-token').setValue('voice-only-draft-key')
    await wrapper.get('label.check-field:last-of-type input').setValue(true)
    await wrapper.get('#profile-stt-timeout').setValue('6500')
    await wrapper.findAll('button').find((button) => button.text() === '测试云端 STT')!.trigger('click')
    await flushPromises()
    expect(test).toHaveBeenCalledWith(expect.objectContaining({ name: '', settings: expect.objectContaining({ cloud_stt_protocol: 'volcengine-ws', cloud_stt_auth_mode: 'api-key', cloud_stt_app_id: '', cloud_stt_access_token: 'voice-only-draft-key', cloud_stt_timeout_ms: 6500, llm_timeout_ms: 1500 }) }), 'stt', undefined)
    expect(wrapper.text()).toContain('语音握手通过')
    expect(create).not.toHaveBeenCalled()
    expect(update).not.toHaveBeenCalled()
    expect(activate).not.toHaveBeenCalled()
    await wrapper.get('#profile-stt-auth').setValue('app-token')
    await wrapper.findAll('button').find((button) => button.text() === '测试云端 STT')!.trigger('click')
    await flushPromises()
    expect(test).toHaveBeenCalledTimes(1)
    expect(wrapper.text()).toContain('需要填写 App ID')
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
