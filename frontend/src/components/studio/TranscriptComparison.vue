<script setup lang="ts">
import { computed } from 'vue'
import type { SessionSnapshot } from '../../api/session'
import { recoveryFeedback } from './alignmentFeedback'
const props = defineProps<{ snapshot?: SessionSnapshot }>()
const positionReliable = computed(() => props.snapshot?.alignment_status ? props.snapshot.alignment_status === 'reliable' : Boolean(props.snapshot?.reliable))
const provisional = computed(() => {
  const snapshot = props.snapshot
  const position = snapshot?.provisional_position
  return snapshot?.state === 'LISTENING' && position != null && position !== snapshot.reading_position
    && snapshot.units?.some(unit => unit.start === position) && snapshot.provisional_transcript?.trim()
    ? snapshot.provisional_transcript : ''
})
const recovery = computed(() => props.snapshot ? recoveryFeedback(props.snapshot) : '')
const alignmentStatus = computed(() => {
  const snapshot = props.snapshot
  if (!snapshot || snapshot.state !== 'LISTENING') return ''
  if (snapshot.stt_ready === false) return '正在连接语音识别，请稍候再朗读。'
  if (snapshot.alignment_status === 'mismatch') return `最近一次有效识别未能可靠匹配讲稿，${recovery.value || '自动提示暂缓；正在尝试自动恢复，可继续朗读更完整的一段，也可手动定位。'}`
  if (recovery.value) return recovery.value
  if (positionReliable.value) return ''
  if (snapshot.speaking) return '正在接收语音，等待稳定识别结果后确认讲稿位置。'
  if (snapshot.alignment_status === 'pending' && snapshot.raw_transcript_is_current === false) return '正在等待本轮有效识别结果，自动提示暂缓；可继续朗读或手动定位。'
  if (!snapshot.raw_transcript?.trim()) return '尚未获得有效识别文本，自动播报暂缓；可继续朗读或手动定位。'
  if (!snapshot.raw_transcript_is_final && snapshot.raw_transcript.trim() !== snapshot.last_transcript?.trim()) return '正在等待识别定稿与讲稿对齐，自动播报暂缓。'
  if (snapshot.alignment_status === 'pending') return '正在等待本轮有效识别结果，自动提示暂缓；可继续朗读或手动定位。'
  return '未能可靠匹配讲稿，自动播报已暂停；可继续朗读、手动提示或重新定位。'
})
</script>
<template>
  <div class="transcript-comparison">
    <section aria-label="实时语音识别"><div class="transcript-heading">实时识别 <small v-if="(snapshot?.raw_transcript_is_current === false || snapshot?.state !== 'LISTENING') && snapshot?.raw_transcript?.trim()">上次结果</small><small v-else>{{ snapshot?.raw_transcript_is_final ? 'final' : 'partial' }} · {{ snapshot?.recognition_filtered ? '已过滤' : snapshot?.raw_transcript_is_final ? '已定稿' : '修订中' }}</small></div><p>{{ snapshot?.raw_transcript || '等待语音输入' }}</p><small v-if="snapshot?.recognition_filtered" role="status" aria-live="polite">{{ snapshot.recognition_filter_reason || '缺少本段有效人声证据' }}，未用于讲稿定位{{ positionReliable ? '，静音等待继续。' : '，请参考当前讲稿匹配状态。' }}</small></section>
    <section aria-label="讲稿匹配">
      <div class="transcript-heading">讲稿匹配 <small v-if="snapshot?.match_confidence != null" title="讲稿对齐得分，不代表原始识别准确率">{{ !positionReliable ? '上次匹配 ' : '' }}{{ Math.round(snapshot.match_confidence * 100) }}%</small></div>
      <p>{{ snapshot?.aligned_transcript || '尚未确认位置' }}</p>
      <div v-if="provisional" class="provisional-match" aria-label="临时定位，待确认"><div class="transcript-heading">临时定位 <small>待确认</small></div><p>{{ provisional }}</p><small>虚线标记供跟读参考，自动提示仍使用已确认位置。</small></div>
      <small v-if="alignmentStatus" role="status" aria-live="polite">{{ alignmentStatus }}</small>
      <small v-if="recovery && snapshot?.recovery_attempts" class="recovery-attempts">已尝试 {{ snapshot.recovery_attempts }} 次</small>
    </section>
  </div>
</template>
<style scoped>
.transcript-comparison{display:grid;grid-template-columns:1fr 1fr;gap:12px}.transcript-comparison section{min-width:0;padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:var(--soft)}.transcript-heading{font-size:12px;color:var(--muted);display:flex;justify-content:space-between}.transcript-heading small{font-size:11px}.transcript-comparison p{margin:8px 0 0;min-height:24px;overflow-wrap:anywhere}.transcript-comparison section>small{display:block;color:var(--warning);font-size:12px;margin-top:5px}.provisional-match{margin-top:12px;padding:10px 12px;border:1px dashed var(--focus);border-radius:6px}.provisional-match>small{display:block;margin-top:5px;font-size:12px;color:var(--muted)}.transcript-comparison section>.recovery-attempts{color:var(--muted)}@media(max-width:600px){.transcript-comparison{grid-template-columns:1fr}}
</style>
