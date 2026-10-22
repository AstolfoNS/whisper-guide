/** Microphone frames live in memory only and are never recorded or persisted. */
export class Microphone {
  private stream?: MediaStream
  private context?: AudioContext
  private source?: MediaStreamAudioSourceNode
  private processor?: AudioWorkletNode
  private sink?: GainNode
  private enabled = false

  get isOpen() { return Boolean(this.stream?.active && this.context) }

  async open(onFrame: (frame: ArrayBuffer) => void, onFailure: (reason: string) => void) {
    this.close()
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('此页面无法使用麦克风，请使用 HTTPS 或 localhost，并允许麦克风权限。')
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    })
    this.stream = stream
    try {
      const context = new AudioContext({ sampleRate: 16000, latencyHint: 'interactive' })
      this.context = context
      await context.audioWorklet.addModule(`${import.meta.env.BASE_URL}pcm-capture.js`)
      this.source = context.createMediaStreamSource(stream)
      this.processor = new AudioWorkletNode(context, 'pcm-capture')
      this.sink = context.createGain()
      this.sink.gain.value = 0
      this.source.connect(this.processor)
      this.processor.connect(this.sink)
      this.sink.connect(context.destination)
      this.processor.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
        if (this.enabled) onFrame(event.data)
      }
      for (const track of stream.getAudioTracks()) track.onended = () => onFailure('麦克风已断开，请检查设备后继续。')
      context.onstatechange = () => {
        if (this.enabled && context.state === 'suspended') onFailure('浏览器已暂停音频，请返回页面并继续会话。')
      }
      await context.resume()
    } catch (error) {
      this.close()
      throw error
    }
  }

  setEnabled(enabled: boolean) {
    this.enabled = enabled
    this.processor?.port.postMessage({ enabled })
    for (const track of this.stream?.getAudioTracks() ?? []) track.enabled = enabled
  }

  async resume() { await this.context?.resume() }

  close() {
    this.enabled = false
    this.processor?.port.postMessage({ enabled: false })
    this.source?.disconnect(); this.processor?.disconnect(); this.sink?.disconnect()
    if (this.context) { this.context.onstatechange = null; void this.context.close() }
    for (const track of this.stream?.getTracks() ?? []) { track.onended = null; track.stop() }
    this.stream = undefined; this.context = undefined; this.source = undefined; this.processor = undefined; this.sink = undefined
  }
}
