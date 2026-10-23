import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import ModelConfigForm from '../../src/components/models/ModelConfigForm.vue'
import ModelConfigList from '../../src/components/models/ModelConfigList.vue'
import { modelConfigDraft } from '../../src/components/models/modelConfigForm'
import { normalizeModelConfig } from '../../src/api/modelConfigs'
import { defaultLlmPresets } from '../../src/api/models'

function profile(overrides: Record<string, unknown> = {}) {
  return normalizeModelConfig({ id: 'profile-a', name: '本地与语义校准', revision: 3, is_active: true, active_revision: 2, pending_activation: true, settings: { stt_provider: 'local', llm_align_mode: 'fallback', llm_model: 'test-model', llm_base_url: 'https://example.test/v1', has_llm_api_key: true, llm_api_key: 'sk-****' }, ...overrides })
}

describe('model configuration editor controls', () => {
  it('preserves stored credentials until explicit replacement or clear and allows undo', async () => {
    const selected = profile()
    const draft = modelConfigDraft(selected)
    const wrapper = mount(ModelConfigForm, { props: { modelValue: draft, profile: selected, presets: defaultLlmPresets() } })
    const key = wrapper.get<HTMLInputElement>('#profile-llm-key')
    expect(key.element.value).toBe('')
    expect(key.attributes('placeholder')).toContain('留空保留')
    await key.setValue('new-secret')
    expect(draft.settings.llm_api_key).toBe('new-secret')
    await wrapper.findAll('button').find((button) => button.text() === '清除凭据')!.trigger('click')
    expect(draft.settings.clear_llm_api_key).toBe(true)
    expect(draft.settings.llm_api_key).toBe('')
    expect(key.element.disabled).toBe(true)
    await wrapper.findAll('button').find((button) => button.text() === '撤销清除')!.trigger('click')
    expect(draft.settings.clear_llm_api_key).toBe(false)
    expect(key.element.disabled).toBe(false)
    wrapper.unmount()
  })

  it('shows cloud microphone consent only for cloud STT, separate from LLM text processing', async () => {
    const draft = modelConfigDraft(profile())
    const wrapper = mount(ModelConfigForm, { props: { modelValue: draft, presets: defaultLlmPresets() } })
    expect(wrapper.find('input[type="checkbox"]').exists()).toBe(false)
    expect(wrapper.text()).toContain('讲稿窗口与识别文字会发送')
    await wrapper.get('#profile-stt-provider').setValue('cloud')
    expect(wrapper.get('label.check-field').text()).toContain('音频片段')
    await wrapper.get('input[type="checkbox"]').setValue(true)
    expect(draft.settings.cloud_opt_in).toBe(true)
    await wrapper.get('#profile-stt-endpoint').setValue('https://voice.example.test/stt')
    expect(draft.settings.cloud_stt_endpoint).toBe('https://voice.example.test/stt')
    wrapper.unmount()
  })

  it('fills a provider preset while keeping model editable and emits save only on submit', async () => {
    const draft = modelConfigDraft(profile())
    const wrapper = mount(ModelConfigForm, { props: { modelValue: draft, presets: defaultLlmPresets() } })
    await wrapper.get('#profile-llm-preset').setValue('deepseek')
    expect(draft.settings.llm_base_url).toBe('https://api.deepseek.com/v1')
    expect(wrapper.emitted('save')).toBeUndefined()
    await wrapper.get('#profile-llm-model').setValue('my-custom-model')
    expect(draft.settings.llm_model).toBe('my-custom-model')
    await wrapper.get('form').trigger('submit')
    expect(wrapper.emitted('save')).toHaveLength(1)
    wrapper.unmount()
  })

  it('filters discovered models and changes only the editable model ID until an explicit save', async () => {
    const draft = modelConfigDraft(profile())
    const wrapper = mount(ModelConfigForm, { props: { modelValue: draft, presets: defaultLlmPresets(), discoveryResult: { ok: true, models: [{ id: 'doubao-model', name: '豆包文本模型' }, { id: 'other-model' }], latency_ms: 8, message: '已获取 2 个模型', truncated: true } } })
    await wrapper.get('#profile-llm-model-search').setValue('豆包')
    expect(wrapper.findAll('#profile-discovered-model option').map((option) => option.attributes('value'))).toEqual(['', 'doubao-model'])
    await wrapper.get('#profile-discovered-model').setValue('doubao-model')
    expect(draft.settings.llm_model).toBe('doubao-model')
    expect(wrapper.get<HTMLInputElement>('#profile-llm-model').element.value).toBe('doubao-model')
    expect(wrapper.emitted('save')).toBeUndefined()
    expect(wrapper.text()).toContain('当前仅显示部分条目')
    await wrapper.get('#profile-llm-model-search').setValue('missing')
    expect(wrapper.text()).toContain('没有匹配的模型')
    await wrapper.get('#profile-llm-model').setValue('manual-id')
    expect(draft.settings.llm_model).toBe('manual-id')
    wrapper.unmount()
  })

  it('allows discovery with alignment disabled and an empty model, requiring only a valid Base URL', async () => {
    const draft = modelConfigDraft()
    const wrapper = mount(ModelConfigForm, { props: { modelValue: draft, presets: defaultLlmPresets() } })
    const button = () => wrapper.findAll('button').find((item) => item.text() === '获取模型列表')!
    expect(button().attributes('disabled')).toBeDefined()
    await wrapper.get('#profile-llm-url').setValue('https://example.test/v1')
    expect(button().attributes('disabled')).toBeUndefined()
    await button().trigger('click')
    expect(wrapper.emitted('discover')).toHaveLength(1)
    expect(draft.settings.llm_model).toBe('')
    expect(draft.settings.llm_align_mode).toBe('off')
    expect(wrapper.emitted('save')).toBeUndefined()
    wrapper.unmount()
  })

  it('protects the built-in local recovery profile from editing', () => {
    const selected = profile({ is_default: true })
    const wrapper = mount(ModelConfigForm, { props: { modelValue: modelConfigDraft(selected), profile: selected, presets: defaultLlmPresets() } })
    expect(wrapper.findAll('fieldset').every((fieldset) => fieldset.attributes('disabled') !== undefined)).toBe(true)
    wrapper.unmount()
  })
})

describe('configuration list selection', () => {
  it('distinguishes selection from activation and displays pending activation status', async () => {
    const wrapper = mount(ModelConfigList, { props: { profiles: [profile()], selectedId: 'profile-a' } })
    expect(wrapper.text()).toContain('当前生效')
    expect(wrapper.text()).toContain('修改待激活')
    expect(wrapper.get('.profile-item').attributes('aria-current')).toBe('true')
    await wrapper.get('.profile-item').trigger('click')
    expect(wrapper.emitted('select')).toEqual([['profile-a']])
    expect(wrapper.emitted('activate')).toBeUndefined()
    await wrapper.findAll('button').find((button) => button.text() === '新建配置')!.trigger('click')
    expect(wrapper.emitted('create')).toHaveLength(1)
    wrapper.unmount()
  })
})
