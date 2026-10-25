import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import TeleprompterView from '../../src/components/studio/TeleprompterView.vue'
import type { SessionSnapshot } from '../../src/api/session'

const text = '第一段。\n下一段。'
const units = [...text].flatMap((character, start) => /[\p{L}\p{N}]/u.test(character)
  ? [{ text: character, start, end: start + 1 }] : [])
function snapshot(overrides: Partial<SessionSnapshot> = {}): SessionSnapshot {
  return {
    script_id: 'boundary-script', state: 'LISTENING', reliable: true, speaking: false,
    reading_position: 2, prompt_position: 5, at_end: false, units, ...overrides,
  } as SessionSnapshot
}

it('keeps the last confirmed unit and next paragraph prompt on separate highlights', () => {
  const wrapper = mount(TeleprompterView, { props: { text, snapshot: snapshot() } })
  expect(wrapper.get('.reading-cursor').text()).toBe('段')
  expect(wrapper.get('.prompt-cursor').text()).toBe('下')
  expect(wrapper.get('.reading-cursor').element.closest('p')).not.toBe(wrapper.get('.prompt-cursor').element.closest('p'))
  expect(wrapper.get('[data-current="true"]').text()).toBe('段')
  expect(wrapper.get('.prompter-body').element.textContent).toBe(text)
  expect(wrapper.find('.end-note').exists()).toBe(false)
  wrapper.unmount()
})

it('retains the final reading highlight and explains why automatic prompting stops at the end', async () => {
  const wrapper = mount(TeleprompterView, { props: { text, snapshot: snapshot({ reading_position: 7, prompt_position: text.length, at_end: true }) } })
  expect(wrapper.get('.reading-cursor').text()).toBe('段')
  expect(wrapper.find('.prompt-cursor').exists()).toBe(false)
  const note = wrapper.get('.end-note')
  expect(note.text()).toContain('已到讲稿末尾，不再自动续播')
  expect(note.attributes('role')).toBe('status')
  expect(note.attributes('aria-live')).toBe('polite')
  await wrapper.setProps({ snapshot: snapshot({ reading_position: 7, prompt_position: 7 }) })
  expect(wrapper.find('.end-note').exists()).toBe(false)
  expect(wrapper.get('.prompt-cursor').text()).toBe('段')
  expect(wrapper.get('.reading-cursor').text()).toBe('段')
  wrapper.unmount()
})

it.each(['READY', 'PROMPTING', 'PAUSED', 'TERMINATED'] as const)('does not announce listening or completion while %s', state => {
  const wrapper = mount(TeleprompterView, { props: { text, snapshot: snapshot({ state, reading_position: 7, prompt_position: text.length, at_end: true }) } })
  expect(wrapper.find('.end-note').exists()).toBe(false)
  wrapper.unmount()
})

it.each([{ reliable: false }, { speaking: true }])('waits for reliable idle alignment before showing the end note (%o)', overrides => {
  const wrapper = mount(TeleprompterView, { props: { text, snapshot: snapshot({ reading_position: 7, prompt_position: text.length, at_end: true, ...overrides }) } })
  expect(wrapper.find('.end-note').exists()).toBe(false)
  wrapper.unmount()
})
