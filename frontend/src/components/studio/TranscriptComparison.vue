<script setup lang="ts">
import type { SessionSnapshot } from '../../api/session'
defineProps<{ snapshot?: SessionSnapshot }>()
</script>
<template>
  <div class="transcript-comparison">
    <section aria-label="实时语音识别"><div class="transcript-heading">实时识别 <small>{{ snapshot?.raw_transcript_is_final ? 'final · 已定稿' : 'partial · 修订中' }}</small></div><p>{{ snapshot?.raw_transcript || '等待语音输入' }}</p></section>
    <section aria-label="讲稿匹配"><div class="transcript-heading">讲稿匹配 <small v-if="snapshot?.match_confidence != null">{{ Math.round(snapshot.match_confidence * 100) }}%</small></div><p>{{ snapshot?.aligned_transcript || '尚未确认位置' }}</p><small v-if="snapshot && !snapshot.reliable">保留上次确认位置；可继续朗读或手动定位。</small></section>
  </div>
</template>
<style scoped>
.transcript-comparison{display:grid;grid-template-columns:1fr 1fr;gap:12px}.transcript-comparison section{min-width:0;padding:14px 16px;border:1px solid var(--line);border-radius:10px;background:var(--soft)}.transcript-heading{font-size:12px;color:var(--muted);display:flex;justify-content:space-between}.transcript-heading small{font-size:11px}.transcript-comparison p{margin:8px 0 0;min-height:24px;overflow-wrap:anywhere}.transcript-comparison section>small{display:block;color:var(--warning);font-size:12px;margin-top:5px}@media(max-width:600px){.transcript-comparison{grid-template-columns:1fr}}
</style>
