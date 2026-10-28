import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import SessionControls from '../../src/components/studio/SessionControls.vue'
import ServiceHealthBar from '../../src/components/health/ServiceHealthBar.vue'
import TranscriptComparison from '../../src/components/studio/TranscriptComparison.vue'
import { useSessionStore } from '../../src/stores/session'
import { promptWaitLabel, silenceTimingLabel, progressTimingLabel } from '../../src/services/promptDiagnostics'
import type { SessionSnapshot } from '../../src/api/session'
vi.mock('../../src/services/sessionController', () => ({ getSessionController: () => ({}) }))
const wrappers: VueWrapper[] = []
beforeEach(() => setActivePinia(createPinia()))
afterEach(() => { for (const wrapper of wrappers.splice(0)) wrapper.unmount() })
const current = (overrides: Partial<SessionSnapshot> = {}) => ({ state: 'LISTENING', units: [],
  silence_timeout_ms: 3000, silence_prompt_policy: 'saved_position', reliable: false, speaking: false,
  alignment_status: 'pending', raw_transcript: '大家好', raw_transcript_is_current: false,
  aligned_transcript: '大家好', prompt_wait_reason: 'silence_wait', silence_elapsed_ms: 1800,
  silence_remaining_ms: 1200, audio_queued_ms: 0, audio_inference_ms: 12, audio_batch_frames: 1,
  last_audio_received_age_ms: 15, ...overrides } as SessionSnapshot)

it('explains that pending or mismatched recognition will use the saved prompt position after silence', () => {
  for (const alignment_status of ['pending', 'mismatch'] as const) {
    const wrapper = mount(TranscriptComparison, { props: { snapshot: current({ alignment_status }) } })
    wrappers.push(wrapper)
    expect(wrapper.get('[role="status"]').text()).toContain('静音等待到期后从已保存的提示起点播报')
    expect(wrapper.get('[role="status"]').text()).not.toContain('自动提示暂缓')
    expect(wrapper.get('[aria-label="实时语音识别"] p').text()).toBe('大家好')
  }
})

it('shows the server silence clock and distinguishes queue backlog from missing audio or active voice', async () => {
  const session = useSessionStore()
  session.snapshot = current()
  const wrapper = mount(SessionControls)
  wrappers.push(wrapper)
  expect(wrapper.get('.prompt-wait').text()).toContain('已静音 1800 / 3000 ms')
  for (const [reason, expected] of [['audio_backlog', '积压音频'], ['audio_missing', '无法判定静音'], ['voice_active', '检测到声音']] as const) {
    session.snapshot = current({ prompt_wait_reason: reason, silence_elapsed_ms: null })
    await wrapper.vm.$nextTick()
    expect(wrapper.get('.prompt-wait').text()).toContain(expected)
    expect(wrapper.get('.prompt-wait').text()).not.toContain('已静音')
  }
})

it('shows queue duration, processing cost and audio arrival age without claiming speech accuracy', async () => {
  useSessionStore().snapshot = current({ prompt_wait_reason: 'audio_backlog', audio_queued_ms: 260 })
  const wrapper = mount(ServiceHealthBar, { attachTo: document.body })
  wrappers.push(wrapper)
  await wrapper.get('button').trigger('click')
  await flushPromises()
  expect(document.body.textContent).toContain('排队 260 ms')
  expect(document.body.textContent).toContain('最近推理 12 ms / 1 帧')
  expect(document.body.textContent).toContain('15 ms 前')
  expect(document.body.textContent).toContain('正在处理积压音频')
})

it('keeps missing clock values explicit and does not infer silence from a stale transcript', () => {
  expect(silenceTimingLabel()).toBe('尚未开始')
  expect(promptWaitLabel()).toBe('')
  expect(silenceTimingLabel(current({ silence_elapsed_ms: 4000 }))).toBe('已静音 3000 / 3000 ms')
})

it('uses only the server progress countdown and explains one-shot waiting', () => {
  const snapshot = current({ prompt_mode: 'progress', progress_timeout_ms: 4000,
    progress_elapsed_ms: 1800, progress_remaining_ms: 2200, progress_prompt_armed: true, prompt_wait_reason: 'voice_active' })
  expect(promptWaitLabel(snapshot)).toBe('等待讲稿有效推进')
  expect(progressTimingLabel(snapshot)).toBe('进度未推进 · 2.2 秒后提示')
  expect(progressTimingLabel({ ...snapshot, progress_remaining_ms: -50 })).toContain('0.0 秒')
  expect(promptWaitLabel({ ...snapshot, progress_prompt_armed: false })).toContain('等待继续朗读')
  expect(progressTimingLabel({ ...snapshot, progress_prompt_armed: false })).toBe('本轮自动提示已完成')
  expect(promptWaitLabel({ ...snapshot, prompt_wait_reason: 'audio_backlog' })).toContain('积压音频')
})

it('keeps uncertain progress mode feedback explicit instead of claiming a next recognized character', () => {
  const wrapper = mount(TranscriptComparison, { props: { snapshot: current({ prompt_mode: 'progress',
    progress_prompt_armed: true, reliable: false, alignment_status: 'mismatch' }) } })
  wrappers.push(wrapper)
  expect(wrapper.text()).toContain('从已保存的提示起点播报')
  expect(wrapper.text()).toContain('不把系统播报视为用户已读')
})
