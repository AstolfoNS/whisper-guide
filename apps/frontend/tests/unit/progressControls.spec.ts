import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import SessionControls from '../../src/components/studio/SessionControls.vue'
import { useSessionStore } from '../../src/stores/session'
import type { SessionSnapshot } from '../../src/api/session'

const controller = vi.hoisted(() => ({
  start: vi.fn(), resume: vi.fn(), pause: vi.fn(), interrupt: vi.fn(), manualPrompt: vi.fn(),
  terminate: vi.fn(), postponePrompt: vi.fn(), configure: vi.fn(), reposition: vi.fn(),
}))
vi.mock('../../src/services/sessionController', () => ({ getSessionController: () => controller }))
let wrapper: VueWrapper | undefined
beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks() })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; document.body.innerHTML = '' })

it('shows an authoritative progress countdown and postpones without a local timer', async () => {
  const session = useSessionStore()
  session.promptMode = 'progress'
  session.snapshot = { id: 's', state: 'LISTENING', stt_ready: true, units: [], prompt_mode: 'progress',
    progress_prompt_armed: true, progress_remaining_ms: 2300, prompt_wait_reason: 'progress_wait' } as SessionSnapshot
  wrapper = mount(SessionControls)
  expect(wrapper.get('.prompt-wait').text()).toContain('2.3 秒后提示')
  const postpone = wrapper.findAll('button').find(button => button.text() === '再等一下')!
  await postpone.trigger('click')
  expect(controller.postponePrompt).toHaveBeenCalledTimes(1)
  expect(session.snapshot.progress_remaining_ms).toBe(2300)
})

it('provides bounded progress settings and an explicit continuous-reading opt-in', async () => {
  const session = useSessionStore()
  session.promptMode = 'progress'
  wrapper = mount(SessionControls, { attachTo: document.body })
  await wrapper.get('[aria-label="更多演讲操作"]').trigger('click')
  await flushPromises()
  expect(document.body.textContent).toContain('慢读、重复练习或改述时也可能插播')
  expect(document.body.textContent).toContain('默认只提示一个短句')
  const timeout = document.querySelector<HTMLInputElement>('#progress-timeout')!
  expect(timeout).not.toBeNull()
  expect(timeout.value).toBe('4000')
  expect(session.continuousPrompt).toBe(false)
  timeout.value = '5500'
  timeout.dispatchEvent(new Event('input', { bubbles: true }))
  timeout.dispatchEvent(new Event('change', { bubbles: true }))
  timeout.dispatchEvent(new FocusEvent('blur', { bubbles: true }))
  await flushPromises()
  expect(session.progressTimeout).toBe(5500)
  expect(session.newSessionOptions()).toMatchObject({ prompt_mode: 'progress', progress_timeout_ms: 5500 })
  const apply = Array.from(document.querySelectorAll('button')).find(button => button.textContent === '应用演讲设置')!
  apply.click()
  await flushPromises()
  expect(controller.configure).toHaveBeenCalledTimes(1)
})

it('keeps stop playback available while timing preferences are locked during recognition', async () => {
  const session = useSessionStore()
  session.snapshot = { id: 's', state: 'PROMPTING', units: [] } as unknown as SessionSnapshot
  wrapper = mount(SessionControls, { attachTo: document.body })
  const stop = wrapper.findAll('button').find(button => button.text() === '打断播报')!
  await stop.trigger('click')
  expect(controller.interrupt).toHaveBeenCalledTimes(1)
  await wrapper.get('[aria-label="更多演讲操作"]').trigger('click')
  await flushPromises()
  expect(document.body.textContent).toContain('请先暂停演讲')
  const apply = Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(button => button.textContent === '应用演讲设置')!
  expect(apply.disabled).toBe(true)
})
it('requires the same explicit confirmation for desktop end and keyboard requests', async () => {
  const session = useSessionStore()
  session.snapshot = { id: 's', state: 'LISTENING', units: [] } as unknown as SessionSnapshot
  wrapper = mount(SessionControls, { attachTo: document.body })
  const end = wrapper.findAll('button').find(button => button.text() === '结束')!
  await end.trigger('click')
  await flushPromises()
  expect(controller.terminate).not.toHaveBeenCalled()
  const cancel = Array.from(document.querySelectorAll('button')).find(button => button.textContent === '取消')!
  cancel.click()
  await flushPromises()
  expect(controller.terminate).not.toHaveBeenCalled()
  window.dispatchEvent(new CustomEvent('request-session-end'))
  await flushPromises()
  const confirm = Array.from(document.querySelectorAll('button')).find(button => button.textContent === '确认结束')!
  expect(confirm).toBeTruthy()
  confirm.click()
  await flushPromises()
  expect(controller.terminate).toHaveBeenCalledTimes(1)
})

it('removes the keyboard request listener when the control view is disposed', async () => {
  const session = useSessionStore()
  session.snapshot = { id: 's', state: 'PAUSED', units: [] } as unknown as SessionSnapshot
  wrapper = mount(SessionControls, { attachTo: document.body })
  wrapper.unmount(); wrapper = undefined
  window.dispatchEvent(new CustomEvent('request-session-end'))
  await flushPromises()
  expect(controller.terminate).not.toHaveBeenCalled()
  expect(document.body.textContent).not.toContain('确认结束')
})

it('exposes a native labelled mode select and records an explicit user change', async () => {
  const session = useSessionStore()
  wrapper = mount(SessionControls, { attachTo: document.body })
  await wrapper.get('[aria-label="更多演讲操作"]').trigger('click')
  await flushPromises()
  const mode = document.querySelector<HTMLSelectElement>('select#prompt-mode')!
  expect(mode).not.toBeNull()
  expect(mode.getAttribute('aria-label')).toBe('自动提示模式')
  expect(Array.from(mode.options).map(option => option.value)).toEqual(['silence', 'progress'])
  mode.value = 'progress'
  mode.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
  expect(session.promptMode).toBe('progress')
  expect(session.promptModeEdited).toBe(true)
  expect(session.newSessionOptions()).toMatchObject({ prompt_mode: 'progress' })
  expect(document.querySelector('#progress-timeout')).not.toBeNull()
  session.snapshot = { id: 's', state: 'LISTENING', units: [] } as unknown as SessionSnapshot
  await flushPromises()
  expect(mode.disabled).toBe(true)
})
