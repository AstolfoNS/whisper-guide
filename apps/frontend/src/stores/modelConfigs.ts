import { computed, ref } from 'vue'
import { defineStore } from 'pinia'
import { listModelConfigs, type ModelConfig } from '../api/modelConfigs.ts'
import { defaultLlmPresets } from '../api/models.ts'

export const useModelConfigsStore = defineStore('model-configs', () => {
  const profiles = ref<ModelConfig[]>([])
  const activeId = ref('')
  const activatedProfile = ref<ModelConfig>()
  const activeRevision = ref<number | null>(null)
  const presets = ref(defaultLlmPresets())
  const loading = ref(false)
  const storeAvailable = ref(true)
  const error = ref('')
  const activeProfile = computed(() => activatedProfile.value ?? profiles.value.find((profile) => profile.id === activeId.value))
  let pendingLoad: Promise<void> | undefined

  function load(): Promise<void> {
    if (pendingLoad) return pendingLoad
    loading.value = true
    error.value = ''
    pendingLoad = listModelConfigs()
      .then((result) => {
        storeAvailable.value = result.store_available !== false
        profiles.value = result.configs
        activeId.value = result.active_id
        activeRevision.value = result.active_revision
        const saved = result.configs.find((profile) => profile.id === result.active_id)
        activatedProfile.value = result.active_profile && saved ? { ...saved, ...result.active_profile } : undefined
        if (result.presets?.length) presets.value = result.presets
      })
      .catch((reason: unknown) => {
        error.value = reason instanceof Error ? reason.message : '无法读取配置列表。'
        throw reason
      })
      .finally(() => { loading.value = false; pendingLoad = undefined })
    return pendingLoad
  }

  return { profiles, activeId, activeRevision, presets, loading, error, activeProfile, storeAvailable, load }
})
