<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton, NInputNumber, NSelect, NPopover } from 'naive-ui'
import { useSessionStore } from '../../stores/session'
import { useHealthStore } from '../../stores/health'
import { getSessionController } from '../../services/sessionController'
import { promptWaitLabel, silenceTimingLabel } from '../../services/promptDiagnostics'
const session = useSessionStore()
const health = useHealthStore()
const controller = getSessionController()
const position = ref(0)
const valid = computed(() => Number.isInteger(session.silenceTimeout) && session.silenceTimeout >= 1000)
const options = computed(() => session.snapshot?.units.map((unit, index) => ({ label: `${index + 1} · ${unit.text}`, value: index })) ?? [])
</script>
<template>
  <div class="session-controls" aria-label="演讲会话控制">
    <p v-if="session.state === 'LISTENING' && session.snapshot?.stt_ready === false" class="speech-connecting" role="status" aria-live="polite">正在连接语音识别，请稍候再朗读。</p>
    <p v-if="session.state === 'LISTENING' && session.snapshot?.prompt_wait_reason && session.snapshot.stt_ready !== false" class="prompt-wait" role="status">{{ promptWaitLabel(session.snapshot) }}<span v-if="session.snapshot.silence_elapsed_ms != null"> · {{ silenceTimingLabel(session.snapshot) }}</span></p>
    <div class="control-buttons">
      <NButton v-if="session.state === 'READY' || session.state === 'TERMINATED'" size="large" type="primary" :loading="session.busy" :disabled="!valid || !session.selectedScript || health.stt !== 'ready' || health.tts !== 'ready'" @click="controller.start()">{{ session.state === 'TERMINATED' ? '开始新演讲' : '开始演讲' }}</NButton>
      <NButton v-else-if="session.state === 'PAUSED'" size="large" type="primary" :loading="session.busy" :disabled="!valid" @click="controller.resume()">继续演讲</NButton>
      <NButton v-else size="large" type="primary" @click="controller.pause()">暂停演讲</NButton>
      <NButton v-if="session.state === 'PROMPTING'" size="large" type="warning" @click="controller.interrupt()">打断播报</NButton>
      <NButton :disabled="session.state !== 'LISTENING'" @click="controller.manualPrompt()">手动提示</NButton>
      <NButton :disabled="!session.active" type="error" secondary @click="controller.terminate()">结束</NButton>
      <NPopover trigger="click" placement="top" :width="310">
        <template #trigger><NButton :disabled="!session.snapshot || !['READY','PAUSED','LISTENING'].includes(session.state)">手动定位</NButton></template>
        <label id="manual-position-label" for="manual-position">下一次提示的起点</label>
        <NSelect v-model:value="position" :input-props="{ id: 'manual-position', 'aria-labelledby': 'manual-position-label' }" aria-label="选择讲稿位置" :options="options" filterable />
        <NButton style="margin-top:10px" @click="controller.reposition(position)">应用位置</NButton>
      </NPopover>
    </div>
    <div class="timeout-control">
      <label id="silence-timeout-label" for="silence-timeout">静音等待</label>
      <NInputNumber v-model:value="session.silenceTimeout" :min="1000" :step="1" :precision="0" :show-button="true" :disabled="!['READY','PAUSED','TERMINATED'].includes(session.state)" aria-labelledby="silence-timeout-label" aria-describedby="silence-timeout-help" :input-props="{ id: 'silence-timeout', 'aria-labelledby': 'silence-timeout-label', 'aria-describedby': 'silence-timeout-help' }" />
      <span id="silence-timeout-help">ms · 最短 1000</span>
      <NButton size="small" :disabled="!valid || !['READY','PAUSED'].includes(session.state)" @click="controller.configure()">应用</NButton>
    </div>
  </div>
</template>
<style scoped>
.session-controls{display:flex;gap:16px;flex-wrap:wrap;align-items:center;justify-content:space-between;padding:18px;border:1px solid var(--line);border-radius:12px;background:var(--surface);position:sticky;bottom:12px;z-index:5;box-shadow:0 6px 20px #0000000a}.prompt-wait{flex-basis:100%;margin:0;color:var(--muted);font-size:13px}.speech-connecting{flex-basis:100%;margin:0;color:var(--warning);font-size:14px}.control-buttons{display:flex;gap:8px;align-items:center;flex-wrap:wrap}.timeout-control{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)}.timeout-control .n-input-number{width:112px}@media(max-width:700px){.session-controls{bottom:8px;padding:12px;gap:12px}.timeout-control{width:100%;flex-wrap:wrap}.control-buttons .n-button--large-type{min-width:130px}}
</style>
