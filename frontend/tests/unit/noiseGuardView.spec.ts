import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import TranscriptComparison from '../../src/components/studio/TranscriptComparison.vue'
import type { SessionSnapshot } from '../../src/api/session'

const snapshot = {
  state: 'LISTENING', reliable: true, speaking: false, raw_transcript: '点',
  raw_transcript_is_final: true, aligned_transcript: '我会坚持自律，好好',
  recognition_filtered: true, recognition_filter_reason: '缺少本段有效人声证据',
} as SessionSnapshot

it('shows a filtered raw final separately without claiming that the aligned script failed', () => {
  const wrapper = mount(TranscriptComparison, { props: { snapshot } })
  const raw = wrapper.get('[aria-label="实时语音识别"]')
  expect(raw.text()).toContain('final · 已过滤')
  expect(raw.text()).toContain('点')
  expect(raw.get('[role="status"]').text()).toContain('静音等待继续')
  expect(wrapper.get('[aria-label="讲稿匹配"]').text()).toContain('我会坚持自律，好好')
  expect(wrapper.text()).not.toContain('自动播报已暂停')
  wrapper.unmount()
})

it('keeps the partial label for a filtered hypothesis and removes the filter when speech is accepted', async () => {
  const wrapper = mount(TranscriptComparison, { props: { snapshot: { ...snapshot, raw_transcript_is_final: false } } })
  expect(wrapper.text()).toContain('partial · 已过滤')
  await wrapper.setProps({ snapshot: { ...snapshot, recognition_filtered: false, raw_transcript: '吃饭', aligned_transcript: '好好吃饭' } })
  expect(wrapper.text()).toContain('final · 已定稿')
  expect(wrapper.text()).not.toContain('已过滤')
  expect(wrapper.find('[role="status"]').exists()).toBe(false)
  wrapper.unmount()
})

it('does not claim that an already unreliable position resumes automatically after filtering noise', () => {
  const wrapper = mount(TranscriptComparison, { props: { snapshot: { ...snapshot, reliable: false, last_transcript: '真实脱稿', raw_transcript_is_final: true } } })
  expect(wrapper.text()).toContain('请参考当前讲稿匹配状态')
  expect(wrapper.text()).toContain('自动播报已暂停')
  expect(wrapper.text()).not.toContain('静音等待继续')
  wrapper.unmount()
})
