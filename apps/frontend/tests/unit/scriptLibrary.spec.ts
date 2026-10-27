import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import ScriptLibraryPanel from '../../src/components/scripts/ScriptLibraryPanel.vue'
import { useScriptsStore } from '../../src/stores/scripts'
import { useSessionStore } from '../../src/stores/session'
import * as api from '../../src/api/scripts'
import type { SessionSnapshot } from '../../src/api/session'

let wrapper: VueWrapper | undefined
function scriptDocument(id: string, name: string): api.ScriptDocument {
  return { id, name, content: name + '的正文。', character_count: 8, created_at: '2026-10-25T00:00:00Z' }
}
const a = scriptDocument('a', '一号讲稿.txt'), b = scriptDocument('b', '二号讲稿.txt')
function setup() {
  const store = useScriptsStore(), session = useSessionStore()
  store.scripts = [a, b]; store.selected = a; session.selectedScript = a
  wrapper = mount(ScriptLibraryPanel, { attachTo: document.body })
  return { store, session, wrapper }
}
function popupButton(text: string): HTMLButtonElement {
  const button = Array.from(globalThis.document.body.querySelectorAll('button')).find(item => item.textContent === text)
  expect(button, 'expected popup button ' + text).toBeTruthy()
  return button!
}
async function confirm(id: string) {
  await wrapper!.get('[data-script-id="' + id + '"] button').trigger('click')
  await flushPromises()
  popupButton('删除讲稿').click()
  await flushPromises()
}
beforeEach(() => { setActivePinia(createPinia()); vi.stubGlobal('fetch', vi.fn(async () => Response.json({}))) })
afterEach(() => { wrapper?.unmount(); wrapper = undefined; vi.restoreAllMocks(); vi.unstubAllGlobals(); globalThis.document.body.innerHTML = '' })

it('provides an accessible delete button outside the selection label and requires Chinese confirmation', async () => {
  const remove = vi.spyOn(api, 'deleteScript').mockResolvedValue(undefined)
  const get = vi.spyOn(api, 'getScript')
  const { store, wrapper } = setup()
  const button = wrapper.get<HTMLButtonElement>('[data-script-id="b"] button')
  expect(button.element.tagName).toBe('BUTTON')
  expect(button.attributes('type')).toBe('button')
  expect(button.attributes('aria-label')).toBe('删除讲稿“二号讲稿.txt”')
  expect(button.element.closest('label')).toBeNull()
  expect(button.element.disabled).toBe(false)
  await button.trigger('click')
  await flushPromises()
  expect(globalThis.document.body.textContent).toContain('确定删除讲稿“二号讲稿.txt”？此操作不可撤销。')
  expect(remove).not.toHaveBeenCalled()
  expect(get).not.toHaveBeenCalled()
  expect(store.selected).toEqual(a)
  popupButton('取消').click()
  await flushPromises()
  expect(remove).not.toHaveBeenCalled()
  expect(store.scripts).toHaveLength(2)
})

it('deletes a selected document only after confirmation and clears its preview', async () => {
  const remove = vi.spyOn(api, 'deleteScript').mockResolvedValue(undefined)
  vi.spyOn(api, 'listScripts').mockResolvedValue([b])
  const { store, session, wrapper } = setup()
  expect(wrapper.get('.preview pre').text()).toBe(a.content)
  await confirm('a')
  expect(remove).toHaveBeenCalledTimes(1)
  expect(store.scripts).toEqual([b])
  expect(store.selected).toBeUndefined()
  expect(session.selectedScript).toBeUndefined()
  expect(wrapper.find('.preview').exists()).toBe(false)
  expect(wrapper.find('[data-script-id="a"]').exists()).toBe(false)
  expect(wrapper.get('[role="status"]').text()).toContain('已删除讲稿“一号讲稿.txt”')
})

it('disables deletion of the active bound script with a reason but permits another document', async () => {
  const remove = vi.spyOn(api, 'deleteScript').mockResolvedValue(undefined)
  vi.spyOn(api, 'listScripts').mockResolvedValue([a])
  const session = useSessionStore()
  session.snapshot = { id: 'session', script_id: 'a', script_text: a.content, state: 'PAUSED' } as SessionSnapshot
  const snapshot = { ...session.snapshot }
  const { store, wrapper } = setup()
  const bound = wrapper.get<HTMLButtonElement>('[data-script-id="a"] button')
  expect(bound.element.disabled).toBe(true)
  const description = globalThis.document.getElementById(bound.attributes('aria-describedby'))
  expect(description?.textContent).toBe('讲稿正在会话中使用，请结束后再删除')
  expect(wrapper.get<HTMLInputElement>('[data-script-id="a"] input').element.disabled).toBe(true)
  expect(wrapper.get<HTMLButtonElement>('[data-script-id="b"] button').element.disabled).toBe(false)
  await confirm('b')
  expect(remove).toHaveBeenCalledWith('b', expect.any(AbortSignal))
  expect(store.selected).toEqual(a)
  expect(session.selectedScript).toEqual(a)
  expect(session.snapshot).toEqual(snapshot)
})

it('keeps a failed deletion in the list and shows the protected-session message', async () => {
  vi.spyOn(api, 'deleteScript').mockRejectedValue(new Error('讲稿正在会话中使用，请结束后再删除'))
  const list = vi.spyOn(api, 'listScripts')
  const { store, session, wrapper } = setup()
  await confirm('a')
  expect(wrapper.get('[role="alert"]').text()).toContain('讲稿正在会话中使用，请结束后再删除')
  expect(store.scripts).toEqual([a, b])
  expect(store.selected).toEqual(a)
  expect(session.selectedScript).toEqual(a)
  expect(wrapper.get('.preview pre').text()).toBe(a.content)
  expect(list).not.toHaveBeenCalled()
})

it('retains search and selection alongside the new delete controls', async () => {
  const get = vi.spyOn(api, 'getScript').mockResolvedValue(b)
  const { store, session, wrapper } = setup()
  await wrapper.get('input[placeholder="搜索讲稿"]').setValue('二号')
  expect(wrapper.findAll('.script-row')).toHaveLength(1)
  expect(wrapper.get('.script-row').attributes('data-script-id')).toBe('b')
  expect(wrapper.get('.script-row button').attributes('aria-label')).toContain(b.name)
  await wrapper.get('input[type="radio"]').setValue()
  await flushPromises()
  expect(get).toHaveBeenCalledWith('b', expect.any(AbortSignal))
  expect(store.selected).toEqual(b)
  expect(session.selectedScript).toEqual(b)
})

it('retains upload and selects the new document when no document was selected', async () => {
  const upload = vi.spyOn(api, 'uploadScripts').mockResolvedValue([b])
  vi.spyOn(api, 'listScripts').mockResolvedValue([a, b])
  vi.spyOn(api, 'getScript').mockResolvedValue(b)
  const { store, session, wrapper } = setup()
  store.selected = undefined; session.selectedScript = undefined
  const input = wrapper.get<HTMLInputElement>('input[type="file"]')
  const file = new File(['新的正文'], 'new.txt', { type: 'text/plain' })
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
  await flushPromises()
  expect(upload).toHaveBeenCalledWith([file], expect.any(AbortSignal))
  expect(store.selected).toEqual(b)
  expect(session.selectedScript).toEqual(b)
  expect(input.element.disabled).toBe(false)
})
