import { SessionClient, type SessionServerEvent, type SessionSnapshot } from '../api/session'
import { Microphone } from '../audio/microphone'
import { PlaybackCancelled, PromptPlayer } from '../audio/player'
import { useSessionStore } from '../stores/session'
import { useHealthStore } from '../stores/health'

type Store = ReturnType<typeof useSessionStore>
type Client = Pick<SessionClient, 'connected' | 'connect' | 'close' | 'send' | 'sendAudio' | 'prepare' | 'attach' | 'waitFor'>
type Mic = Pick<Microphone, 'open' | 'close' | 'resume' | 'setEnabled' | 'isOpen'>
  & Partial<Pick<Microphone, 'contextSampleRate' | 'processingSettings'>>
type Player = Pick<PromptPlayer, 'unlock' | 'stop' | 'close' | 'play'>
export interface SessionDependencies {
  microphone?: Mic; player?: Player
  clientFactory?: (event: (event: SessionServerEvent) => void, close: () => void) => Client
}

/** Application-scoped resource owner. Views never own microphone/socket lifetimes. */
export class SessionController {
  private client: Client
  private microphone: Mic
  private player: Player
  private operation = 0
  private captureGeneration = -1
  private captureSessionId = ''
  private openingMicrophone?: Promise<void>
  private captureAttempt = 0
  private minimumGeneration = -1
  private pendingState: SessionSnapshot['state'] | undefined
  private expectedPrompt = ''
  private lastFeedback = -1
  private destroyed = false
  private inStudio = false

  constructor(private store: Store, dependencies: SessionDependencies = {}) {
    try { this.store.recoverySessionId = sessionStorage.getItem('whisper-guide:session-id') ?? '' } catch { /* Optional metadata only. */ }
    this.microphone = dependencies.microphone ?? new Microphone()
    this.player = dependencies.player ?? new PromptPlayer()
    this.client = dependencies.clientFactory?.(this.onEvent, this.disconnected)
      ?? new SessionClient(this.onEvent, this.disconnected)
  }
  enterStudio() { this.inStudio = true }
  private ensure(operation: number) {
    if (operation !== this.operation || this.destroyed || !this.inStudio) throw new Error('操作已取消。')
  }
  private stopCapture() {
    const microphoneFailed = this.store.microphone === 'error'
    this.captureAttempt++
    this.captureGeneration = -1
    this.captureSessionId = ''
    this.microphone.close()
    this.openingMicrophone = undefined
    this.store.microphone = microphoneFailed ? 'error' : 'idle'
    const health = useHealthStore()
    health.captureInputRate = 0; health.captureContextRate = 0; health.captureChannels = 0
  }
  private stopPrompt() {
    this.expectedPrompt = ''; this.player.stop()
    if (useHealthStore().playback === 'pending') useHealthStore().playback = 'stopped'
  }
  private async preparePlayback(operation: number) {
    const health = useHealthStore()
    health.playback = 'unknown'; health.playbackDetail = ''
    try { await this.player.unlock() }
    catch (error) {
      if (operation !== this.operation || this.destroyed || !this.inStudio || health.playback !== 'unknown') return
      // A normal stop may cancel an in-flight unlock without indicating an output fault.
      if (error instanceof PlaybackCancelled) return
      health.playback = 'failed'
      health.playbackDetail = error instanceof Error ? error.message : '无法启用浏览器播放，请检查系统输出设备后点击继续。'
    }
  }
  private validTimeout() {
    if (!Number.isInteger(this.store.silenceTimeout) || this.store.silenceTimeout < 1000) throw new Error('静音等待时间必须为不小于 1000 毫秒的整数。')
  }
  private disconnected = () => {
    this.stopCapture(); this.stopPrompt()
    this.store.connection = 'disconnected'
    if (this.store.active) {
      this.operation++
      this.store.localPaused = true
      this.store.error = '连接已中断，保留进度；返回工作台后点击继续重连。'
    }
  }
  private onEvent = (event: SessionServerEvent) => {
    if (this.destroyed) return
    if (event.type === 'error') { this.store.error = event.message; return }
    if (event.type === 'audio_diagnostics') {
      const snapshot = this.store.snapshot
      if (snapshot?.id === event.session_id && snapshot.generation === event.generation
        && snapshot.state === 'LISTENING' && !this.store.localPaused) {
        Object.assign(snapshot, event.diagnostics)
      }
      return
    }
    if (event.type === 'snapshot') {
      const next = event.session
      const current = this.store.snapshot
      if (current && current.id !== next.id && current.state !== 'TERMINATED') return
      if (current?.id === next.id && next.generation < current.generation) return
      if (next.generation < this.minimumGeneration) return
      if (this.pendingState && next.generation === this.minimumGeneration && next.state !== this.pendingState) return
      this.store.snapshot = next
      this.store.recoverySessionId = next.state === 'TERMINATED' ? '' : next.id
      try {
        if (next.state === 'TERMINATED') sessionStorage.removeItem('whisper-guide:session-id')
        else sessionStorage.setItem('whisper-guide:session-id', next.id)
      } catch { /* Recording and transcripts are never stored. */ }
      if (this.pendingState && next.state === this.pendingState && next.generation >= this.minimumGeneration) this.pendingState = undefined
      this.store.localPaused = this.pendingState === 'PAUSED'
      this.store.silenceTimeout = next.silence_timeout_ms
      if (next.feedback_id !== this.lastFeedback) {
        this.lastFeedback = next.feedback_id
        this.store.notice = next.transition_trigger === 'user_paused' ? '' : next.message ?? ''
      }
      if (next.state === 'LISTENING') {
        this.stopPrompt()
        if (!this.inStudio) this.pause()
        else if (next.stt_ready === false) {
          this.captureAttempt++
          this.captureGeneration = -1
          this.captureSessionId = ''
          this.microphone.setEnabled(false)
          this.store.microphone = 'idle'
        } else void this.enableCapture(next)
      } else {
        this.microphone.setEnabled(false)
        this.captureGeneration = -1
        if (next.state !== 'PROMPTING') {
          this.stopPrompt()
          if (next.state === 'PAUSED' || next.state === 'TERMINATED') this.stopCapture()
        } else if (next.prompt) this.expectedPrompt = next.prompt.id
      }
      return
    }
    if (event.type === 'audio' && this.inStudio && this.store.state === 'PROMPTING' && event.prompt_id === this.expectedPrompt) {
      const id = event.prompt_id
      useHealthStore().playback = 'pending'
      useHealthStore().playbackDetail = ''
      void this.player.play(event.data, () => {
        if (id === this.expectedPrompt && this.client.connected) {
          this.expectedPrompt = ''
          useHealthStore().playback = 'success'
          useHealthStore().playbackDetail = ''
          this.client.send({ type: 'playback_finished', prompt_id: id })
        }
      }, (reason?: string) => {
        if (id === this.expectedPrompt && this.client.connected) {
          this.expectedPrompt = ''
          useHealthStore().playback = 'failed'
          useHealthStore().playbackDetail = reason || '浏览器播放失败，请检查输出设备、音量和页面音频权限后继续。'
          this.client.send({ type: 'playback_failed', prompt_id: id })
        }
      })
    }
  }
  private async connect() {
    this.store.connection = 'connecting'
    try { await this.client.connect(); this.store.connection = 'connected' }
    catch (error) { this.store.connection = 'disconnected'; throw error }
  }
  private sendFrame = (frame: ArrayBuffer) => {
    const snapshot = this.store.snapshot
    if (!this.destroyed && this.inStudio && this.store.state === 'LISTENING'
      && this.store.microphone === 'active' && snapshot?.stt_ready !== false
      && snapshot?.id === this.captureSessionId && snapshot.generation === this.captureGeneration) {
      this.client.sendAudio(frame)
    }
  }
  private async openMicrophone() {
    if (this.microphone.isOpen) return
    const opening = this.openingMicrophone ??= this.microphone.open(this.sendFrame, reason => {
      this.pause(reason)
      this.store.microphone = 'error'
    })
    try {
      await opening
      if (this.microphone.isOpen) {
        const health = useHealthStore()
        health.captureInputRate = this.microphone.processingSettings?.sampleRate ?? 0
        health.captureContextRate = this.microphone.contextSampleRate ?? 0
        health.captureChannels = this.microphone.processingSettings?.channelCount ?? 0
      }
    }
    finally { if (this.openingMicrophone === opening) this.openingMicrophone = undefined }
  }
  private async enableCapture(next: SessionSnapshot) {
    if (next.stt_ready === false || (this.captureGeneration === next.generation && this.captureSessionId === next.id)
      || !this.inStudio || this.destroyed) return
    this.microphone.setEnabled(false)
    this.store.microphone = 'idle'
    this.captureGeneration = next.generation
    this.captureSessionId = next.id
    const attempt = ++this.captureAttempt
    const current = () => attempt === this.captureAttempt && !this.destroyed && this.inStudio
      && this.store.state === 'LISTENING' && this.store.snapshot?.id === next.id
      && this.store.snapshot.generation === next.generation && this.store.snapshot.stt_ready !== false
    try {
      await this.openMicrophone()
      if (!current()) return
      await this.microphone.resume()
      if (!current()) return
      this.microphone.setEnabled(true)
      if (!current()) return
      if (!this.microphone.isOpen) throw new Error('麦克风已中断，请检查输入设备后继续。')
      this.store.microphone = 'active'
      this.client.send({ type: 'audio_ready' })
    } catch (error) {
      if (current()) {
        this.pause(error instanceof Error ? error.message : '麦克风无法启用。')
        this.store.microphone = 'error'
      }
    }
  }
  async start() {
    if (this.store.busy || !this.store.selectedScript || !['READY', 'TERMINATED'].includes(this.store.state)) return
    const operation = ++this.operation
    this.store.busy = true; this.store.error = ''
    try {
      this.validTimeout()
      // Start output unlock inside the user gesture; recognition can still run if output fails.
      void this.preparePlayback(operation)
      this.store.microphone = 'requesting'
      try { await this.openMicrophone() }
      catch (error) { this.store.microphone = 'error'; throw error }
      this.ensure(operation)
      if (!this.client.connected) await this.connect()
      this.ensure(operation)
      if (!this.store.snapshot || this.store.snapshot.state === 'TERMINATED') {
        this.minimumGeneration = -1; this.pendingState = undefined
        const ready = this.client.waitFor(s => s.state === 'READY')
        this.client.prepare(this.store.selectedScript.id, this.store.silenceTimeout)
        await ready
      } else this.client.send({ type: 'configure', silence_timeout_ms: this.store.silenceTimeout })
      this.ensure(operation)
      const listening = this.client.waitFor(s => s.state === 'LISTENING')
      this.client.send({ type: 'start' })
      await listening
    } catch (error) {
      const microphoneFailed = this.store.microphone === 'error'
      this.stopCapture()
      if (microphoneFailed) this.store.microphone = 'error'
      if (operation === this.operation && !this.destroyed) this.store.error = error instanceof Error ? error.message : '无法开始会话。'
    } finally { this.store.busy = false }
  }
  pause(reason = '') {
    if (!['LISTENING', 'PROMPTING'].includes(this.store.state)) return
    this.operation++
    if (this.store.snapshot) {
      this.minimumGeneration = Math.max(this.minimumGeneration, this.store.snapshot.generation + 1)
      this.pendingState = 'PAUSED'
    }
    this.stopCapture(); this.stopPrompt()
    this.store.localPaused = true
    if (reason) this.store.error = reason
    if (this.client.connected) this.client.send({ type: 'pause' })
  }
  async resume() {
    if (this.store.state !== 'PAUSED' || !this.store.snapshot || this.store.busy) return
    const operation = ++this.operation
    this.store.busy = true; this.store.error = ''
    try {
      this.validTimeout()
      // Start output unlock inside the user gesture; recognition can still run if output fails.
      void this.preparePlayback(operation)
      this.store.microphone = 'requesting'
      try { await this.openMicrophone() }
      catch (error) { this.store.microphone = 'error'; throw error }
      this.ensure(operation)
      if (!this.client.connected) {
        await this.connect(); this.ensure(operation)
        const attached = this.client.waitFor(s => s.id === this.store.snapshot?.id)
        this.client.attach(this.store.snapshot.id)
        await attached
      }
      this.ensure(operation)
      this.client.send({ type: 'configure', silence_timeout_ms: this.store.silenceTimeout })
      const listening = this.client.waitFor(s => s.state === 'LISTENING')
      this.client.send({ type: 'resume' })
      await listening
    } catch (error) {
      const microphoneFailed = this.store.microphone === 'error'
      this.stopCapture()
      if (microphoneFailed) this.store.microphone = 'error'
      if (operation === this.operation && !this.destroyed) this.store.error = error instanceof Error ? error.message : '无法继续会话。'
    } finally { this.store.busy = false }
  }
  interrupt() {
    if (this.store.state !== 'PROMPTING') return
    this.operation++
    if (this.store.snapshot) {
      this.minimumGeneration = Math.max(this.minimumGeneration, this.store.snapshot.generation + 1)
      this.pendingState = 'LISTENING'
    }
    this.stopPrompt()
    if (this.client.connected) this.client.send({ type: 'interrupt' })
  }
  terminate() {
    this.operation++
    this.stopCapture(); this.stopPrompt()
    if (this.store.snapshot) {
      this.minimumGeneration = Math.max(this.minimumGeneration, this.store.snapshot.generation + 1)
      this.pendingState = 'TERMINATED'
      if (this.client.connected) this.client.send({ type: 'terminate' })
      else this.store.error = '请先重连确认结束会话。'
    }
  }
  configure() {
    try {
      this.validTimeout()
      if (this.store.active && this.client.connected) this.client.send({ type: 'configure', silence_timeout_ms: this.store.silenceTimeout })
    } catch (error) { this.store.error = (error as Error).message }
  }
  manualPrompt() {
    if (this.store.state === 'LISTENING' && this.client.connected) this.client.send({ type: 'manual_prompt' })
  }
  reposition(unitIndex: number) {
    if (this.client.connected) this.client.send({ type: 'reposition', unit_index: unitIndex })
  }
  async leaveStudio() {
    this.inStudio = false
    this.operation++
    this.stopCapture(); this.stopPrompt()
    if (['LISTENING', 'PROMPTING'].includes(this.store.state) && this.client.connected) {
      const paused = this.client.waitFor(s => s.state === 'PAUSED', 3000)
      this.pause()
      try { await paused }
      catch { this.client.close(); this.disconnected() }
    }
  }
  async reconnect(sessionId?: string) {
    if (this.destroyed || this.store.busy) return
    if (sessionId && this.store.active && sessionId !== this.store.snapshot?.id) { this.store.error = '请先结束当前会话，再恢复其他会话。'; return }
    if (sessionId) this.store.recoverySessionId = sessionId
    if (['LISTENING', 'PROMPTING'].includes(this.store.state)) return
    this.store.busy = true
    try {
      if (!this.client.connected) await this.connect()
      const id = this.store.snapshot?.state !== 'TERMINATED' ? this.store.snapshot?.id || this.store.recoverySessionId : this.store.recoverySessionId
      if (id) {
        if (id !== this.store.snapshot?.id) {
          this.minimumGeneration = -1; this.pendingState = undefined; this.captureGeneration = -1; this.lastFeedback = -1
        }
        const attached = this.client.waitFor(s => s.id === id)
        this.client.attach(id)
        await attached
        this.store.localPaused = false
      }
      this.store.error = ''
    } catch (error) { this.store.error = (error as Error).message }
    finally { this.store.busy = false }
  }
  dispose() { this.destroyed = true; this.operation++; this.stopCapture(); this.stopPrompt(); this.client.close(); this.player.close() }
}
let controller: SessionController | undefined
export function getSessionController() { return controller ??= new SessionController(useSessionStore()) }
export function disposeSessionController() { controller?.dispose(); controller = undefined }

if (import.meta.hot) import.meta.hot.dispose(disposeSessionController)
