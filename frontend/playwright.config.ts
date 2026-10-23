import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './tests/e2e', workers: 1, timeout: 45000,
  use: { baseURL: 'http://127.0.0.1:5174', viewport: { width: 1440, height: 1000 }, permissions: ['microphone'], launchOptions: { args: ['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'] }, trace: 'retain-on-failure' },
  webServer: [
    { command: '../backend-core/.venv/bin/uvicorn browser_app:app --app-dir ../backend-core/tests --host 127.0.0.1 --port 8010', env: { PYTHONPATH: '../backend-core' }, url: 'http://127.0.0.1:8010/api/health', reuseExistingServer: false },
    { command: 'pnpm exec vite --host 127.0.0.1 --port 5174', env: { VITE_API_PROXY_TARGET: 'http://127.0.0.1:8010' }, url: 'http://127.0.0.1:5174', reuseExistingServer: false },
  ],
})
