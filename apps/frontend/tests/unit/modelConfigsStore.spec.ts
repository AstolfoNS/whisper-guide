import { afterEach, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import * as api from '../../src/api/modelConfigs'
import { normalizeModelSettings } from '../../src/api/models'
import { useModelConfigsStore } from '../../src/stores/modelConfigs'

afterEach(() => vi.restoreAllMocks())

it('shows the activated configuration snapshot while saved changes await activation', async () => {
  setActivePinia(createPinia())
  const saved = api.normalizeModelConfig({ id: 'a', name: '待生效名称', revision: 3, is_active: true, pending_activation: true, settings: { llm_align_mode: 'hybrid', llm_model: 'new-model' } })
  vi.spyOn(api, 'listModelConfigs').mockResolvedValue({ configs: [saved], active_id: 'a', active_revision: 2, active_profile: { id: 'a', name: '当前生效名称', revision: 2, settings: normalizeModelSettings({ llm_align_mode: 'off', llm_model: '' }) }, presets: [] })
  const store = useModelConfigsStore()
  await store.load()
  expect(store.profiles[0].settings.llm_model).toBe('new-model')
  expect(store.activeProfile?.name).toBe('当前生效名称')
  expect(store.activeProfile?.revision).toBe(2)
  expect(store.activeProfile?.settings.llm_align_mode).toBe('off')
  expect(store.activeProfile?.pending_activation).toBe(true)
})

it('keeps corrupt credential storage visible as read-only and deduplicates concurrent loads', async () => {
  setActivePinia(createPinia())
  const request = vi.spyOn(api, 'listModelConfigs').mockResolvedValue({ configs: [], active_id: '', active_revision: null, store_available: false, presets: [] })
  const store = useModelConfigsStore()
  await Promise.all([store.load(), store.load()])
  expect(request).toHaveBeenCalledTimes(1)
  expect(store.storeAvailable).toBe(false)
})
