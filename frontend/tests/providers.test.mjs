import test from 'node:test'
import assert from 'node:assert/strict'
import { normalizeProviderCatalog } from '../src/api/providers.ts'

test('provider catalog normalizes generic providers without exposing credentials', () => {
  const catalog = normalizeProviderCatalog({
    default_provider: 'openai-compatible',
    configuration_source: 'server',
    providers: [{
      id: 'openai-compatible',
      label: '兼容 OpenAI 协议的服务',
      kind: 'cloud',
      available: true,
      configured: true,
      privacy: '音频片段仅发送到后端配置的服务',
      model: 'speech-model',
      supports_streaming: true,
      requires_opt_in: true,
      access_token: 'must never be copied',
    }],
  })
  assert.equal(catalog.default_provider, 'openai-compatible')
  assert.equal(catalog.providers[0].id, 'openai-compatible')
  assert.equal(catalog.providers[0].model, 'speech-model')
  assert.equal('access_token' in catalog.providers[0], false)
})

test('provider catalog safely falls back for an older or invalid response', () => {
  const catalog = normalizeProviderCatalog({ providers: [{ id: 'local', available: true }] })
  assert.equal(catalog.default_provider, 'local')
  assert.equal(catalog.providers[0].label, 'local')
  assert.equal(catalog.providers[0].supports_streaming, true)

  const empty = normalizeProviderCatalog(null)
  assert.equal(empty.providers.length, 1)
  assert.equal(empty.providers[0].id, 'local')
  assert.equal(empty.providers[0].available, false)
})
