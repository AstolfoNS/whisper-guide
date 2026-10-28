import type { SessionSnapshot } from '../api/session'

const waitingLabels: Record<NonNullable<SessionSnapshot['prompt_wait_reason']>, string> = {
  inactive: '当前不在识别状态', stt_connecting: '等待识别连接就绪', audio_not_ready: '等待麦克风就绪确认',
  audio_missing: '未收到持续音频，无法判定静音', audio_backlog: '正在处理积压音频，先确认其中是否有人声',
  audio_processing: '正在处理最新音频，等待确认静音',
  voice_active: '检测到声音，静音计时尚未开始', voice_pending: '正在确认短促声音',
  at_end: '已到全文末尾，无后续内容', silence_wait: '静音计时中', recognition_settling: '结算本轮识别后开始提示',
  ready: '静音已到，准备提示', progress_wait: '等待讲稿有效推进',
  progress_ready: '进度等待已到，准备提示', progress_disarmed: '已提示一段，等待继续朗读或点击再等一下',
  awaiting_progress: '已提示一段，等待继续朗读或点击再等一下',
}

export function promptWaitLabel(snapshot?: SessionSnapshot): string {
  if (snapshot?.prompt_mode === 'progress' && snapshot.progress_prompt_armed === false
    && snapshot.state === 'LISTENING' && snapshot.prompt_wait_reason !== 'at_end'
    && !['stt_connecting', 'audio_not_ready', 'audio_missing', 'audio_backlog', 'audio_processing'].includes(snapshot.prompt_wait_reason ?? '')) {
    return waitingLabels.progress_disarmed
  }
  if (snapshot?.prompt_mode === 'progress' && snapshot.progress_remaining_ms != null
    && ['voice_active', 'voice_pending', 'silence_wait', 'progress_wait', 'ready', 'progress_ready'].includes(snapshot.prompt_wait_reason ?? '')) {
    return snapshot.progress_remaining_ms <= 0 ? waitingLabels.progress_ready : waitingLabels.progress_wait
  }
  return snapshot?.prompt_wait_reason ? waitingLabels[snapshot.prompt_wait_reason] || '等待服务端诊断' : ''
}

export function silenceTimingLabel(snapshot?: SessionSnapshot): string {
  if (snapshot?.silence_elapsed_ms == null) return '尚未开始'
  return `已静音 ${Math.min(snapshot.silence_elapsed_ms, snapshot.silence_timeout_ms)} / ${snapshot.silence_timeout_ms} ms`
}

export function progressTimingLabel(snapshot?: SessionSnapshot): string {
  if (snapshot?.progress_prompt_armed === false) return '本轮自动提示已完成'
  if (snapshot?.progress_remaining_ms == null) return '等待有效收音'
  return `进度未推进 · ${(Math.max(0, snapshot.progress_remaining_ms) / 1000).toFixed(1)} 秒后提示`
}

export function promptTimingLabel(snapshot?: SessionSnapshot): string {
  return snapshot?.prompt_mode === 'progress' ? progressTimingLabel(snapshot) : silenceTimingLabel(snapshot)
}
