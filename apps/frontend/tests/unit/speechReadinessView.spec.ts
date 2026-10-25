import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ServiceHealthBar from '../../src/components/health/ServiceHealthBar.vue'
import SessionControls from '../../src/components/studio/SessionControls.vue'
import TranscriptComparison from '../../src/components/studio/TranscriptComparison.vue'
import { useSessionStore } from '../../src/stores/session'
import { speechFallbackMessage } from '../../src/services/speechDiagnostics'
import type { SessionSnapshot, SpeechFallbackReason } from '../../src/api/session'

vi.mock('../../src/services/sessionController', () => ({ getSessionController: () => ({}) }))
const wrappers: VueWrapper[] = []
beforeEach(() => setActivePinia(createPinia()))
afterEach(() => { for (const wrapper of wrappers.splice(0)) wrapper.unmount() })

it('asks the speaker to wait until the session recognizer is ready', async () => {
  const store = useSessionStore()
  store.snapshot = { state: 'LISTENING', stt_ready: false, units: [] } as unknown as SessionSnapshot
  const controls = mount(SessionControls), health = mount(ServiceHealthBar)
  wrappers.push(controls, health)
  expect(controls.get('[role="status"]').text()).toBe('正在连接语音识别，请稍候再朗读。')
  expect(health.get('button').text()).toContain('连接语音识别中')
  store.snapshot = { ...store.snapshot, stt_ready: true }
  await controls.vm.$nextTick()
  expect(controls.find('.speech-connecting').exists()).toBe(false)
  expect(health.get('button').text()).not.toContain('连接语音识别中')
})

it.each([
  [{ error_code: 'TIMEOUT', stage: 'connect', timeout_ms: 5000 }, '连接握手超时（5000 ms）'],
  [{ error_code: 'TIMEOUT', stage: 'initialize', timeout_ms: 1000 }, '服务初始化超时（1000 ms）'],
  [{ error_code: 'TIMEOUT', stage: 'result' }, '等待识别结果超时'],
  [{ error_code: 'HTTP_ERROR', stage: 'connect', http_status: 403 }, '云端语音请求失败（阶段：连接握手，HTTP 403）'],
  [{ error_code: 'PROVIDER_ERROR', stage: 'initialize', provider_error_code: 45000081 }, '云端语音服务返回错误（阶段：服务初始化，业务码 45000081）'],
] as const)('formats a safe Chinese fallback reason (%o)', (reason, expected) => {
  expect(speechFallbackMessage(reason)).toBe(expected)
})

it('shows the actual fallback provider and reason without changing the configured provider', async () => {
  const store = useSessionStore()
  store.snapshot = { state: 'LISTENING', stt_ready: true, stt_provider: 'cloud', stt_provider_active: 'local', stt_fallback: true,
    stt_fallback_reason: { error_code: 'HTTP_ERROR', stage: 'connect', http_status: 403 },
  } as SessionSnapshot
  const wrapper = mount(ServiceHealthBar, { attachTo: document.body })
  wrappers.push(wrapper)
  expect(wrapper.get('button').text()).toContain('HTTP 403')
  await wrapper.get('button').trigger('click')
  await flushPromises()
  expect(document.body.textContent).toContain('本地 sherpa-onnx（云端回退）')
  expect(document.body.querySelector('[data-testid="fallback-reason"]')?.textContent).toContain('HTTP 403')
  expect(store.snapshot.stt_provider).toBe('cloud')
})

it('never renders unexpected upstream strings as fallback labels or diagnostic codes', () => {
  const reason = { error_code: 'secret-key', stage: 'https://private.example.test', http_status: 'sk-private',
    provider_error_code: -1, timeout_ms: Infinity, message: 'Authorization: secret',
  } as unknown as SpeechFallbackReason
  expect(speechFallbackMessage(reason)).toBe('云端语音不可用')
  expect(speechFallbackMessage()).toBe('')
})

it('keeps the raw repeated recognition verbatim and replaces partials without appending the confirmed script', async () => {
  const raw = '我将来将继续继续历史立足立立足岗位'
  const confirmed = '我将继续立足岗位'
  const current = { state: 'PAUSED', raw_transcript: raw, aligned_transcript: confirmed, reliable: true,
    raw_transcript_is_current: true, raw_transcript_is_final: true, match_confidence: 1,
  } as SessionSnapshot
  const wrapper = mount(TranscriptComparison, { props: { snapshot: current } })
  wrappers.push(wrapper)
  expect(wrapper.get('[aria-label="实时语音识别"] p').text()).toBe(raw)
  expect(wrapper.get('[aria-label="实时语音识别"] .transcript-heading small').text()).toBe('上次结果')
  expect(wrapper.get('[aria-label="讲稿匹配"] > p').text()).toBe(confirmed)
  expect(wrapper.get('[aria-label="讲稿匹配"] .transcript-heading small').attributes('title')).toContain('不代表原始识别准确率')
  await wrapper.setProps({ snapshot: { ...current, state: 'LISTENING', raw_transcript: '我将继续', raw_transcript_is_final: false } })
  expect(wrapper.get('[aria-label="实时语音识别"] p').text()).toBe('我将继续')
  expect(wrapper.get('[aria-label="讲稿匹配"] > p').text()).toBe(confirmed)
})
