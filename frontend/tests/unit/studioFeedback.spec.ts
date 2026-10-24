import { afterEach, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import StudioView from '../../src/views/StudioView.vue'
import TranscriptComparison from '../../src/components/studio/TranscriptComparison.vue'
import { useSessionStore } from '../../src/stores/session'
import { useScriptsStore } from '../../src/stores/scripts'
import type { SessionSnapshot } from '../../src/api/session'

vi.mock('../../src/services/sessionController', () => ({ getSessionController: () => ({ enterStudio: vi.fn() }) }))

let mounted: VueWrapper | undefined
const mismatchMessage = '暂时无法可靠定位讲稿，自动提示已暂停；可手动提示或重新定位。'
function snapshot(overrides: Partial<SessionSnapshot> = {}): SessionSnapshot {
  return {
    id: 'session-feedback', script_id: 'script', script_name: '测试讲稿', state: 'LISTENING',
    silence_timeout_ms: 1000, reading_position: null, prompt_position: 0, generation: 1,
    prompt: null, reliable: false, speaking: false, pause_reason: null, message: null,
    feedback_id: 1, last_transcript: '', raw_transcript: '', aligned_transcript: '',
    match_confidence: null, at_end: false, units: [], ...overrides,
  }
}
async function studio(current: SessionSnapshot) {
  const pinia = createPinia()
  const session = useSessionStore(pinia)
  session.snapshot = current
  session.notice = current.message ?? ''
  vi.spyOn(useScriptsStore(pinia), 'refresh').mockResolvedValue(undefined)
  const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/studio', component: StudioView }] })
  await router.push('/studio')
  await router.isReady()
  mounted = mount(StudioView, { global: { plugins: [pinia, router], stubs: { ScriptLibraryPanel: true, TeleprompterView: true, SessionControls: true, StateMachineMonitor: true } } })
  await flushPromises()
  return { wrapper: mounted, session }
}
afterEach(() => { mounted?.unmount(); mounted = undefined; vi.restoreAllMocks() })

it('shows an alignment failure and removes stale feedback as soon as the position is reliable again', async () => {
  const { wrapper, session } = await studio(snapshot({ raw_transcript: '无关内容', raw_transcript_is_final: true, last_transcript: '无关内容', message: mismatchMessage }))
  expect(wrapper.get('[role="alert"]').text()).toBe(mismatchMessage)
  expect(wrapper.get('.transcript-comparison [role="status"]').text()).toContain('自动播报已暂停')
  session.snapshot = { ...session.snapshot!, reliable: true, aligned_transcript: '大家好', match_confidence: 0.95 }
  await wrapper.vm.$nextTick()
  expect(session.notice).toBe(mismatchMessage)
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.find('.transcript-comparison [role="status"]').exists()).toBe(false)
  expect(wrapper.text()).not.toContain(mismatchMessage)
  session.snapshot = { ...session.snapshot!, reliable: false, speaking: false, raw_transcript: '今天', raw_transcript_is_final: false }
  await wrapper.vm.$nextTick()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.get('.transcript-comparison [role="status"]').text()).toContain('等待识别定稿')
  session.snapshot = { ...session.snapshot!, feedback_id: 2, raw_transcript: '再次无关', raw_transcript_is_final: true, last_transcript: '再次无关' }
  await wrapper.vm.$nextTick()
  expect(wrapper.get('[role="alert"]').text()).toBe(mismatchMessage)
})

it('explains missing recognition text without claiming that existing text failed alignment', () => {
  mounted = mount(TranscriptComparison, { props: { snapshot: snapshot() } })
  expect(mounted.get('[role="status"]').text()).toContain('尚未获得有效识别文本')
  expect(mounted.text()).toContain('自动播报暂缓')
  expect(mounted.text()).not.toContain('未能可靠匹配讲稿')
})

it('distinguishes pending partial recognition from a final alignment failure', async () => {
  mounted = mount(TranscriptComparison, { props: { snapshot: snapshot({ raw_transcript: '大家', raw_transcript_is_final: false }) } })
  expect(mounted.get('[role="status"]').text()).toContain('等待识别定稿')
  await mounted.setProps({ snapshot: snapshot({ raw_transcript: '无关内容', raw_transcript_is_final: true, last_transcript: '无关内容' }) })
  expect(mounted.get('[role="status"]').text()).toContain('未能可靠匹配讲稿')
})

it('treats ongoing speech as waiting for stable recognition', () => {
  mounted = mount(TranscriptComparison, { props: { snapshot: snapshot({ speaking: true }) } })
  expect(mounted.get('[role="status"]').text()).toContain('正在接收语音')
  expect(mounted.text()).not.toContain('自动播报已暂停')
})

it('shows a service pause reason but hides the stale reason after resuming', async () => {
  const { wrapper, session } = await studio(snapshot({ state: 'PAUSED', reliable: true, transition_trigger: 'service_fault', pause_reason: '麦克风数据中断，已暂停。', message: '麦克风数据中断，已暂停。' }))
  expect(wrapper.get('[role="alert"]').text()).toContain('麦克风数据中断')
  session.snapshot = { ...session.snapshot!, state: 'LISTENING', pause_reason: null, transition_trigger: 'user_resumed' }
  await wrapper.vm.$nextTick()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})


it('keeps confirmed mismatch feedback stable while VAD speech starts and ends', async () => {
  const { wrapper, session } = await studio(snapshot({ alignment_status: 'mismatch', raw_transcript: '来', raw_transcript_is_final: true, raw_transcript_is_current: true, last_transcript: '来', aligned_transcript: '我要', message: mismatchMessage }))
  for (const speaking of [false, true, false, true]) {
    session.snapshot = { ...session.snapshot!, speaking }
    await wrapper.vm.$nextTick()
    expect(wrapper.get('[role="alert"]').text()).toBe(mismatchMessage)
    const alignment = wrapper.get('[aria-label="讲稿匹配"]')
    expect(alignment.text()).toContain('最近一次有效识别未能可靠匹配讲稿')
    expect(alignment.text()).toContain('自动提示暂缓')
    expect(alignment.text()).not.toContain('正在接收语音')
  }
})

it('clears confirmed mismatch feedback after recovery and does not revive it during a new pending utterance', async () => {
  const { wrapper, session } = await studio(snapshot({ alignment_status: 'mismatch', raw_transcript: '来', raw_transcript_is_final: true, last_transcript: '来', message: mismatchMessage }))
  session.snapshot = { ...session.snapshot!, alignment_status: 'reliable', reliable: true, last_transcript: '大家好', raw_transcript: '大家好', raw_transcript_is_current: true, aligned_transcript: '大家好', match_confidence: 1 }
  await wrapper.vm.$nextTick()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.get('[aria-label="讲稿匹配"]').text()).not.toContain('自动提示暂缓')
  session.snapshot = { ...session.snapshot!, alignment_status: 'pending', reliable: false, speaking: false, raw_transcript_is_current: false }
  await wrapper.vm.$nextTick()
  expect(session.notice).toBe(mismatchMessage)
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.get('[aria-label="讲稿匹配"]').text()).toContain('等待本轮有效识别结果')
  expect(wrapper.get('[aria-label="讲稿匹配"]').text()).toContain('上次匹配 100%')
  expect(wrapper.get('[aria-label="实时语音识别"] .transcript-heading small').text()).toBe('上次结果')
})

it('shows recovery progress without repeating a stale failure or promising successful relocation', async () => {
  const { wrapper, session } = await studio(snapshot({
    alignment_status: 'mismatch', recovery_status: 'searching', recovery_attempts: 1,
    raw_transcript: '正在继续读', raw_transcript_is_final: true,
    message: '继续朗读一小段讲稿将自动重新定位。',
  }))
  expect(wrapper.get('[role="alert"]').text()).toContain('正在尝试自动恢复')
  expect(wrapper.get('[role="alert"]').text()).not.toContain('将自动重新定位')
  session.snapshot = { ...session.snapshot!, recovery_status: 'ambiguous' }
  await wrapper.vm.$nextTick()
  expect(wrapper.get('[role="alert"]').text()).toContain('多处相似内容')
  session.snapshot = { ...session.snapshot!, recovery_status: 'confirming' }
  await wrapper.vm.$nextTick()
  expect(wrapper.get('[role="alert"]').text()).toContain('等待后续朗读确认')
  session.snapshot = { ...session.snapshot!, recovery_status: 'idle', reliable: true, alignment_status: 'reliable' }
  await wrapper.vm.$nextTick()
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
})


it('shows the connecting state without reviving an old mismatch instruction before speech is ready', async () => {
  const { wrapper } = await studio(snapshot({ stt_ready: false, alignment_status: 'mismatch', message: mismatchMessage }))
  expect(wrapper.text()).toContain('连接识别中')
  expect(wrapper.find('[role="alert"]').exists()).toBe(false)
  expect(wrapper.get('[aria-label="讲稿匹配"] [role="status"]').text()).toContain('正在连接语音识别，请稍候再朗读')
})
