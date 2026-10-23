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
