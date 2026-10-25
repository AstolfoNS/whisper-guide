<script setup lang="ts">
import { ref, useId } from 'vue'
import { NButton, NAlert, NInput, NPopconfirm } from 'naive-ui'
import { useScriptsStore } from '../../stores/scripts'
import { useSessionStore } from '../../stores/session'
const scripts = useScriptsStore()
const session = useSessionStore()
const uploadId = useId()
const query = ref('')
const uploading = ref(false)
const uploadError = ref('')
const deletionNotice = ref('')
const inUseMessage = '讲稿正在会话中使用，请结束后再删除'
function isBound(id: string) { return session.active && session.snapshot?.script_id === id }
function cannotDelete(id: string) { return isBound(id) || scripts.loading || Boolean(scripts.deletingId) || uploading.value }
function boundReasonId(id: string) { return 'script-in-use-' + uploadId + '-' + id }
async function select(id: string) {
  if (session.active || scripts.deletingId) return
  await scripts.select(id)
  if (!session.active) session.selectedScript = scripts.selected
}
async function remove(id: string, name: string): Promise<boolean> {
  if (cannotDelete(id)) return false
  uploadError.value = ''
  deletionNotice.value = ''
  const removed = await scripts.remove(id)
  if (removed) deletionNotice.value = '已删除讲稿“' + name + '”。'
  return removed
}
async function upload(event: Event) {
  const input = event.target as HTMLInputElement
  uploading.value = true; uploadError.value = ''
  try {
    const created = await scripts.upload(Array.from(input.files ?? []))
    if (!scripts.selected && !session.active && created[0]) await select(created[0].id)
  } catch (error) { uploadError.value = (error as Error).message }
  finally { uploading.value = false; input.value = '' }
}
</script>
<template>
  <section class="library-panel" aria-labelledby="library-title">
    <div class="panel-title"><h2 id="library-title">讲稿库</h2><NButton size="tiny" :loading="scripts.loading" :disabled="Boolean(scripts.deletingId)" @click="scripts.refresh()">刷新</NButton></div>
    <label :for="uploadId" class="upload-label">上传讲稿 <small>txt / text / md · 每份 ≤2 MiB</small></label>
    <input :id="uploadId" type="file" accept=".txt,.text,.md" multiple :disabled="uploading || Boolean(scripts.deletingId)" @change="upload" />
    <NAlert v-if="uploadError || scripts.error" type="error" :show-icon="false" role="alert">{{ uploadError || scripts.error }}</NAlert>
    <p v-if="deletionNotice" class="deletion-notice" role="status" aria-live="polite">{{ deletionNotice }}</p>
    <NInput v-model:value="query" placeholder="搜索讲稿" aria-label="搜索讲稿" clearable />
    <p v-if="session.active" class="muted">当前讲稿已绑定；结束会话后可更换。</p>
    <fieldset class="script-choices">
      <legend class="sr-only">选择演讲稿</legend>
      <div class="script-list">
        <div v-for="script in scripts.scripts.filter(item => item.name.toLowerCase().includes(query.toLowerCase()))" :key="script.id" class="script-row" :data-script-id="script.id">
          <label class="script-choice">
            <input type="radio" :name="'studio-script-' + uploadId" :value="script.id" :checked="scripts.selected?.id === script.id" :disabled="session.active || scripts.loading || Boolean(scripts.deletingId)" @change="select(script.id)" />
            <span>
              <span class="script-title">{{ script.name }}</span>
              <small>{{ script.character_count }} 字符</small>
              <small v-if="isBound(script.id)" :id="boundReasonId(script.id)" class="bound-reason">{{ inUseMessage }}</small>
            </span>
          </label>
          <NPopconfirm :disabled="cannotDelete(script.id)" positive-text="删除讲稿" negative-text="取消" :positive-button-props="{ type: 'error', loading: scripts.deletingId === script.id, disabled: cannotDelete(script.id) }" @positive-click="remove(script.id, script.name)">
            <template #trigger>
              <NButton size="tiny" type="error" secondary attr-type="button" :aria-label="'删除讲稿“' + script.name + '”'" :aria-describedby="isBound(script.id) ? boundReasonId(script.id) : undefined" :title="isBound(script.id) ? inUseMessage : '删除此讲稿'" :disabled="cannotDelete(script.id)" :loading="scripts.deletingId === script.id" @click.stop>删除</NButton>
            </template>
            确定删除讲稿“{{ script.name }}”？此操作不可撤销。
          </NPopconfirm>
        </div>
      </div>
    </fieldset>
    <p v-if="!scripts.scripts.length" class="muted">尚无讲稿，请上传纯文本文件。</p>
    <details v-if="scripts.selected" class="preview"><summary>正文预览</summary><pre>{{ scripts.selected.content }}</pre></details>
  </section>
</template>
<style scoped>
.library-panel{display:grid;grid-template-columns:minmax(0,1fr);gap:14px;min-width:0}.library-panel>*{min-width:0;max-width:100%}.panel-title{display:flex;align-items:center;justify-content:space-between}.panel-title h2{font-size:16px;margin:0}.upload-label{font-weight:600}.upload-label small{display:block;color:var(--muted);font-weight:400;margin-top:4px}input[type=file]{max-width:100%;font-size:12px}.script-choices{border:0;padding:0;margin:0}.script-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:start}.script-choice{display:flex;align-items:flex-start;gap:8px;cursor:pointer;min-width:0}.script-choice input{accent-color:var(--accent);margin-top:5px;width:16px;height:16px;flex-shrink:0}.script-choice>span{min-width:0}.script-list{display:grid;gap:12px;max-height:42vh;overflow:auto;padding:4px}.script-list small{display:block;color:var(--muted);font-size:11px}.script-list .bound-reason{color:var(--warning);margin-top:3px}.script-title{overflow-wrap:anywhere}.deletion-notice{font-size:12px;color:var(--muted);margin:0;overflow-wrap:anywhere}.preview{border-top:1px solid var(--line);padding-top:12px}.preview pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:200px;overflow:auto;font:inherit;font-size:13px;line-height:1.7}.muted{color:var(--muted);font-size:12px}
</style>
