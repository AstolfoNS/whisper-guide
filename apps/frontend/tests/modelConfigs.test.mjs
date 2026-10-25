import test from 'node:test'
import assert from 'node:assert/strict'
import {
  normalizeModelConfig, sanitizeModelConfigInput, updateModelConfig,
  duplicateModelConfig, activateModelConfig, deleteModelConfig,
  testModelConfig, discoverModels, ModelConfigError,
} from '../src/api/modelConfigs.ts'
import { modelConfigDraft, validateCloudSttDraft, validateModelConfigDraft } from '../src/components/models/modelConfigForm.ts'

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
  draft.settings.cloud_stt_access_token = 'test-voice-credential'
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


function cloudDraft() {
  const draft = modelConfigDraft()
  Object.assign(draft.settings, {
    stt_provider: 'cloud', cloud_opt_in: true, cloud_stt_protocol: 'volcengine-ws',
    cloud_stt_auth_mode: 'api-key', cloud_stt_endpoint: 'wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async',
    cloud_stt_access_token: 'voice-only-key',
  })
  return draft
}

test('native ASR API Key does not require App ID, while old authentication does', () => {
  const draft = cloudDraft()
  assert.equal(draft.name, '')
  assert.equal(validateCloudSttDraft(draft), '')
  draft.settings.cloud_stt_auth_mode = 'app-token'
  assert.match(validateCloudSttDraft(draft), /App ID/)
  draft.settings.cloud_stt_app_id = 'voice-app'
  assert.equal(validateCloudSttDraft(draft), '')
  draft.settings.cloud_stt_resource_id = ''
  assert.match(validateCloudSttDraft(draft), /Resource ID/)
})

test('ASR retains only its own saved credential and explicit clear blocks testing', () => {
  const saved = normalizeModelConfig({ id: 'voice', settings: { cloud_stt_token_configured: true } })
  const draft = cloudDraft()
  draft.settings.cloud_stt_access_token = ''
  draft.settings.llm_api_key = 'separate-text-model-key'
  assert.match(validateCloudSttDraft(draft), /语音服务 API Key/)
  assert.equal(validateCloudSttDraft(draft, saved), '')
  assert.equal('cloud_stt_access_token' in sanitizeModelConfigInput(draft).settings, false)
  draft.settings.clear_cloud_stt_access_token = true
  assert.match(validateCloudSttDraft(draft, saved), /语音服务 API Key/)
  draft.settings.clear_cloud_stt_access_token = false
  draft.settings.cloud_stt_access_token = 'sk-****'
  assert.match(validateCloudSttDraft(draft), /语音服务 API Key/)
})

test('native ASR validates encrypted protocol, consent and independent timeout', () => {
  const draft = cloudDraft()
  draft.settings.cloud_opt_in = false
  assert.match(validateCloudSttDraft(draft), /授权/)
  draft.settings.cloud_opt_in = true
  for (const url of ['https://voice.example.test', 'ws://voice.example.test', 'wss://user:password@voice.example.test', 'wss://voice.example.test/asr?token=private', 'wss://voice.example.test/asr#private']) {
    draft.settings.cloud_stt_endpoint = url
    assert.notEqual(validateCloudSttDraft(draft), '')
  }
  for (const url of ['wss://voice.example.test', 'ws://127.0.0.1:8010/stt', 'ws://[::1]:8010/stt']) {
    draft.settings.cloud_stt_endpoint = url
    assert.equal(validateCloudSttDraft(draft), '')
  }
  for (const timeout of [199, 30001, 500.5]) {
    draft.settings.cloud_stt_timeout_ms = timeout
    assert.match(validateCloudSttDraft(draft), /200 至 30000/)
  }
  for (const timeout of [200, 30000]) {
    draft.settings.cloud_stt_timeout_ms = timeout
    draft.settings.llm_timeout_ms = 1
    assert.equal(validateCloudSttDraft(draft), '')
  }
  draft.settings.cloud_stt_protocol = 'http-json'
  assert.match(validateCloudSttDraft(draft), /HTTP JSON/)
  draft.settings.cloud_stt_endpoint = 'https://gateway.example.test/stt'
  draft.settings.cloud_stt_resource_id = ''
  assert.equal(validateCloudSttDraft(draft), '')
})

test('unsaved native ASR tests send all protocol fields without saving or borrowing an LLM key', async () => {
  const original = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, init) => {
    calls.push({ url, data: JSON.parse(init.body) })
    return Response.json({ ok: true, target: 'stt', verified: 'network', message: '已连接', latency_ms: 20 })
  }
  try {
    const draft = cloudDraft()
    draft.settings.llm_api_key = 'text-only-key'
    draft.settings.cloud_stt_timeout_ms = 8000
    draft.settings.llm_timeout_ms = 1500
    draft.settings.cloud_stt_enable_nonstream = false
    await testModelConfig(draft, 'stt')
    assert.equal(calls.length, 1)
    assert.match(calls[0].url, /\/test$/)
    assert.equal(calls[0].data.id, undefined)
    assert.equal(calls[0].data.settings.cloud_stt_protocol, 'volcengine-ws')
    assert.equal(calls[0].data.settings.cloud_stt_auth_mode, 'api-key')
    assert.equal(calls[0].data.settings.cloud_stt_resource_id, 'volc.seedasr.sauc.duration')
    assert.equal(calls[0].data.settings.cloud_stt_timeout_ms, 8000)
    assert.equal(calls[0].data.settings.llm_timeout_ms, 1500)
    assert.equal(calls[0].data.settings.cloud_stt_enable_nonstream, false)
    assert.equal(calls[0].data.settings.cloud_stt_access_token, 'voice-only-key')
    assert.equal(calls[0].data.settings.llm_api_key, 'text-only-key')
  } finally { globalThis.fetch = original }
})
