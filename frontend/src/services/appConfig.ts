import { readonly, ref } from 'vue'

const DEFAULT_APP_TITLE = 'whisper-guide'
const title = ref(DEFAULT_APP_TITLE)
export const appTitle = readonly(title)
let configuration: Promise<void> | undefined

/** Public runtime branding is loaded once per page; it never blocks the app. */
export function loadAppConfig(): Promise<void> {
  return configuration ??= (async () => {
    try {
      const base = (import.meta.env?.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
      const response = await fetch(`${base}/api/app-config`, { signal: AbortSignal.timeout(5000), cache: 'no-store' })
      if (!response.ok) return
      const result: unknown = await response.json()
      if (typeof result === 'object' && result !== null && 'app_title' in result
        && typeof result.app_title === 'string') title.value = result.app_title.trim() || DEFAULT_APP_TITLE
    } catch { title.value = DEFAULT_APP_TITLE }
  })()
}
