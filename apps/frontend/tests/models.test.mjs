import test from 'node:test'
import assert from 'node:assert/strict'
import { defaultLlmPresets, normalizeModelSettings } from '../src/api/models.ts'

test('model settings normalize nested response and preserve only secret metadata', () => {
  const settings = normalizeModelSettings({
    stt: { provider: 'cloud', endpoint: 'https://stt.example', app_id: 'app', has_access_token: true },
    llm: { align_mode: 'fallback', provider_preset: 'custom', base_url: 'https://llm.example/v1', model: 'model-a', api_key: 'sk-****1234', has_api_key: true, timeout_ms: 900 },
    presets: [{ id: 'custom', label: '自定义', base_url: '', model: '' }],
  })
  assert.equal(settings.stt_provider, 'cloud')
  assert.equal(settings.cloud_stt_token_configured, true)
  assert.equal(settings.llm_align_mode, 'fallback')
  assert.equal(settings.llm_model, 'model-a')
  assert.equal(settings.llm_api_key_hint, 'sk-****1234')
  assert.equal('llm_api_key' in settings, false)
})

test('model presets include generic providers and custom fallback', () => {
  const presets = defaultLlmPresets()
  assert.deepEqual(presets.map((preset) => preset.id), ['custom', 'deepseek', 'qwen', 'doubao', 'openai', 'ollama'])
  assert.equal(presets.find((preset) => preset.id === 'custom')?.base_url, '')
})


test('cloud ASR protocol options normalize flat, nested and older settings with independent timeout', () => {
  const defaults = normalizeModelSettings({})
  assert.equal(defaults.cloud_stt_protocol, 'http-json')
  assert.equal(defaults.cloud_stt_auth_mode, 'api-key')
  assert.equal(defaults.cloud_stt_resource_id, 'volc.seedasr.sauc.duration')
  assert.equal(defaults.cloud_stt_timeout_ms, 5000)
  assert.equal(defaults.cloud_stt_enable_nonstream, true)
  for (const payload of [
    { cloud_stt_protocol: 'volcengine-ws', cloud_stt_auth_mode: 'app-token', cloud_stt_resource_id: 'custom-resource', cloud_stt_timeout_ms: 7200, cloud_stt_enable_nonstream: false, llm_timeout_ms: 1500 },
    { stt: { protocol: 'volcengine-ws', auth_mode: 'app-token', resource_id: 'custom-resource', timeout_ms: 7200, enable_nonstream: false }, llm: { timeout_ms: 1500 } },
  ]) {
    const settings = normalizeModelSettings(payload)
    assert.equal(settings.cloud_stt_protocol, 'volcengine-ws')
    assert.equal(settings.cloud_stt_auth_mode, 'app-token')
    assert.equal(settings.cloud_stt_resource_id, 'custom-resource')
    assert.equal(settings.cloud_stt_timeout_ms, 7200)
    assert.equal(settings.cloud_stt_enable_nonstream, false)
    assert.equal(settings.llm_timeout_ms, 1500)
  }
})
