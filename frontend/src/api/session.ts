export type SessionState = 'READY' | 'LISTENING' | 'PROMPTING' | 'PAUSED' | 'TERMINATED'
export interface TransitionRecord {
  from_state: SessionState | null
  to_state: SessionState
  trigger: string
  timestamp: string | null
}
export interface ScriptUnit { text: string; start: number; end: number }
export interface PromptSegment { id: string; text: string; start: number; end: number; next_position: number }
export interface SpeechFallbackReason {
  error_code: string; stage?: string; http_status?: number; provider_error_code?: number; timeout_ms?: number
}
export interface SessionSnapshot {
  id: string; script_id: string; script_name: string; script_text?: string; stt_provider?: string; state: SessionState; current_state?: SessionState
  stt_provider_active?: string; stt_fallback?: boolean; stt_ready?: boolean; stt_fallback_reason?: SpeechFallbackReason | null
  recognition_filtered?: boolean; recognition_filter_reason?: string; recognition_filtered_count?: number
  alignment_status?: 'reliable' | 'pending' | 'mismatch'; raw_transcript_is_current?: boolean
  provisional_position?: number | null; provisional_transcript?: string; provisional_confidence?: number | null
  recovery_status?: 'idle' | 'searching' | 'ambiguous' | 'confirming'; recovery_attempts?: number
  model_config_id?: string; model_config_name?: string; model_config_revision?: number
  silence_timeout_ms: number; reading_position: number | null; prompt_position: number
  generation: number; prompt: PromptSegment | null; reliable: boolean; speaking: boolean
  previous_state?: SessionState | null; transition_trigger?: string | null; transition_at?: string | null
  transition_history?: TransitionRecord[]; allowed_transitions?: SessionState[]
  pause_reason: string | null; message: string | null; feedback_id: number
  last_transcript: string; raw_transcript: string; raw_transcript_is_final?: boolean; aligned_transcript: string; match_confidence: number | null; at_end: boolean; units: ScriptUnit[]
}
export type SessionServerEvent =
  | { type: 'snapshot'; session: SessionSnapshot }
  | { type: 'audio'; prompt_id: string; mime_type: string; data: string }
  | { type: 'error'; message: string }

function wsUrl() {
  const configured = (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
  const url = new URL(configured ? `${configured}/api/session` : '/api/session', location.href)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

export class SessionClient {
  private socket?: WebSocket
  private waiters = new Set<{ predicate: (session: SessionSnapshot) => boolean; resolve: (session: SessionSnapshot) => void; reject: (reason: Error) => void; timer: ReturnType<typeof setTimeout> }>()
  waitFor(predicate: (session: SessionSnapshot) => boolean, timeoutMs = 15000): Promise<SessionSnapshot> {
    return new Promise((resolve, reject) => {
      const waiter = { predicate, resolve, reject, timer: setTimeout(() => { this.waiters.delete(waiter); reject(new Error('等待服务状态超时，请重试。')) }, timeoutMs) }
      this.waiters.add(waiter)
    })
  }
  private dispatch(event: SessionServerEvent) {
    this.onEvent(event)
    for (const waiter of this.waiters) {
      if (event.type === 'error') {
        clearTimeout(waiter.timer); waiter.reject(new Error(event.message)); this.waiters.delete(waiter)
      } else if (event.type === 'snapshot' && waiter.predicate(event.session)) {
        clearTimeout(waiter.timer); waiter.resolve(event.session); this.waiters.delete(waiter)
      }
    }
  }
  private onEvent: (event: SessionServerEvent) => void
  private onClose: () => void
  constructor(onEvent: (event: SessionServerEvent) => void, onClose: () => void) { this.onEvent = onEvent; this.onClose = onClose }
  get connected() { return this.socket?.readyState === WebSocket.OPEN }
  async connect() {
    this.close(); const socket = new WebSocket(wsUrl()); this.socket = socket
    socket.onmessage = (event) => { if (this.socket !== socket) return; try { this.dispatch(JSON.parse(String(event.data)) as SessionServerEvent) } catch { this.dispatch({ type: 'error', message: '收到无法识别的服务端消息。' }) } }
    socket.onclose = () => { if (this.socket === socket) { this.socket = undefined; this.onClose() } }
    await new Promise<void>((resolve, reject) => {
      const timeout = window.setTimeout(() => { socket.close(); reject(new Error('实时连接超时。')) }, 5000)
      socket.onopen = () => { window.clearTimeout(timeout); resolve() }
      socket.addEventListener('close', () => { window.clearTimeout(timeout); reject(new Error('实时连接已关闭。')) }, { once: true })
      socket.addEventListener('error', () => { window.clearTimeout(timeout); reject(new Error('实时连接失败，请检查服务端和网络。')) }, { once: true })
    })
  }
  send(command: Record<string, unknown>) { if (!this.connected) throw new Error('实时连接已断开，请连接后继续。'); this.socket!.send(JSON.stringify(command)) }
  sendAudio(frame: ArrayBuffer) { if (!this.connected) return; if (this.socket!.bufferedAmount > 320000) { this.close(); this.onClose(); return }; this.socket!.send(frame) }
  prepare(scriptId: string, timeout: number, sttProvider?: string) { this.send({ type: 'prepare', script_id: scriptId, silence_timeout_ms: timeout, ...(sttProvider ? { stt_provider: sttProvider } : {}) }) }
  attach(sessionId: string) { this.send({ type: 'attach', session_id: sessionId }) }
  close() { for (const waiter of this.waiters) { clearTimeout(waiter.timer); waiter.reject(new Error('连接已关闭。')) }; this.waiters.clear(); const socket = this.socket; this.socket = undefined; if (socket) { socket.onmessage = null; socket.onclose = null; socket.close() } }
}
