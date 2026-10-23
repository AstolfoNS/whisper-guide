import test from 'node:test'
import assert from 'node:assert/strict'
import { createServer } from 'vite'
import { createSSRApp } from 'vue'
import { renderToString } from '@vue/server-renderer'

let vite
async function loadComponent(file) {
  vite ??= await createServer({ server: { middlewareMode: true }, appType: 'custom' })
  return (await vite.ssrLoadModule(`/src/components/${file}`)).default
}

test.after(async () => { await vite?.close() })

test('state monitor renders five nodes and highlights the actual latest transition', async () => {
  const Monitor = await loadComponent('StateMachineMonitor.vue')
  const session = {
    state: 'PROMPTING', previous_state: 'LISTENING', transition_trigger: 'silence_threshold_reached',
    transition_at: '2026-10-23T08:30:00Z', allowed_transitions: ['LISTENING', 'PAUSED', 'TERMINATED'],
    transition_history: [{ from_state: 'LISTENING', to_state: 'PROMPTING', trigger: 'silence_threshold_reached', timestamp: '2026-10-23T08:30:00Z' }],
  }
  const html = await renderToString(createSSRApp(Monitor, { session }))
  assert.match(html, /当前：播报/)
  assert.match(html, /达到静音阈值/)
  assert.equal((html.match(/class="state-monitor__node(?:\s|"|--)/g) ?? []).length, 5)
  assert.equal((html.match(/state-monitor__edge--active/g) ?? []).length, 1)
  assert.match(html, /datetime="2026-10-23T08:30:00Z"/)
  assert.match(html, /允许切换：识别、暂停、终止/)
})

test('model panel renders native form controls for provider, free model name, password keys and actions', async () => {
  const Panel = await loadComponent('ModelConfigPanel.vue')
  const html = await renderToString(createSSRApp(Panel))
  assert.match(html, /id="stt-provider"/)
  assert.match(html, /value="cloud"/)
  assert.match(html, /value="fallback"/)
  assert.match(html, /value="hybrid"/)
  assert.match(html, /服务商预设/)
  assert.match(html, /模型名称/)
  assert.match(html, /type="password"/)
  assert.match(html, /type="submit"[^>]*>保存配置/)
  assert.match(html, /测试连通性/)
  assert.doesNotMatch(html, /readonly/)
})

test('session workspace keeps plain text script preview and both cursor labels', async () => {
  const Session = await loadComponent('SessionPanel.vue')
  const html = await renderToString(createSSRApp(Session, {
    serviceAvailable: true,
    script: { id: 'script', name: '测试稿', content: '大家好，今天开始。', character_count: 9 },
  }))
  assert.match(html, /reading_position/)
  assert.match(html, /prompt_position/)
  assert.match(html, /讲稿双游标视图/)
  assert.match(html, /大家好，今天开始。/)
  assert.match(html, /Alt\+Shift\+P/)
})
