import { defineStore } from 'pinia'
import type { ScriptDocument } from '../api/scripts'
import type { PromptMode, PromptOptions, SessionSnapshot, SessionState } from '../api/session'
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
    promptMode: runtimeDefaults.value.defaultPromptMode as PromptMode,
    progressTimeout: runtimeDefaults.value.defaultProgressTimeoutMs,
    continuousPrompt: runtimeDefaults.value.defaultContinuousPrompt,
    promptModeEdited: false, progressTimeoutEdited: false, continuousPromptEdited: false,
    appliedPromptMode: runtimeDefaults.value.defaultPromptMode as PromptMode,
    appliedProgressTimeout: runtimeDefaults.value.defaultProgressTimeoutMs,
    appliedContinuousPrompt: runtimeDefaults.value.defaultContinuousPrompt,
    optionDraftSessionId: '',
    pendingOptionEdits: { silence: false, mode: false, timeout: false, continuous: false },
    recoverySessionId: '',
    recoverableSessions: [] as { session_id: string; script_name: string; state: SessionState; config_name: string }[],
  }),
  actions: {
    markPendingOptionEdit(option: 'silence' | 'mode' | 'timeout' | 'continuous') {
      if (!this.active || !['READY', 'PAUSED'].includes(this.state)) return
      const id = this.snapshot!.id
      if (this.optionDraftSessionId !== id) {
        this.pendingOptionEdits = { silence: false, mode: false, timeout: false, continuous: false }
        this.optionDraftSessionId = id
      }
      this.pendingOptionEdits[option] = true
    },
    markSilenceTimeoutEdited() { this.silenceTimeoutEdited = true; this.markPendingOptionEdit('silence') },
    markPromptOptionEdited(option: 'mode' | 'timeout' | 'continuous') {
      if (option === 'mode') this.promptModeEdited = true
      else if (option === 'timeout') this.progressTimeoutEdited = true
      else this.continuousPromptEdited = true
      this.markPendingOptionEdit(option)
    },
    newSessionTimeout(): number | undefined {
      return this.silenceTimeoutEdited || this.silenceTimeout !== this.appliedSilenceDefault ? this.silenceTimeout : undefined
    },
    newSessionOptions(): Partial<PromptOptions> {
      return {
        ...(this.promptModeEdited || this.promptMode !== this.appliedPromptMode ? { prompt_mode: this.promptMode } : {}),
        ...(this.progressTimeoutEdited || this.progressTimeout !== this.appliedProgressTimeout ? { progress_timeout_ms: this.progressTimeout } : {}),
        ...(this.continuousPromptEdited || this.continuousPrompt !== this.appliedContinuousPrompt ? { continuous_prompt: this.continuousPrompt } : {}),
      }
    },
    currentSessionOptions(): PromptOptions {
      return { prompt_mode: this.promptMode, progress_timeout_ms: this.progressTimeout, continuous_prompt: this.continuousPrompt }
    },
    restoreOptions(snapshot: SessionSnapshot) {
      const keepDraft = this.optionDraftSessionId === snapshot.id && ['READY', 'PAUSED'].includes(snapshot.state)
      const accept = <T>(option: keyof typeof this.pendingOptionEdits, local: T, remote: T): T => {
        // A delayed pause/attach acknowledgement can contain the old options.
        // Keep only explicit drafts for this session until each value is acknowledged.
        if (keepDraft && this.pendingOptionEdits[option] && local !== remote) return local
        this.pendingOptionEdits[option] = false
        return remote
      }
      this.silenceTimeout = accept('silence', this.silenceTimeout, snapshot.silence_timeout_ms)
      this.promptMode = accept('mode', this.promptMode, snapshot.prompt_mode ?? 'silence')
      this.progressTimeout = accept('timeout', this.progressTimeout, snapshot.progress_timeout_ms ?? 4000)
      this.continuousPrompt = accept('continuous', this.continuousPrompt, snapshot.continuous_prompt ?? false)
      if (!Object.values(this.pendingOptionEdits).some(Boolean)) this.optionDraftSessionId = ''
    },
    applyServerDefaults() {
      if (this.snapshot) return
      if (!this.silenceTimeoutEdited && this.silenceTimeout === this.appliedSilenceDefault) {
        this.silenceTimeout = runtimeDefaults.value.defaultSilenceTimeoutMs
        this.appliedSilenceDefault = this.silenceTimeout
      }
      if (!this.promptModeEdited && this.promptMode === this.appliedPromptMode) {
        this.promptMode = runtimeDefaults.value.defaultPromptMode
        this.appliedPromptMode = this.promptMode
      }
      if (!this.progressTimeoutEdited && this.progressTimeout === this.appliedProgressTimeout) {
        this.progressTimeout = runtimeDefaults.value.defaultProgressTimeoutMs
        this.appliedProgressTimeout = this.progressTimeout
      }
      if (!this.continuousPromptEdited && this.continuousPrompt === this.appliedContinuousPrompt) {
        this.continuousPrompt = runtimeDefaults.value.defaultContinuousPrompt
        this.appliedContinuousPrompt = this.continuousPrompt
      }
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
