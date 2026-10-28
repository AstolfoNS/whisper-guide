<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { NButton, NInputNumber, NSelect, NDrawer, NDrawerContent, NModal, NSwitch } from 'naive-ui'
import { useSessionStore } from '../../stores/session'
import { useHealthStore } from '../../stores/health'
import { getSessionController } from '../../services/sessionController'
import { promptWaitLabel, promptTimingLabel } from '../../services/promptDiagnostics'
const session = useSessionStore()
const health = useHealthStore()
const controller = getSessionController()
const position = ref(0)
const settingsOpen = ref(false)
const positionOpen = ref(false)
const endConfirmationOpen = ref(false)
const canConfigure = computed(() => ['READY', 'PAUSED', 'TERMINATED'].includes(session.state))
const valid = computed(() => Number.isInteger(session.silenceTimeout) && session.silenceTimeout >= 1000
  && Number.isInteger(session.progressTimeout) && session.progressTimeout >= 2000 && session.progressTimeout <= 10000)
const options = computed(() => session.snapshot?.units.map((unit, index) => ({ label: `${index + 1} · ${unit.text}`, value: index })) ?? [])
function applyPosition() { controller.reposition(position.value); positionOpen.value = false }
function requestFinish() {
  if (!session.active) return
  settingsOpen.value = false; positionOpen.value = false; endConfirmationOpen.value = true
}
function finish() { controller.terminate(); endConfirmationOpen.value = false }
onMounted(() => window.addEventListener('request-session-end', requestFinish))
onBeforeUnmount(() => window.removeEventListener('request-session-end', requestFinish))
</script>
<template>
  <div class="session-controls" aria-label="演讲会话控制">
    <p v-if="session.state === 'LISTENING' && session.snapshot?.stt_ready === false" class="speech-connecting" role="status" aria-live="polite">正在连接语音识别，请稍候再朗读。</p>
    <div v-if="session.state === 'LISTENING' && session.snapshot?.prompt_wait_reason && session.snapshot.stt_ready !== false" class="prompt-wait-row">
      <p class="prompt-wait" role="status">{{ promptWaitLabel(session.snapshot) }}<span v-if="session.snapshot.prompt_mode === 'progress' || session.snapshot.silence_elapsed_ms != null"> · {{ promptTimingLabel(session.snapshot) }}</span></p>
      <NButton v-if="session.promptMode === 'progress'" size="small" class="postpone-button" @click="controller.postponePrompt()">再等一下</NButton>
    </div>
    <div class="control-buttons">
      <NButton v-if="session.state === 'READY' || session.state === 'TERMINATED'" class="primary-control" size="large" type="primary" :loading="session.busy" :disabled="!valid || !session.selectedScript || health.stt !== 'ready' || health.tts !== 'ready'" @click="controller.start()">{{ session.state === 'TERMINATED' ? '开始新演讲' : '开始演讲' }}</NButton>
      <NButton v-else-if="session.state === 'PAUSED'" class="primary-control" size="large" type="primary" :loading="session.busy" :disabled="!valid" @click="controller.resume()">继续演讲</NButton>
      <NButton v-else class="primary-control" size="large" type="primary" @click="controller.pause()">暂停演讲</NButton>
      <NButton v-if="session.state === 'PROMPTING'" class="prompt-control" size="large" type="warning" @click="controller.interrupt()">打断播报</NButton>
      <template v-else>
        <NButton class="desktop-control" :disabled="session.state !== 'LISTENING'" @click="controller.manualPrompt()">手动提示</NButton>
        <NButton class="mobile-control prompt-control" :disabled="session.state !== 'LISTENING'" @click="controller.manualPrompt()">立即提示</NButton>
      </template>
      <NButton class="desktop-control" :disabled="!session.active" type="error" secondary @click="requestFinish">结束</NButton>
      <NButton class="desktop-control" :disabled="!session.snapshot || !['READY','PAUSED','LISTENING'].includes(session.state)" @click="positionOpen = true">手动定位</NButton>
      <NButton class="more-control" aria-label="更多演讲操作" @click="settingsOpen = true"><span class="desktop-control">演讲设置</span><span class="mobile-control">更多</span></NButton>
    </div>
    <div class="timeout-control desktop-control">
      <label id="silence-timeout-label" for="silence-timeout">静音等待</label>
      <NInputNumber v-model:value="session.silenceTimeout" @update:value="session.markSilenceTimeoutEdited()" :min="1000" :step="1" :precision="0" :show-button="true" :disabled="!canConfigure" aria-labelledby="silence-timeout-label" aria-describedby="silence-timeout-help" :input-props="{ id: 'silence-timeout', 'aria-labelledby': 'silence-timeout-label', 'aria-describedby': 'silence-timeout-help' }" />
      <span id="silence-timeout-help">ms · 最短 1000</span>
      <NButton size="small" :disabled="!valid || !['READY','PAUSED'].includes(session.state)" @click="controller.configure()">应用</NButton>
    </div>
  </div>
  <NDrawer v-model:show="settingsOpen" width="min(100vw, 440px)" placement="right" :auto-focus="true">
    <NDrawerContent title="演讲设置与操作" closable>
      <div class="speech-settings">
        <label for="prompt-mode">自动提示模式</label>
        <select id="prompt-mode" v-model="session.promptMode" aria-label="自动提示模式" :disabled="!canConfigure" @change="session.markPromptOptionEdited('mode')"><option value="silence">静音后提示</option><option value="progress">进度超时提示</option></select>
        <p class="setting-help">{{ session.promptMode === 'progress' ? '讲稿位置持续未推进时主动提示，即使仍检测到声音；慢读、重复练习或改述时也可能插播。' : '停止讲话并达到静音等待时间后提示，适合希望减少打断的场景。' }}</p>
        <div class="mobile-setting">
          <label for="silence-timeout-settings">静音等待</label>
          <NInputNumber v-model:value="session.silenceTimeout" @update:value="session.markSilenceTimeoutEdited()" :min="1000" :step="100" :precision="0" :disabled="!canConfigure" aria-label="静音等待" :input-props="{ id: 'silence-timeout-settings', inputmode: 'numeric' }" />
          <p class="setting-help">毫秒，最短 1000；进度模式也保留静音提示。</p>
        </div>
        <template v-if="session.promptMode === 'progress'">
          <label for="progress-timeout">进度等待</label>
          <NInputNumber v-model:value="session.progressTimeout" @update:value="session.markPromptOptionEdited('timeout')" :min="2000" :max="10000" :step="500" :precision="0" :disabled="!canConfigure" aria-label="进度等待" :input-props="{ id: 'progress-timeout', inputmode: 'numeric' }" />
          <p class="setting-help">2000–10000 毫秒。噪音、重复文字和无关识别不会重新计时。</p>
          <label class="continuous-setting"><span>持续代读后文</span><NSwitch v-model:value="session.continuousPrompt" @update:value="session.markPromptOptionEdited('continuous')" :disabled="!canConfigure" aria-label="持续代读后文" /></label>
          <p class="setting-help">默认只提示一个短句；打开后，每次完整等待结束会继续提示下一段。</p>
        </template>
        <p v-if="!canConfigure" class="setting-help">请先暂停演讲，再修改模式或等待时间。</p>
        <NButton type="primary" :disabled="!valid || !canConfigure" @click="controller.configure(); settingsOpen = false">应用演讲设置</NButton>
        <div v-if="session.active" class="additional-actions">
          <NButton v-if="session.state === 'LISTENING' && session.promptMode === 'progress'" @click="controller.postponePrompt(); settingsOpen = false">再等一下</NButton>
          <NButton :disabled="!['READY','PAUSED','LISTENING'].includes(session.state)" @click="settingsOpen = false; positionOpen = true">手动定位</NButton>
          <NButton type="error" secondary @click="requestFinish">结束演讲</NButton>
        </div>
      </div>
    </NDrawerContent>
  </NDrawer>
  <NDrawer v-model:show="positionOpen" width="min(100vw, 440px)" placement="right" :auto-focus="true">
    <NDrawerContent title="手动定位" closable>
      <div class="speech-settings">
        <label id="manual-position-label" for="manual-position">下一次提示的起点</label>
        <NSelect v-model:value="position" :input-props="{ id: 'manual-position', 'aria-labelledby': 'manual-position-label' }" aria-label="选择讲稿位置" :options="options" filterable />
        <NButton type="primary" @click="applyPosition">应用位置</NButton>
      </div>
    </NDrawerContent>
  </NDrawer>
  <NModal v-model:show="endConfirmationOpen" preset="dialog" title="结束演讲" positive-text="确认结束" negative-text="取消" :mask-closable="false" @positive-click="finish">结束后释放当前演讲进度，讲稿文件保留。确认结束？</NModal>
</template>
<style scoped>
.session-controls{display:flex;gap:16px;flex-wrap:wrap;align-items:center;justify-content:space-between;padding:18px;border:1px solid var(--line);border-radius:12px;background:var(--surface);position:sticky;bottom:12px;z-index:5;box-shadow:0 6px 20px #0000000a;min-width:0}
.prompt-wait-row{flex-basis:100%;display:flex;align-items:center;justify-content:space-between;gap:12px;min-width:0}.prompt-wait{margin:0;color:var(--muted);font-size:13px;overflow-wrap:anywhere}.postpone-button{flex-shrink:0}.speech-connecting{flex-basis:100%;margin:0;color:var(--warning);font-size:14px}.control-buttons{display:flex;gap:8px;align-items:center;flex-wrap:wrap;min-width:0}.timeout-control{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)}.timeout-control .n-input-number{width:112px}.mobile-control,.mobile-setting{display:none}
.speech-settings{display:grid;gap:12px;padding-bottom:calc(20px + env(safe-area-inset-bottom, 0px))}.speech-settings select{width:100%;min-height:44px;padding:8px 12px;border:1px solid var(--line);border-radius:6px;color:var(--ink);background:var(--surface);font:inherit}.speech-settings select:disabled{opacity:.6}.speech-settings>label,.mobile-setting>label{font-size:14px;font-weight:600}.setting-help{font-size:13px;color:var(--muted);margin:0;line-height:1.7}.mobile-setting{gap:10px}.continuous-setting{display:flex;align-items:center;justify-content:space-between;gap:12px}.additional-actions{display:grid;gap:12px;border-top:1px solid var(--line);padding-top:20px;margin-top:8px}
@media(max-width:700px){.session-controls{bottom:0;padding:12px 12px calc(12px + env(safe-area-inset-bottom, 0px));gap:10px;border-radius:12px 12px 0 0}.desktop-control{display:none!important}.mobile-control{display:inline-flex}.mobile-setting{display:grid}.control-buttons{display:grid;grid-template-columns:minmax(0,1.3fr) minmax(0,1fr) minmax(0,.75fr);gap:8px;width:100%}.control-buttons .n-button{min-width:0;min-height:44px;padding-left:10px;padding-right:10px}.prompt-wait-row{gap:8px}.prompt-wait{font-size:12px}.postpone-button{min-height:44px}.speech-settings .n-button{min-height:44px}.continuous-setting{min-height:44px}.speech-settings :deep(.n-input),.speech-settings :deep(.n-base-selection){min-height:44px}.speech-settings select,.speech-settings :deep(.n-input__input-el),.speech-settings :deep(.n-base-selection-label){font-size:16px}}
</style>
