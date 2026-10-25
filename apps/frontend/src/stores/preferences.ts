import { computed, ref, watch, type WatchStopHandle } from 'vue'
import { defineStore } from 'pinia'

export type ThemePreference = 'light' | 'dark' | 'system'
const THEME_KEY = 'whisper-guide-theme'

export const usePreferencesStore = defineStore('preferences', () => {
  const theme = ref<ThemePreference>('system')
  const systemDark = ref(false)
  const isDark = computed(() => theme.value === 'dark' || (theme.value === 'system' && systemDark.value))
  let media: MediaQueryList | undefined
  let stopThemeWatch: WatchStopHandle | undefined

  function updateSystemTheme(event: MediaQueryListEvent) { systemDark.value = event.matches }
  function setTheme(value: ThemePreference) {
    theme.value = value
    try { window.localStorage.setItem(THEME_KEY, value) } catch { /* Private browsing can disable storage. */ }
  }
  function initialize() {
    if (typeof window === 'undefined' || stopThemeWatch) return
    try {
      const saved = window.localStorage.getItem(THEME_KEY)
      if (saved === 'light' || saved === 'dark' || saved === 'system') theme.value = saved
    } catch { /* The system preference remains available without storage. */ }
    media = window.matchMedia?.('(prefers-color-scheme: dark)')
    systemDark.value = media?.matches ?? false
    media?.addEventListener('change', updateSystemTheme)
    stopThemeWatch = watch(isDark, (dark) => {
      document.documentElement.dataset.theme = dark ? 'dark' : 'light'
      document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
    }, { immediate: true })
  }
  function dispose() {
    media?.removeEventListener('change', updateSystemTheme)
    media = undefined
    stopThemeWatch?.()
    stopThemeWatch = undefined
  }
  return { theme, isDark, setTheme, initialize, dispose }
})
