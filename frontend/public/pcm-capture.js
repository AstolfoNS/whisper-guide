/* 16 kHz, mono, little-endian PCM16; 20 ms frames; no persistent audio storage. */
class PcmCapture extends AudioWorkletProcessor {
  enabled = false; frame = new ArrayBuffer(640); view = new DataView(this.frame); offset = 0; sampleWeight = 0; sampleSum = 0
  constructor() { super(); this.port.onmessage = ({ data }) => { this.enabled = data.enabled === true; this.offset = 0; this.sampleWeight = 0; this.sampleSum = 0; this.frame = new ArrayBuffer(640); this.view = new DataView(this.frame) } }
  process(inputs) {
    const channel = inputs[0]?.[0]; if (!this.enabled || !channel) return true
    const ratio = sampleRate / 16000
    for (const sample of channel) { let remaining = 1; while (remaining > 1e-8) { const weight = Math.min(remaining, ratio - this.sampleWeight); this.sampleSum += sample * weight; this.sampleWeight += weight; remaining -= weight; if (this.sampleWeight >= ratio - 1e-8) { const value = Math.max(-1, Math.min(1, this.sampleSum / ratio)); this.view.setInt16(this.offset, Math.round(value * (value < 0 ? 32768 : 32767)), true); this.offset += 2; this.sampleSum = 0; this.sampleWeight = 0; if (this.offset === 640) { this.port.postMessage(this.frame, [this.frame]); this.frame = new ArrayBuffer(640); this.view = new DataView(this.frame); this.offset = 0 } } } }
    return true
  }
}
registerProcessor('pcm-capture', PcmCapture)
