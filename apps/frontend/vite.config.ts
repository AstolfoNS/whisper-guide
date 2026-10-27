import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv, type UserConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const frontendDirectory = fileURLToPath(new URL('.', import.meta.url))

/** Config evaluation runs before Vite loads dotenv files for application code. */
export function createFrontendConfig(mode: string, envDir = frontendDirectory): UserConfig {
  const env = loadEnv(mode, envDir, 'VITE_')
  const apiProxy = {
    '/api': {
      target: env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
      changeOrigin: true,
      ws: true,
    },
  }
  return {
    plugins: [vue()],
    server: {
      port: 5173,
      strictPort: true,
      proxy: apiProxy,
    },
    preview: {
      port: 4173,
      strictPort: true,
      proxy: apiProxy,
    },
  }
}

export default defineConfig(({ mode }) => createFrontendConfig(mode))
