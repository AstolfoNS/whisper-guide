/** Browser playback with a generation token; cancelled decoding cannot play later. */
export class PromptPlayer {
  private context?: AudioContext
  private source?: AudioBufferSourceNode
  private generation = 0
  async unlock() {
    this.context ??= new AudioContext()
    await this.context.resume()
  }
  stop() {
    this.generation++
    if (this.source) {
      this.source.onended = null
      this.source.stop()
      this.source.disconnect()
      this.source = undefined
    }
  }
  async play(encoded: string, ended: () => void, failed: () => void) {
    this.stop()
    const generation = this.generation
    try {
      await this.unlock()
      const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0))
      const buffer = await this.context!.decodeAudioData(bytes.buffer)
      if (generation !== this.generation) return
      const source = this.context!.createBufferSource()
      source.buffer = buffer
      source.connect(this.context!.destination)
      source.onended = () => {
        if (generation !== this.generation) return
        this.source = undefined
        source.disconnect()
        ended()
      }
      this.source = source
      source.start()
    } catch { if (generation === this.generation) failed() }
  }
  close() { this.stop(); void this.context?.close(); this.context = undefined }
}
