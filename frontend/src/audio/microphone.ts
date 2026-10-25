export interface MicrophoneProcessingSettings {
  echoCancellation?: boolean
  noiseSuppression?: boolean
  autoGainControl?: boolean
  voiceIsolation?: boolean
  channelCount?: number
  sampleRate?: number
}

function readProcessingSettings(track: MediaStreamTrack): MicrophoneProcessingSettings | undefined {
  try {
    const settings = track.getSettings?.()
    if (!settings) return undefined
    const { echoCancellation, noiseSuppression, autoGainControl, channelCount, sampleRate } = settings
    const voiceIsolation = (settings as MediaTrackSettings & { voiceIsolation?: boolean }).voiceIsolation
    return { echoCancellation, noiseSuppression, autoGainControl, voiceIsolation, channelCount, sampleRate }
  } catch { return undefined }
}

function captureRate(settings?: MicrophoneProcessingSettings): number | undefined {
  const rate = settings?.sampleRate
  return typeof rate === 'number' && Number.isFinite(rate) && rate > 0 ? rate : undefined
}

function sampleRateFailure(error: unknown, stage: string): boolean {
  if (stage !== 'context' && stage !== 'source') return false
  return error instanceof Error && (error.name === 'NotSupportedError' || /sample[ -]?rate/i.test(error.message))
}

/** Input-device rate stays in the capture graph; the worklet emits 16 kHz PCM. */
export class Microphone {
  private stream?: MediaStream
  private context?: AudioContext
  private processor?: AudioWorkletNode
  private enabled = false
  private operation = 0
  private disposeCapture?: () => void
  private captureFailure?: (reason: string) => void
  private cancelOpening?: () => void
  private appliedProcessing?: MicrophoneProcessingSettings

  get isOpen() {
    return Boolean(this.stream?.active && this.context && this.context.state !== 'closed'
      && this.stream.getAudioTracks().some(track => track.readyState !== 'ended'))
  }
  get contextSampleRate() { return this.context?.sampleRate }
  get processingSettings(): Readonly<MicrophoneProcessingSettings> | undefined {
    return this.appliedProcessing ? { ...this.appliedProcessing } : undefined
  }

  private waitForStartup(pending: Promise<unknown>, message: string) {
    return new Promise<void>((resolve, reject) => {
      let settled = false
      const finish = (error?: unknown) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        if (this.cancelOpening === cancel) this.cancelOpening = undefined
        if (error) reject(error)
        else resolve()
      }
      const cancel = () => finish(new Error('音频准备已取消。'))
      const timer = setTimeout(() => finish(new Error(message)), 5000)
      this.cancelOpening = cancel
      pending.then(() => finish(), error => finish(error))
    })
  }

  async open(onFrame: (frame: ArrayBuffer) => void, onFailure: (reason: string) => void) {
    this.close()
    const operation = this.operation
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('此页面无法使用麦克风，请使用 HTTPS 或 localhost，并允许麦克风权限。')
    }
    let supported: MediaTrackSupportedConstraints = {}
    try { supported = navigator.mediaDevices.getSupportedConstraints?.() ?? {} } catch { /* Optional hints. */ }
    const audio: MediaTrackConstraints = {
      channelCount: { ideal: 1 }, echoCancellation: { ideal: true },
      noiseSuppression: { ideal: true }, autoGainControl: { ideal: true },
    }
    if ((supported as Record<string, boolean>).voiceIsolation) {
      ;(audio as Record<string, unknown>).voiceIsolation = { ideal: true }
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio })
    const tracks = stream.getAudioTracks()
    const stopTracks = () => {
      for (const track of stream.getTracks()) {
        track.onended = null
        track.onmute = null
        try { track.stop() } catch { /* Continue releasing the remaining tracks. */ }
      }
    }
    if (operation !== this.operation) { stopTracks(); return }
    const track = tracks[0]
    if (!track || track.readyState === 'ended') { stopTracks(); throw new Error('麦克风没有可用音轨，请检查输入设备后重试。') }
    for (const item of tracks) item.enabled = false
    let settings = readProcessingSettings(track)
    let context: AudioContext | undefined
    let source: MediaStreamAudioSourceNode | undefined
    let processor: AudioWorkletNode | undefined
    let sink: GainNode | undefined
    const disposeGraph = async () => {
      const previousContext = context
      if (previousContext) previousContext.onstatechange = null
      if (processor) { processor.port.onmessage = null; processor.onprocessorerror = null }
      for (const node of [source, processor, sink]) {
        try { node?.disconnect() } catch { /* One failed node cannot retain the rest. */ }
      }
      context = undefined; source = undefined; processor = undefined; sink = undefined
      if (previousContext && previousContext.state !== 'closed') {
        // Releasing an interrupted browser context must not leave startup waiting forever.
        await new Promise<void>(resolve => {
          const timer = setTimeout(resolve, 1000)
          try { void previousContext.close().then(() => { clearTimeout(timer); resolve() }, () => { clearTimeout(timer); resolve() }) }
          catch { clearTimeout(timer); resolve() }
        })
      }
    }
    const disposeCapture = () => { stopTracks(); void disposeGraph() }
    this.disposeCapture = disposeCapture
    const attemptedRates = new Set<number | undefined>()
    const rates: (number | undefined)[] = [captureRate(settings)]
    try {
      while (rates.length && attemptedRates.size < 3) {
        if (operation !== this.operation) return
        const rate = rates.shift()
        if (attemptedRates.has(rate)) continue
        attemptedRates.add(rate)
        let stage = 'context'
        try {
          // Device settings, when reported, are a better match than the output-device default.
          context = new AudioContext({ latencyHint: 'interactive', ...(rate === undefined ? {} : { sampleRate: rate }) })
          const audioContext = context
          stage = 'source'
          source = audioContext.createMediaStreamSource(stream)
          stage = 'worklet'
          await this.waitForStartup(audioContext.audioWorklet.addModule(`${import.meta.env.BASE_URL}pcm-capture.js`), '麦克风音频模块加载超时，请刷新页面后重试。')
          if (operation !== this.operation) return
          processor = new AudioWorkletNode(audioContext, 'pcm-capture', {
            numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1], channelCountMode: 'max',
          })
          sink = audioContext.createGain()
          sink.gain.value = 0
          source.connect(processor)
          processor.connect(sink)
          sink.connect(audioContext.destination)
          stage = 'resume'
          await this.waitForStartup(audioContext.resume(), '浏览器启动麦克风音频超时，请返回页面并重试。')
          if (operation !== this.operation) return
          if (audioContext.state !== 'running') throw new Error('浏览器未能启动音频，请返回页面并重试。')
          if (tracks.some(item => item.readyState === 'ended') || !stream.active) throw new Error('麦克风已断开，请检查设备后继续。')

          this.stream = stream
          this.context = audioContext
          this.processor = processor
          this.appliedProcessing = settings
          const fail = (reason: string) => {
            if (operation !== this.operation) return
            this.close()
            onFailure(reason)
          }
          this.captureFailure = fail
          processor.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
            if (operation === this.operation && this.enabled && audioContext.state === 'running') onFrame(event.data)
          }
          processor.onprocessorerror = () => fail('麦克风音频处理已中断，请重新开始或继续会话。')
          for (const item of tracks) {
            try { item.contentHint = 'speech' } catch { /* Unsupported hint. */ }
            item.onended = () => fail('麦克风已断开，请检查设备后继续。')
            item.onmute = () => { if (this.enabled) fail('麦克风输入已被系统中断，请检查设备后继续。') }
          }
          audioContext.onstatechange = () => {
            if (operation !== this.operation) return
            if (this.enabled && audioContext.state !== 'running') {
              fail('浏览器音频已被暂停或中断，请返回页面并继续会话。')
            } else if (audioContext.state === 'closed') this.close()
          }
          return
        } catch (error) {
          await disposeGraph()
          if (operation !== this.operation) return
          if (!sampleRateFailure(error, stage)) throw error
          // One bounded retry with the browser default, then a fresh reported input rate.
          settings = readProcessingSettings(track) ?? settings
          for (const candidate of [undefined, captureRate(settings)]) {
            if (!attemptedRates.has(candidate) && !rates.includes(candidate)) rates.push(candidate)
          }
          if (!rates.length || attemptedRates.size >= 3) {
            throw new Error('浏览器无法匹配麦克风采样率，请重新选择输入设备后重试。')
          }
        }
      }
    } catch (error) {
      if (this.disposeCapture === disposeCapture) this.disposeCapture = undefined
      stopTracks()
      await disposeGraph()
      throw error
    } finally {
      if (operation !== this.operation) disposeCapture()
    }
  }

  setEnabled(enabled: boolean) {
    if (enabled === this.enabled) return
    if (enabled && (!this.isOpen || this.context?.state !== 'running'
      || this.stream?.getAudioTracks().some(track => track.muted))) {
      this.captureFailure?.('麦克风或浏览器音频尚不可用，请检查设备后继续。')
      return
    }
    this.enabled = enabled
    this.processor?.port.postMessage({ enabled })
    for (const track of this.stream?.getAudioTracks() ?? []) track.enabled = enabled
  }

  async resume() {
    const context = this.context, operation = this.operation
    if (!context) return
    try {
      await this.waitForStartup(context.resume(), '浏览器恢复麦克风音频超时，请返回页面并重试。')
      if (operation !== this.operation) return
      if (context.state !== 'running') throw new Error('浏览器音频尚未恢复，请返回页面并重试。')
    } catch (error) {
      if (operation === this.operation) throw error
    }
  }

  close() {
    this.operation++
    this.enabled = false
    this.appliedProcessing = undefined
    const cancelOpening = this.cancelOpening
    this.cancelOpening = undefined
    cancelOpening?.()
    const disposeCapture = this.disposeCapture
    this.disposeCapture = undefined
    this.captureFailure = undefined
    try { this.processor?.port.postMessage({ enabled: false }) } catch { /* A failed processor may already be closed. */ }
    this.stream = undefined; this.context = undefined; this.processor = undefined
    disposeCapture?.()
  }
}
