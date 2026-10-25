import type { Page } from '@playwright/test'

/** Test-only acoustic input: finite 220 Hz tone followed by live PCM silence. */
export async function useSyntheticMicrophone(page: Page, voiceMs = 600) {
  await page.addInitScript((voiceFrames: number) => {
    let frameIndex = 0
    const original = WebSocket.prototype.send
    WebSocket.prototype.send = function (data) {
      if (typeof data === 'string') {
        const command = JSON.parse(data)
        if (command.type === 'audio_ready') frameIndex = 0
      }
      if (data instanceof ArrayBuffer) {
        const pcm = new Int16Array(data.byteLength / 2)
        if (frameIndex < voiceFrames) {
          for (let index = 0; index < pcm.length; index++) pcm[index] = Math.round(600 * Math.sin(2 * Math.PI * 220 * (frameIndex * pcm.length + index) / 16000))
        }
        frameIndex++
        return original.call(this, pcm.buffer)
      }
      return original.call(this, data)
    }
  }, Math.ceil(voiceMs / 20))
}
