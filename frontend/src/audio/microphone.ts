export interface MicrophoneProcessingSettings {
  echoCancellation?: boolean
  noiseSuppression?: boolean
  autoGainControl?: boolean
  voiceIsolation?: boolean
  channelCount?: number
  sampleRate?: number
}

/** Microphone frames and applied processing settings live in memory only. */
export class Microphone {
  private stream?: MediaStream
  private context?: AudioContext
  private source?: MediaStreamAudioSourceNode
  private processor?: AudioWorkletNode
  private sink?: GainNode
  private enabled = false
  private operation = 0
  private appliedProcessing?: MicrophoneProcessingSettings

  get isOpen() { return Boolean(this.stream?.active && this.context) }
  get contextSampleRate() { return this.context?.sampleRate }
  get processingSettings(): Readonly<MicrophoneProcessingSettings> | undefined {
    return this.appliedProcessing ? { ...this.appliedProcessing } : undefined
  }

  async open(onFrame: (frame: ArrayBuffer) => void, onFailure: (reason: string) => void) {
    this.close()
    const operation = this.operation
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('此页面无法使用麦克风，请使用 HTTPS 或 localhost，并允许麦克风权限。')
    }
    const supported = navigator.mediaDevices.getSupportedConstraints?.() ?? {}
    const audio: MediaTrackConstraints = {
      channelCount: { ideal: 1 },
      echoCancellation: { ideal: true },
      noiseSuppression: { ideal: true },
      autoGainControl: { ideal: true },
    }
    if ((supported as Record<string, boolean>).voiceIsolation) {
      ;(audio as Record<string, unknown>).voiceIsolation = { ideal: true }
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio })
    if (operation !== this.operation) {
      for (const track of stream.getTracks()) track.stop()
      return
    }
    for (const track of stream.getAudioTracks()) track.enabled = false
    let context: AudioContext | undefined
    let source: MediaStreamAudioSourceNode | undefined
    let processor: AudioWorkletNode | undefined
    let sink: GainNode | undefined
    let appliedProcessing: MicrophoneProcessingSettings | undefined
    try {
      const audioContext = new AudioContext({ sampleRate: 16000, latencyHint: 'interactive' })
      context = audioContext
      await audioContext.audioWorklet.addModule(`${import.meta.env.BASE_URL}pcm-capture.js`)
      source = audioContext.createMediaStreamSource(stream)
      processor = new AudioWorkletNode(audioContext, 'pcm-capture')
      sink = audioContext.createGain()
      sink.gain.value = 0
      source.connect(processor)
      processor.connect(sink)
      sink.connect(audioContext.destination)
      processor.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (operation === this.operation && this.enabled) onFrame(event.data)
      }
      for (const track of stream.getAudioTracks()) {
        // Optional speech-processing hints must never prevent microphone capture.
        try { track.contentHint = 'speech' } catch { /* Unsupported by this browser. */ }
        track.onended = () => { if (operation === this.operation) onFailure('麦克风已断开，请检查设备后继续。') }
      }
      try {
        const settings = stream.getAudioTracks()[0]?.getSettings?.()
        if (settings) {
          const { echoCancellation, noiseSuppression, autoGainControl, channelCount, sampleRate } = settings
          const voiceIsolation = (settings as MediaTrackSettings & { voiceIsolation?: boolean }).voiceIsolation
          appliedProcessing = { echoCancellation, noiseSuppression, autoGainControl, voiceIsolation, channelCount, sampleRate }
        }
      } catch { /* Actual settings are optional diagnostics, never inferred from requests. */ }
      context.onstatechange = () => {
        if (operation === this.operation && this.enabled && audioContext.state === 'suspended') onFailure('浏览器已暂停音频，请返回页面并继续会话。')
      }
      await audioContext.resume()
      if (operation !== this.operation) {
        audioContext.onstatechange = null
        processor.disconnect()
        source.disconnect()
        sink.disconnect()
        await audioContext.close()
        for (const track of stream.getTracks()) track.stop()
        return
      }
      this.stream = stream
      this.context = context
      this.source = source
      this.processor = processor
      this.sink = sink
      this.appliedProcessing = appliedProcessing
    } catch (error) {
      if (context) context.onstatechange = null
      processor?.disconnect()
      source?.disconnect()
      sink?.disconnect()
      if (context) await context.close().catch(() => {})
      for (const track of stream.getTracks()) {
        track.onended = null
        track.stop()
      }
      throw error
    }
  }

  setEnabled(enabled: boolean) {
    if (enabled === this.enabled) return
    this.enabled = enabled
    this.processor?.port.postMessage({ enabled })
    for (const track of this.stream?.getAudioTracks() ?? []) track.enabled = enabled
  }

  async resume() { await this.context?.resume() }

  close() {
    this.operation++
    this.enabled = false
    this.appliedProcessing = undefined
    this.processor?.port.postMessage({ enabled: false })
    this.source?.disconnect(); this.processor?.disconnect(); this.sink?.disconnect()
    if (this.context) { this.context.onstatechange = null; void this.context.close() }
    for (const track of this.stream?.getTracks() ?? []) { track.onended = null; track.stop() }
    this.stream = undefined; this.context = undefined; this.source = undefined; this.processor = undefined; this.sink = undefined
  }
}
