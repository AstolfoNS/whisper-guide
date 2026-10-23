<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { getScript, listScripts, uploadScripts } from '../api/scripts'
import type { ScriptDocument, ScriptSummary } from '../api/scripts'

const props = defineProps<{ locked?: boolean }>()
const emit = defineEmits<{ selected: [script: ScriptDocument | undefined] }>()

const maxFiles = 10
const maxFileBytes = 2 * 1024 * 1024
const scripts = ref<ScriptSummary[]>([])
const pendingFiles = ref<File[]>([])
const selectedScriptId = ref('')
const selectedScript = ref<ScriptDocument>()
const selecting = ref(false)
const loading = ref(false)
const uploading = ref(false)
const libraryError = ref('')
const uploadError = ref('')
const selectionError = ref('')
const uploadMessage = ref('')
const filesValid = ref(true)
const fileInput = ref<HTMLInputElement>()
let libraryRequest: AbortController | undefined
let selectionRequest: AbortController | undefined
let uploadRequest: AbortController | undefined
const requestTimers = new Map<AbortController, ReturnType<typeof setTimeout>>()

const pendingNames = computed(() => pendingFiles.value.map((file) => file.name).join('、'))

function startRequest() {
  const controller = new AbortController()
  requestTimers.set(controller, setTimeout(() => controller.abort(), 30000))
  return controller
}

function finishRequest(controller: AbortController) {
  clearTimeout(requestTimers.get(controller))
  requestTimers.delete(controller)
}

function errorMessage(error: unknown, controller: AbortController) {
  if (controller.signal.aborted) return '请求超时，请检查连接后重试。'
  return error instanceof Error ? error.message : '请求失败，请稍后重试。'
}

async function refreshScripts() {
  libraryRequest?.abort()
  const controller = startRequest()
  libraryRequest = controller
  loading.value = true
  libraryError.value = ''
  try {
    const result = await listScripts(controller.signal)
    if (controller.signal.aborted || libraryRequest !== controller) return
    scripts.value = result
    if (selectedScriptId.value && !result.some((script) => script.id === selectedScriptId.value)) {
      selectionRequest?.abort()
      selectionRequest = undefined
      selectedScriptId.value = ''
      selectedScript.value = undefined
      emit('selected', undefined)
      selecting.value = false
      selectionError.value = '原先选择的讲稿已不可用，请重新选择。'
    }
  } catch (error) {
    if (libraryRequest === controller) libraryError.value = errorMessage(error, controller)
  } finally {
    finishRequest(controller)
    if (libraryRequest === controller) loading.value = false
  }
}

function chooseFiles(event: Event) {
  const input = event.target as HTMLInputElement
  pendingFiles.value = Array.from(input.files ?? [])
  uploadError.value = ''
  uploadMessage.value = ''
  filesValid.value = true
  if (pendingFiles.value.length > maxFiles) {
    filesValid.value = false
    uploadError.value = `每次最多上传 ${maxFiles} 份讲稿，请重新选择。`
    return
  }
  for (const file of pendingFiles.value) {
    if (!/\.(txt|text|md)$/i.test(file.name)) {
      filesValid.value = false
      uploadError.value = `“${file.name}”不是支持的纯文本文件，请选择 .txt、.text 或 .md 文件。`
      return
    }
    if (file.size > maxFileBytes) {
      filesValid.value = false
      uploadError.value = `“${file.name}”超过单份 2 MiB 的大小限制。`
      return
    }
    if (file.size === 0) {
      filesValid.value = false
      uploadError.value = `“${file.name}”是空文件，请选择包含正文的讲稿。`
      return
    }
  }
}

async function selectScript(id: string) {
  if (props.locked) return
  selectionRequest?.abort()
  const controller = startRequest()
  selectionRequest = controller
  selectedScriptId.value = id
  selectedScript.value = undefined
  emit('selected', undefined)
  selecting.value = true
  selectionError.value = ''
  try {
    const result = await getScript(id, controller.signal)
    if (controller.signal.aborted || selectionRequest !== controller) return
    selectedScript.value = result
    emit('selected', result)
  } catch (error) {
    if (selectionRequest === controller) selectionError.value = errorMessage(error, controller)
  } finally {
    finishRequest(controller)
    if (selectionRequest === controller) selecting.value = false
  }
}

async function upload() {
  if (!pendingFiles.value.length || !filesValid.value || uploading.value) return
  const controller = startRequest()
  uploadRequest = controller
  uploading.value = true
  uploadError.value = ''
  uploadMessage.value = ''
  try {
    const created = await uploadScripts(pendingFiles.value, controller.signal)
    if (controller.signal.aborted) return
    scripts.value = [...created, ...scripts.value.filter((script) => !created.some((item) => item.id === script.id))]
    uploadMessage.value = `已上传 ${created.length} 份讲稿。`
    pendingFiles.value = []
    if (fileInput.value) fileInput.value.value = ''
    if (!selectedScriptId.value && created[0]) {
      selectedScriptId.value = created[0].id
      selectedScript.value = created[0]
      emit('selected', created[0])
    }
    await refreshScripts()
  } catch (error) {
    uploadError.value = errorMessage(error, controller)
  } finally {
    finishRequest(controller)
    uploading.value = false
  }
}

function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}

onMounted(refreshScripts)
onBeforeUnmount(() => {
  libraryRequest?.abort()
  selectionRequest?.abort()
  uploadRequest?.abort()
  libraryRequest = undefined
  selectionRequest = undefined
  for (const timeout of requestTimers.values()) clearTimeout(timeout)
  requestTimers.clear()
})
</script>

<template>
  <section class="script-card" aria-labelledby="scripts-title">
    <div class="section-heading">
      <div>
<h2 id="scripts-title">我的讲稿</h2>
      </div>
      <button type="button" class="secondary-button" :disabled="loading || uploading" @click="refreshScripts">
        {{ loading ? '刷新中…' : '刷新讲稿列表' }}
      </button>
    </div>

    <form class="upload-form" :aria-busy="uploading" @submit.prevent="upload">
      <label for="script-files">选择讲稿文件（支持多选）</label>
      <p id="upload-help" class="input-help">
        txt / text / md · 每份 ≤ 2 MiB · 每次 ≤ 10 份
      </p>
      <input
        id="script-files"
        ref="fileInput"
        type="file"
        accept=".txt,.text,.md"
        multiple
        :disabled="uploading"
        aria-describedby="upload-help pending-files upload-error"
        @change="chooseFiles"
      />
      <p id="pending-files" class="input-help">{{ pendingFiles.length ? `已选择 ${pendingFiles.length} 份：${pendingNames}` : '尚未选择文件。' }}</p>
      <p id="upload-error" class="form-error" role="alert">{{ uploadError }}</p>
      <button type="submit" :disabled="uploading || !pendingFiles.length || !filesValid">
        {{ uploading ? '正在上传…' : '上传讲稿' }}
      </button>
      <p class="form-message" role="status" aria-live="polite">{{ uploadMessage }}</p>
    </form>

    <div class="script-workspace">
      <div class="script-library" :aria-busy="loading">
        <p v-if="libraryError" class="form-error" role="alert">{{ libraryError }}</p>
        <p v-if="!scripts.length" class="empty-state" role="status">
          {{ loading ? '正在读取讲稿…' : libraryError ? '讲稿列表暂时不可用，可重试刷新。' : '暂无讲稿' }}
        </p>
        <fieldset v-else class="script-choices">
          <legend>选择本次演讲稿（共 {{ scripts.length }} 份）</legend>
          <label v-for="script in scripts" :key="script.id" class="script-choice" :class="{ 'script-choice--selected': script.id === selectedScriptId }">
            <input
              type="radio"
              name="selected-script"
              :value="script.id"
              :checked="script.id === selectedScriptId"
              :disabled="locked"
              @change="selectScript(script.id)"
            />
            <span>
              <span class="script-name">{{ script.name }}</span>
              <span class="script-meta">{{ script.character_count }} 字符 · {{ formatDate(script.created_at) }}</span>
            </span>
          </label>
        </fieldset>
      </div>

      <section class="script-preview" aria-labelledby="preview-title" :aria-busy="selecting">
        <h3 id="preview-title">本次演讲稿</h3>
        <p class="form-error" role="alert">{{ selectionError }}</p>
        <p v-if="selecting" role="status">正在加载讲稿内容…</p>
        <template v-else-if="selectedScript">
          <p class="selected-script-name" role="status">已选择：{{ selectedScript.name }}</p>
          <p class="script-meta">{{ selectedScript.character_count }} 字符</p>
          <div class="script-content" tabindex="0" role="region" :aria-label="`${selectedScript.name}全文预览`">{{ selectedScript.content }}</div>
        </template>
        <p v-else-if="!selectionError" class="empty-state">未选择讲稿</p>
        <button v-if="selectionError && selectedScriptId" type="button" class="secondary-button" @click="selectScript(selectedScriptId)">重新加载所选讲稿</button>
      </section>
    </div>

    <p v-if="locked" class="input-help" role="status">会话中讲稿已锁定，结束后可更换。</p>
  </section>
</template>
