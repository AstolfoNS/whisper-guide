import { defineStore } from 'pinia'

export type EngineStatus = 'unknown' | 'ready' | 'unavailable' | 'fallback'
export const useHealthStore = defineStore('health', {
  state: () => ({
    backend: 'unknown' as EngineStatus, stt: 'unknown' as EngineStatus,
    vad: 'unknown' as EngineStatus, tts: 'unknown' as EngineStatus,
    provider: 'local', llmMode: 'off', model: '', configName: '',
    checking: false, checkedAt: '', detail: '', cloud: false,
    playback: 'unknown' as 'unknown' | 'success' | 'failed',
  }),
})
