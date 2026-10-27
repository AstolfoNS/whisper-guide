import { defineStore } from 'pinia'
import type { ScriptDocument } from '../api/scripts'
import type { SessionSnapshot, SessionState } from '../api/session'
import { loadAppConfig, runtimeDefaults } from '../services/appConfig'

export const useSessionStore = defineStore('session', {
  state: () => ({
    snapshot: undefined as SessionSnapshot | undefined,
    selectedScript: undefined as ScriptDocument | undefined,
    busy: false, error: '', notice: '', localPaused: false,
    connection: 'disconnected' as 'disconnected' | 'connecting' | 'connected',
    microphone: 'idle' as 'idle' | 'requesting' | 'active' | 'error',
    silenceTimeout: runtimeDefaults.value.defaultSilenceTimeoutMs,
    silenceTimeoutEdited: false,
    appliedSilenceDefault: runtimeDefaults.value.defaultSilenceTimeoutMs,
    recoverySessionId: '',
    recoverableSessions: [] as { session_id: string; script_name: string; state: SessionState; config_name: string }[],
  }),
  actions: {
    markSilenceTimeoutEdited() { this.silenceTimeoutEdited = true },
    newSessionTimeout(): number | undefined {
      return this.silenceTimeoutEdited || this.silenceTimeout !== this.appliedSilenceDefault ? this.silenceTimeout : undefined
    },
    applyServerDefaults() {
      if (this.snapshot || this.silenceTimeoutEdited || this.silenceTimeout !== this.appliedSilenceDefault) return
      this.silenceTimeout = runtimeDefaults.value.defaultSilenceTimeoutMs
      this.appliedSilenceDefault = runtimeDefaults.value.defaultSilenceTimeoutMs
    },
    async initializeDefaults() {
      await loadAppConfig()
      this.applyServerDefaults()
    },
  },
  getters: {
    state: (store): SessionState => store.localPaused ? 'PAUSED' : store.snapshot?.state ?? 'READY',
    active: (store): boolean => Boolean(store.snapshot && store.snapshot.state !== 'TERMINATED'),
  },
})
