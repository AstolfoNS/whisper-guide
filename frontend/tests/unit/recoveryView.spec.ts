import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import TeleprompterView from '../../src/components/studio/TeleprompterView.vue'
import TranscriptComparison from '../../src/components/studio/TranscriptComparison.vue'
import type { SessionSnapshot } from '../../src/api/session'

const text = '第一段。\n下一段。'
const units = [...text].flatMap((character, start) => /[\p{L}\p{N}]/u.test(character)
  ? [{ text: character, start, end: start + 1 }] : [])
function snapshot(overrides: Partial<SessionSnapshot> = {}): SessionSnapshot {
  return {
    id: 'recovery-ui', script_id: 'script', script_name: '测试稿', script_text: text,
    state: 'LISTENING', reliable: false, speaking: true, silence_timeout_ms: 3000,
    reading_position: 2, prompt_position: 5, generation: 1, prompt: null,
    pause_reason: null, message: null, feedback_id: 0, last_transcript: '第一段',
    raw_transcript: '下一', raw_transcript_is_final: false, aligned_transcript: '第一段',
    match_confidence: 1, at_end: false, alignment_status: 'pending', units, ...overrides,
  }
}

it('shows provisional text and a dashed cursor separately from confirmed reading and prompt positions', async () => {
  const current = snapshot({ provisional_position: 6, provisional_transcript: '下一' })
  const prompter = mount(TeleprompterView, { props: { text, snapshot: current } })
  const comparison = mount(TranscriptComparison, { props: { snapshot: current } })
  expect(prompter.get('.reading-cursor').text()).toBe('段')
  expect(prompter.get('.prompt-cursor').text()).toBe('下')
  expect(prompter.get('.provisional-cursor').text()).toBe('一')
  expect(prompter.get('[data-current="true"]').text()).toBe('段')
  expect(prompter.get('[data-provisional="true"]').attributes('title')).toContain('尚未确认')
  expect(prompter.text()).toContain('临时定位（待确认）')
  const candidate = comparison.get('[aria-label="临时定位，待确认"]')
  expect(candidate.text()).toContain('下一')
  expect(candidate.text()).toContain('自动提示仍使用已确认位置')
  expect(comparison.get('[aria-label="讲稿匹配"] > p').text()).toBe('第一段')
  await prompter.setProps({ snapshot: { ...current, provisional_position: 7, provisional_transcript: '下一段' } })
  expect(prompter.findAll('.provisional-cursor')).toHaveLength(1)
  expect(prompter.get('.provisional-cursor').text()).toBe('段')
  expect(prompter.get('.reading-cursor').element).not.toBe(prompter.get('.provisional-cursor').element)
  expect(prompter.get('.prompt-cursor').text()).toBe('下')
  expect(prompter.find('.end-note').exists()).toBe(false)
  prompter.unmount()
  comparison.unmount()
})

it.each([-1, 3, text.length, 2])('does not display an invalid or already confirmed provisional position (%s)', position => {
  const current = snapshot({ provisional_position: position, provisional_transcript: '下一' })
  const prompter = mount(TeleprompterView, { props: { text, snapshot: current } })
  const comparison = mount(TranscriptComparison, { props: { snapshot: current } })
  expect(prompter.find('.provisional-cursor').exists()).toBe(false)
  expect(comparison.find('.provisional-match').exists()).toBe(false)
  expect(prompter.get('.reading-cursor').text()).toBe('段')
  prompter.unmount()
  comparison.unmount()
})

it('removes provisional markers when pausing or confirming the candidate', async () => {
  const current = snapshot({ provisional_position: 6, provisional_transcript: '下一' })
  const wrapper = mount(TeleprompterView, { props: { text, snapshot: current } })
  await wrapper.setProps({ snapshot: { ...current, state: 'PAUSED' } })
  expect(wrapper.find('.provisional-cursor').exists()).toBe(false)
  await wrapper.setProps({ snapshot: { ...current, reading_position: 6, provisional_position: null, reliable: true } })
  expect(wrapper.find('.provisional-cursor').exists()).toBe(false)
  expect(wrapper.get('.reading-cursor').text()).toBe('一')
  wrapper.unmount()
})

it.each([
  ['searching', '正在尝试自动恢复'],
  ['ambiguous', '多处相似内容'],
  ['confirming', '等待后续朗读确认'],
] as const)('describes %s recovery without claiming a confirmed location', (recovery_status, message) => {
  const wrapper = mount(TranscriptComparison, { props: { snapshot: snapshot({
    alignment_status: 'mismatch', recovery_status, recovery_attempts: 2,
  }) } })
  const alignment = wrapper.get('[aria-label="讲稿匹配"]')
  expect(alignment.text()).toContain(message)
  expect(alignment.text()).toContain('自动提示暂缓')
  expect(alignment.text()).toContain('已尝试 2 次')
  expect(alignment.text()).not.toContain('将自动重新定位')
  expect(alignment.get('p').text()).toBe('第一段')
  wrapper.unmount()
})

it('keeps older snapshots usable without provisional or recovery fields', () => {
  const current = snapshot({ reliable: true, speaking: false, alignment_status: 'reliable' })
  const wrapper = mount(TranscriptComparison, { props: { snapshot: current } })
  expect(wrapper.find('.provisional-match').exists()).toBe(false)
  expect(wrapper.find('.recovery-attempts').exists()).toBe(false)
  expect(wrapper.find('[role="status"]').exists()).toBe(false)
  wrapper.unmount()
})
