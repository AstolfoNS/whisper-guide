/* Actual context rate → 16 kHz, mono, little-endian PCM16; 20 ms; memory only. */
class PcmCapture extends AudioWorkletProcessor {
  enabled = false
  frame = new ArrayBuffer(640)
  view = new DataView(this.frame)
  offset = 0
  sampleWeight = 0
  sampleSum = 0

  constructor() {
    super()
    this.port.onmessage = ({ data }) => {
      const enabled = data.enabled === true
      if (enabled === this.enabled) return
      this.enabled = enabled
      this.offset = 0
      this.sampleWeight = 0
      this.sampleSum = 0
      this.frame = new ArrayBuffer(640)
      this.view = new DataView(this.frame)
    }
  }

  process(inputs) {
    const channels = inputs[0]
    if (!this.enabled || !channels?.length || !channels[0]?.length) return true
    const ratio = sampleRate / 16000
    for (let index = 0; index < channels[0].length; index++) {
      // Do not discard a microphone routed to the second channel of a USB interface.
      let sample = 0
      for (const channel of channels) sample += Number.isFinite(channel[index]) ? channel[index] : 0
      sample /= channels.length
      let remaining = 1
      while (remaining > 1e-8) {
        const weight = Math.min(remaining, ratio - this.sampleWeight)
        this.sampleSum += sample * weight
        this.sampleWeight += weight
        remaining -= weight
        if (this.sampleWeight >= ratio - 1e-8) {
          const value = Math.max(-1, Math.min(1, this.sampleSum / ratio))
          this.view.setInt16(this.offset, Math.round(value * (value < 0 ? 32768 : 32767)), true)
          this.offset += 2
          this.sampleSum = 0
          this.sampleWeight = 0
          if (this.offset === 640) {
            this.port.postMessage(this.frame, [this.frame])
            this.frame = new ArrayBuffer(640)
            this.view = new DataView(this.frame)
            this.offset = 0
          }
        }
      }
    }
    return true
  }
}
registerProcessor('pcm-capture', PcmCapture)
