import { readonly, ref } from 'vue'

const DEFAULT_APP_TITLE = 'whisper-guide'
const title = ref(DEFAULT_APP_TITLE)
export const appTitle = readonly(title)
const defaults = ref({
  defaultSilenceTimeoutMs: 3000,
  scriptMaxFileBytes: 2097152,
  scriptMaxUploadFiles: 10,
})
export const runtimeDefaults = readonly(defaults)
let configuration: Promise<void> | undefined

function positiveInteger(value: unknown, minimum = 1): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum
}

/** Public UI defaults are loaded once per page; credentials are never requested. */
export function loadAppConfig(): Promise<void> {
  return configuration ??= (async () => {
    try {
      const base = (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
      const response = await fetch(`${base}/api/app-config`, { signal: AbortSignal.timeout(5000), cache: 'no-store' })
      if (!response.ok) return
      const result: unknown = await response.json()
      if (typeof result !== 'object' || result === null) return
      const config = result as Record<string, unknown>
      if (typeof config.app_title === 'string') title.value = config.app_title.trim() || DEFAULT_APP_TITLE
      defaults.value = {
        defaultSilenceTimeoutMs: positiveInteger(config.default_silence_timeout_ms, 1000) ? config.default_silence_timeout_ms : defaults.value.defaultSilenceTimeoutMs,
        scriptMaxFileBytes: positiveInteger(config.script_max_file_bytes) ? config.script_max_file_bytes : defaults.value.scriptMaxFileBytes,
        scriptMaxUploadFiles: positiveInteger(config.script_max_upload_files) ? config.script_max_upload_files : defaults.value.scriptMaxUploadFiles,
      }
    } catch { /* Old/offline servers retain compatible defaults. */ }
  })()
}

export function formatUploadLimit(bytes: number): string {
  return bytes % 1048576 === 0 ? `${bytes / 1048576} MiB`
    : bytes % 1024 === 0 ? `${bytes / 1024} KiB` : `${bytes} B`
}
