<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton, NTag, NDrawer, NDrawerContent } from 'naive-ui'
import { useHealthStore } from '../../stores/health'
import { useSessionStore } from '../../stores/session'
import { refreshHealth } from '../../services/healthMonitor'
import { getSessionController } from '../../services/sessionController'
import { speechFallbackMessage } from '../../services/speechDiagnostics'
import { promptWaitLabel, silenceTimingLabel, progressTimingLabel } from '../../services/promptDiagnostics'
const health = useHealthStore()
const session = useSessionStore()
const open = ref(false)
const connecting = computed(() => session.state === 'LISTENING' && session.snapshot?.stt_ready === false)
const fallbackReason = computed(() => session.snapshot?.stt_fallback ? speechFallbackMessage(session.snapshot.stt_fallback_reason) : '')
const sessionProvider = computed(() => {
  const snapshot = session.snapshot
  if (!snapshot) return '未创建会话'
  if (snapshot.stt_fallback) return '本地 sherpa-onnx（云端回退）'
  const active = snapshot.stt_provider_active ?? snapshot.stt_provider
  if (!active) return '未读取'
  return active === 'local' ? '本地 sherpa-onnx' : active === 'cloud' ? '云端识别' : active
})
const labels: Record<string, string> = { ready: '就绪', unavailable: '不可用', unknown: '待检查', fallback: '降级' }
</script>
<template>
  <NButton size="small" secondary aria-label="打开服务诊断" @click="open = true">
    {{ connecting ? '连接语音识别中' : session.snapshot?.stt_fallback ? '识别已回退本地' : '服务' }} <span class="health-summary">STT {{ connecting ? '连接中' : session.snapshot?.stt_fallback ? '本地降级' : labels[health.stt] }} · VAD {{ labels[health.vad] }} · TTS {{ health.tts === 'ready' ? '已安装' : labels[health.tts] }}</span><span v-if="fallbackReason" class="fallback-summary" :title="fallbackReason">{{ fallbackReason }}</span>
  </NButton>
  <NDrawer v-model:show="open" width="min(420px, 100vw)" placement="right" :auto-focus="true">
    <NDrawerContent title="服务与连接诊断" closable>
      <dl class="health-details">
        <div><dt>后端 HTTP</dt><dd>{{ labels[health.backend] }}</dd></div>
        <div><dt>会话 WebSocket</dt><dd>{{ { disconnected: '未连接', connecting: '连接中', connected: '已连接' }[session.connection] }}</dd></div>
        <div><dt>麦克风</dt><dd>{{ { idle: '未采集', requesting: '等待授权', active: '采集中', error: '不可用' }[session.microphone] }}</dd></div>
        <div v-if="health.captureContextRate"><dt>采集采样率</dt><dd>输入 {{ health.captureInputRate ? `${health.captureInputRate} Hz` : '未上报' }} · 音频图 {{ health.captureContextRate }} Hz → 16000 Hz 单声道 PCM</dd></div>
        <div v-if="health.captureChannels"><dt>输入声道</dt><dd>{{ health.captureChannels }} → 1</dd></div>
        <div><dt>STT 服务能力</dt><dd>{{ health.provider }} · {{ labels[health.stt] }}</dd></div>
        <div><dt>当前会话 STT</dt><dd>{{ sessionProvider }}</dd></div>
        <div v-if="session.snapshot?.state === 'LISTENING'"><dt>会话识别连接</dt><dd>{{ connecting ? '正在连接，请稍候再朗读' : session.snapshot.stt_ready === true ? '已就绪' : '未上报' }}</dd></div>
        <div v-if="fallbackReason"><dt>本次回退原因</dt><dd data-testid="fallback-reason">{{ fallbackReason }}</dd></div>
        <div v-if="session.snapshot?.prompt_wait_reason"><dt>自动提示等待原因</dt><dd>{{ promptWaitLabel(session.snapshot) }}</dd></div>
        <div v-if="session.snapshot?.prompt_wait_reason"><dt>静音计时</dt><dd>{{ silenceTimingLabel(session.snapshot) }}</dd></div>
        <div v-if="session.snapshot?.prompt_mode === 'progress'"><dt>进度计时</dt><dd>{{ progressTimingLabel(session.snapshot) }}</dd></div>
        <div v-if="session.snapshot?.last_audio_received_age_ms != null"><dt>最近音频到达</dt><dd>{{ session.snapshot.last_audio_received_age_ms }} ms 前</dd></div>
        <div v-if="session.snapshot?.audio_queued_ms != null"><dt>音频处理</dt><dd>排队 {{ session.snapshot.audio_queued_ms }} ms · 最近推理 {{ session.snapshot.audio_inference_ms ?? 0 }} ms / {{ session.snapshot.audio_batch_frames ?? 0 }} 帧</dd></div>
        <div><dt>VAD</dt><dd>{{ labels[health.vad] }}</dd></div>
        <div><dt>TTS 依赖</dt><dd>{{ health.tts === 'ready' ? '已安装，合成时检查网络' : labels[health.tts] }}</dd></div>
        <div><dt>最近播放</dt><dd>{{ { unknown: '尚未播放', pending: '准备或播放中', success: '浏览器播放结束', failed: '播放失败', stopped: '已取消播放' }[health.playback] }}</dd></div>
        <div v-if="health.playbackDetail"><dt>播放原因</dt><dd role="status">{{ health.playbackDetail }}</dd></div>
        <div><dt>默认配置</dt><dd>{{ health.configName || '未读取' }}</dd></div>
        <div><dt>当前会话配置</dt><dd>{{ session.snapshot?.model_config_name || '未创建会话' }}</dd></div>
        <div><dt>语义模型</dt><dd>{{ health.llmMode === 'off' ? '关闭' : health.model || '待配置' }}</dd></div>
        <div><dt>检查时间</dt><dd>{{ health.checkedAt ? new Date(health.checkedAt).toLocaleTimeString() : '未检查' }}</dd></div>
      </dl>
      <p v-if="health.detail" role="status">{{ health.detail }}</p>
      <NTag v-if="session.snapshot?.stt_fallback" type="warning" role="status">云端识别已回退本地</NTag>
      <NTag v-if="health.cloud" type="warning">当前配置允许音频发送至云端，本服务不保存录音</NTag>
      <div class="health-actions">
        <NButton :loading="health.checking" @click="refreshHealth">刷新检查</NButton>
        <NButton :disabled="['LISTENING', 'PROMPTING'].includes(session.state)" @click="getSessionController().reconnect()">重连会话</NButton>
      </div>
      <p class="muted">检查状态不会开启麦克风或调用付费模型。开始和继续演讲请返回工作台。</p>
      <p class="muted">提示音发送到浏览器的系统默认输出。浏览器播放结束仅表示音频播放流程完成，请同时核对系统音量、标签页静音和实际扬声器。</p>
    </NDrawerContent>
  </NDrawer>
</template>
<style scoped>
.fallback-summary{margin-left:8px;max-width:230px;overflow:hidden;text-overflow:ellipsis;color:var(--warning);font-size:12px}.health-summary{margin-left:8px;font-size:12px}.health-details{display:grid;gap:16px}.health-details>div{display:flex;justify-content:space-between;gap:18px;border-bottom:1px solid var(--line);padding-bottom:10px}.health-details dt{color:var(--muted)}.health-details dd{margin:0;text-align:right;overflow-wrap:anywhere}.health-actions{display:flex;gap:8px;margin:24px 0}.muted{color:var(--muted);font-size:12px}@media(max-width:700px){.health-summary,.fallback-summary{display:none}}

@media(max-width:700px){.health-details>div{flex-wrap:wrap;gap:6px}.health-details dd{flex-basis:100%;text-align:left}.health-actions{flex-wrap:wrap}.health-actions .n-button{min-height:44px}}

</style>
