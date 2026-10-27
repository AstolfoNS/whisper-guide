// @vitest-environment node
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { createFrontendConfig } from '../../vite.config'

let directory: string
beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'whisper-guide-vite-env-'))
  vi.stubEnv('VITE_API_PROXY_TARGET', undefined)
})
afterEach(() => { vi.unstubAllEnvs(); rmSync(directory, { recursive: true, force: true }) })

function expectProxy(target: string, mode = 'development') {
  const config = createFrontendConfig(mode, directory)
  expect(config.server?.proxy?.['/api']).toMatchObject({ target, changeOrigin: true, ws: true })
  expect(config.preview?.proxy?.['/api']).toMatchObject({ target, changeOrigin: true, ws: true })
}

it('uses the default backend when dotenv has no proxy target', () => {
  expectProxy('http://127.0.0.1:8000')
})

it('reads the frontend dotenv target for both development and preview proxies', () => {
  writeFileSync(join(directory, '.env'), 'VITE_API_PROXY_TARGET=http://127.0.0.1:8123\n')
  expectProxy('http://127.0.0.1:8123')
})

it('selects mode-specific dotenv values ahead of shared local values', () => {
  writeFileSync(join(directory, '.env'), 'VITE_API_PROXY_TARGET=http://127.0.0.1:8123\n')
  writeFileSync(join(directory, '.env.local'), 'VITE_API_PROXY_TARGET=http://127.0.0.1:8124\n')
  writeFileSync(join(directory, '.env.production'), 'VITE_API_PROXY_TARGET=http://127.0.0.1:8125\n')
  expectProxy('http://127.0.0.1:8125', 'production')
  expectProxy('http://127.0.0.1:8124', 'development')
})

it('keeps exported process values ahead of all dotenv files', () => {
  writeFileSync(join(directory, '.env.development.local'), 'VITE_API_PROXY_TARGET=http://127.0.0.1:8123\n')
  vi.stubEnv('VITE_API_PROXY_TARGET', 'http://127.0.0.1:8999')
  expectProxy('http://127.0.0.1:8999')
})

it('uses the default when the configured target is empty', () => {
  writeFileSync(join(directory, '.env'), 'VITE_API_PROXY_TARGET=\n')
  expectProxy('http://127.0.0.1:8000')
})
