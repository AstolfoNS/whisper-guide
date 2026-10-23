import { defineStore } from 'pinia'
import type { ScriptDocument } from '../api/scripts'
import type { SessionSnapshot, SessionState } from '../api/session'

export const useSessionStore = defineStore('session', {
  state: () => ({
    snapshot: undefined as SessionSnapshot | undefined,
    selectedScript: undefined as ScriptDocument | undefined,
    busy: false, error: '', notice: '', localPaused: false,
    connection: 'disconnected' as 'disconnected' | 'connecting' | 'connected',
    microphone: 'idle' as 'idle' | 'requesting' | 'active' | 'error',
    silenceTimeout: 3000,
    recoverySessionId: '',
    recoverableSessions: [] as { session_id: string; script_name: string; state: SessionState; config_name: string }[],
  }),
  getters: {
    state: (store): SessionState => store.localPaused ? 'PAUSED' : store.snapshot?.state ?? 'READY',
    active: (store): boolean => Boolean(store.snapshot && store.snapshot.state !== 'TERMINATED'),
  },
})
