<script setup lang="ts">
import { computed } from 'vue'
import type { SessionSnapshot } from '../../api/session'
const props = defineProps<{ snapshot?: SessionSnapshot }>()
const alignmentStatus = computed(() => {
  const snapshot = props.snapshot
  if (!snapshot || snapshot.state !== 'LISTENING' || snapshot.reliable) return ''
  if (snapshot.speaking) return '正在接收语音，等待稳定识别结果后确认讲稿位置。'
  if (!snapshot.raw_transcript?.trim()) return '尚未获得有效识别文本，自动播报暂缓；可继续朗读或手动定位。'
  if (!snapshot.raw_transcript_is_final && snapshot.raw_transcript.trim() !== snapshot.last_transcript?.trim()) return '正在等待识别定稿与讲稿对齐，自动播报暂缓。'
  return '未能可靠匹配讲稿，自动播报已暂停；可继续朗读、手动提示或重新定位。'
})
</script>
<template>
  <div class="transcript-comparison">
    <section aria-label="实时语音识别"><div class="transcript-heading">实时识别 <small>{{ snapshot?.raw_transcript_is_final ? 'final' : 'partial' }} · {{ snapshot?.recognition_filtered ? '已过滤' : snapshot?.raw_transcript_is_final ? '已定稿' : '修订中' }}</small></div><p>{{ snapshot?.raw_transcript || '等待语音输入' }}</p><small v-if="snapshot?.recognition_filtered" role="status" aria-live="polite">{{ snapshot.recognition_filter_reason || '缺少本段有效人声证据' }}，未用于讲稿定位{{ snapshot.reliable ? '，静音等待继续。' : '，请参考当前讲稿匹配状态。' }}</small></section>
    <section aria-label="讲稿匹配"><div class="transcript-heading">讲稿匹配 <small v-if="snapshot?.match_confidence != null">{{ Math.round(snapshot.match_confidence * 100) }}%</small></div><p>{{ snapshot?.aligned_transcript || '尚未确认位置' }}</p><small v-if="alignmentStatus" role="status" aria-live="polite">{{ alignmentStatus }}</small></section>
  </div>
</template>
<style scoped>
.transcript-comparison{display:grid;grid-template-columns:1fr 1fr;gap:12px}.transcript-comparison section{min-width:0;padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:var(--soft)}.transcript-heading{font-size:12px;color:var(--muted);display:flex;justify-content:space-between}.transcript-heading small{font-size:11px}.transcript-comparison p{margin:8px 0 0;min-height:24px;overflow-wrap:anywhere}.transcript-comparison section>small{display:block;color:var(--warning);font-size:12px;margin-top:5px}@media(max-width:600px){.transcript-comparison{grid-template-columns:1fr}}
</style>
