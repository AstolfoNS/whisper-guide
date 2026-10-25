import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const apiProxy = {
  '/api': {
    target: process.env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000',
    changeOrigin: true,
    ws: true,
  },
}

export default defineConfig({
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
})
