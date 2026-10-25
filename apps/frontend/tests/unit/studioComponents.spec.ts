import { beforeEach, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import TeleprompterView from '../../src/components/studio/TeleprompterView.vue'
import StateMachineMonitor from '../../src/components/studio/StateMachineMonitor.vue'
import SessionControls from '../../src/components/studio/SessionControls.vue'
import ServiceHealthBar from '../../src/components/health/ServiceHealthBar.vue'
import { useHealthStore } from '../../src/stores/health'
import { useSessionStore } from '../../src/stores/session'
import type { SessionSnapshot } from '../../src/api/session'

beforeEach(() => setActivePinia(createPinia()))
it('keeps original script text and whole-word cursor offsets', async () => {
  const snapshot = { script_id: 's', reading_position: 3, prompt_position: 6,
    units: [{ text: 'AI', start: 3, end: 5 }, { text: '2026', start: 6, end: 10 }],
  } as SessionSnapshot
  const wrapper = mount(TeleprompterView, { props: { text: '欢迎 AI 2026。', snapshot } })
  expect(wrapper.get('.reading-cursor').text()).toBe('AI')
  expect(wrapper.get('.prompt-cursor').text()).toBe('2026')
  expect(wrapper.get('.prompter-body').text()).toBe('欢迎 AI 2026。')
  await wrapper.setProps({ snapshot: { ...snapshot, raw_transcript: '改变部分假设' } })
  expect(wrapper.get('.reading-cursor').text()).toBe('AI')
  wrapper.unmount()
})
it('shows all five states, actual last edge and legal transitions', () => {
  const wrapper = mount(StateMachineMonitor, { props: { session: {
    state: 'PROMPTING', previous_state: 'LISTENING', transition_trigger: 'silence_threshold_reached',
    transition_at: '2026-10-24T08:00:00Z', allowed_transitions: ['LISTENING','PAUSED','TERMINATED'],
    transition_history: [{ from_state:'LISTENING',to_state:'PROMPTING',trigger:'silence_threshold_reached',timestamp:'2026-10-24T08:00:00Z' }],
  } as SessionSnapshot } })
  expect(wrapper.findAll('.state-monitor__node')).toHaveLength(5)
  expect(wrapper.findAll('.state-monitor__edge--active')).toHaveLength(1)
  expect(wrapper.text()).toContain('达到静音阈值')
  expect(wrapper.text()).toContain('允许切换：识别、暂停、终止')
})
it('enforces accessible minimum 1000ms silence configuration', async () => {
  const store = useSessionStore()
  const wrapper = mount(SessionControls)
  const input = wrapper.get('#silence-timeout')
  expect(input.attributes('aria-labelledby') || input.element.closest('[aria-labelledby]')).toBeTruthy()
  expect(wrapper.text()).toContain('最短 1000')
  store.silenceTimeout = 999
  await wrapper.vm.$nextTick()
  expect(wrapper.findAll('button').find(button => button.text() === '应用')?.attributes('disabled')).toBeDefined()
  wrapper.unmount()
})

it('distinguishes the configured cloud capability from actual session fallback', async () => {
  const session = useSessionStore()
  const health = useHealthStore()
  health.provider = 'cloud'
  health.stt = 'ready'
  health.cloud = true
  session.snapshot = { state: 'LISTENING', stt_provider: 'cloud', stt_provider_active: 'cloud', stt_fallback: false } as SessionSnapshot
  const wrapper = mount(ServiceHealthBar, { attachTo: document.body })
  await wrapper.get('button').trigger('click')
  await flushPromises()
  expect(document.body.textContent).toContain('云端识别')
  expect(document.body.textContent).not.toContain('云端识别已回退本地')
  session.snapshot = { ...session.snapshot, stt_provider_active: 'local', stt_fallback: true }
  await wrapper.vm.$nextTick()
  expect(wrapper.get('button').text()).toContain('识别已回退本地')
  expect(document.body.textContent).toContain('云端识别已回退本地')
  expect(document.body.textContent).toContain('本地 sherpa-onnx（云端回退）')
  expect(health.provider).toBe('cloud')
  expect(health.stt).toBe('ready')
  session.snapshot = { ...session.snapshot, stt_provider: 'local', stt_provider_active: 'local', stt_fallback: false }
  await wrapper.vm.$nextTick()
  expect(document.body.textContent).not.toContain('云端识别已回退本地')
  expect(wrapper.get('button').text()).not.toContain('已回退')
  wrapper.unmount()
})
