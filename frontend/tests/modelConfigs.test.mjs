import test from 'node:test'
import assert from 'node:assert/strict'
import {
  normalizeModelConfig, sanitizeModelConfigInput, updateModelConfig,
  duplicateModelConfig, activateModelConfig, deleteModelConfig,
  testModelConfig, discoverModels, ModelConfigError,
} from '../src/api/modelConfigs.ts'
import { modelConfigDraft, validateModelConfigDraft } from '../src/components/models/modelConfigForm.ts'

function profile() {
  return normalizeModelConfig({ id: 'a', name: '演讲配置', revision: 3, is_active: true, active_revision: 2, pending_activation: true, settings: { stt_provider: 'local', llm_align_mode: 'fallback', llm_model: 'test', llm_base_url: 'https://example.test/v1', has_llm_api_key: true, llm_api_key: 'sk-****' } })
}

test('editor draft keeps revision and activation metadata separate and never fills a masked key', () => {
  const draft = modelConfigDraft(profile())
  assert.equal(draft.revision, 3)
  assert.equal(draft.settings.llm_api_key, '')
  assert.equal(draft.settings.clear_llm_api_key, false)
  assert.equal(profile().active_revision, 2)
  assert.equal(profile().pending_activation, true)
})

test('credential preserve, replacement and explicit clear are distinct payloads', () => {
  const draft = modelConfigDraft(profile())
  assert.equal('llm_api_key' in sanitizeModelConfigInput(draft).settings, false)
  draft.settings.llm_api_key = 'sk-replacement'
  assert.equal(sanitizeModelConfigInput(draft).settings.llm_api_key, 'sk-replacement')
  draft.settings.clear_llm_api_key = true
  const clear = sanitizeModelConfigInput(draft)
  assert.equal(clear.settings.clear_llm_api_key, true)
  assert.equal('llm_api_key' in clear.settings, false)
  draft.settings.clear_llm_api_key = false
  draft.settings.llm_api_key = 'sk-****'
  assert.equal('llm_api_key' in sanitizeModelConfigInput(draft).settings, false)
})

test('cloud drafts require explicit microphone authorization and model drafts require URL and model', () => {
  const draft = modelConfigDraft()
  assert.match(validateModelConfigDraft(draft), /配置名称/)
  draft.name = 'test'
  assert.equal(validateModelConfigDraft(draft), '')
  draft.settings.stt_provider = 'cloud'
  draft.settings.cloud_stt_endpoint = 'https://stt.example.test'
  assert.match(validateModelConfigDraft(draft), /授权/)
  draft.settings.cloud_opt_in = true
  draft.settings.llm_align_mode = 'hybrid'
  assert.match(validateModelConfigDraft(draft), /Base URL/)
  draft.settings.llm_base_url = 'http://localhost:11434/v1'
  assert.match(validateModelConfigDraft(draft), /模型名称/)
})

test('save, duplicate, test and activate use independent routes and never send secrets during copy', async () => {
  const calls = []
  const original = globalThis.fetch
  globalThis.fetch = async (url, init) => {
    calls.push({ url, ...init, data: init?.body ? JSON.parse(init.body) : undefined })
    if (init?.method === 'DELETE') return new Response(null, { status: 204 })
    if (String(url).endsWith('/test')) return Response.json({ ok: true, message: '本地能力已检查', target: 'stt', verified: 'capability', latency_ms: null })
    return Response.json(profile())
  }
  try {
    await updateModelConfig('a', modelConfigDraft(profile()))
    await duplicateModelConfig('a')
    const result = await testModelConfig(modelConfigDraft(profile()), 'stt', 'a')
    await activateModelConfig('a', 3)
    await deleteModelConfig('b')
    assert.equal(calls[0].method, 'PUT')
    assert.equal(calls[0].data.revision, 3)
    assert.equal('llm_api_key' in calls[0].data.settings, false)
    assert.deepEqual(calls[1].data, {})
    assert.match(calls[1].url, /\/a\/duplicate$/)
    assert.equal(calls[2].data.target, 'stt')
    assert.equal(calls[2].data.id, 'a')
    assert.equal(result.verified, 'capability')
    assert.match(calls[3].url, /\/a\/activate$/)
    assert.deepEqual(calls[3].data, { revision: 3 })
    assert.equal(calls[4].method, 'DELETE')
  } finally { globalThis.fetch = original }
})

test('revision and session conflicts retain structured server reasons', async () => {
  const original = globalThis.fetch
  globalThis.fetch = async () => Response.json({ detail: { code: 'SESSION_ACTIVE', message: '请先结束当前会话' } }, { status: 409 })
  try {
    await assert.rejects(() => activateModelConfig('a', 3), (error) => error instanceof ModelConfigError && error.status === 409 && error.code === 'SESSION_ACTIVE' && /结束/.test(error.message))
  } finally { globalThis.fetch = original }
})

test('model discovery uses the draft without requiring a model, preserving saved credentials and supporting cancellation', async () => {
  const original = globalThis.fetch
  const calls = []
  const signal = new AbortController().signal
  globalThis.fetch = async (url, init) => {
    calls.push({ url, ...init, data: JSON.parse(init.body) })
    return Response.json({ ok: true, models: [{ id: 'available-model' }], latency_ms: 3, message: '已获取模型列表', truncated: false })
  }
  try {
    const draft = modelConfigDraft(profile())
    draft.settings.llm_align_mode = 'off'
    draft.settings.llm_model = ''
    const result = await discoverModels(draft, 'a', signal)
    assert.match(calls[0].url, /\/discover-models$/)
    assert.equal(calls[0].method, 'POST')
    assert.equal(calls[0].signal, signal)
    assert.equal(calls[0].data.id, 'a')
    assert.equal(calls[0].data.settings.llm_model, '')
    assert.equal('llm_api_key' in calls[0].data.settings, false)
    assert.deepEqual(result.models, [{ id: 'available-model' }])
    draft.settings.llm_api_key = 'draft-only-key'
    await discoverModels(draft)
    assert.equal(calls[1].data.settings.llm_api_key, 'draft-only-key')
    assert.equal(calls[1].data.id, undefined)
    draft.settings.clear_llm_api_key = true
    await discoverModels(draft, 'a')
    assert.equal(calls[2].data.settings.clear_llm_api_key, true)
    assert.equal('llm_api_key' in calls[2].data.settings, false)
  } finally { globalThis.fetch = original }
})
