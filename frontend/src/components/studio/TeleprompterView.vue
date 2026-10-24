<script setup lang="ts">
import { computed, nextTick, ref, shallowRef, watch } from 'vue'
import { NButton, NSlider, NSwitch } from 'naive-ui'
import type { SessionSnapshot } from '../../api/session'
const props = defineProps<{ text: string; snapshot?: SessionSnapshot }>()
const fontSize = ref(32)
const follow = ref(true)
const atEnd = computed(() => props.snapshot?.state === 'LISTENING' && props.snapshot.at_end && props.snapshot.reliable && !props.snapshot.speaking)
const viewport = ref<HTMLElement>()
const unitCache = shallowRef(props.snapshot?.units ?? [])
watch([() => props.text, () => props.snapshot?.script_id], () => { unitCache.value = props.snapshot?.units ?? [] })
const paragraphs = computed(() => {
  let offset = 0
  return props.text.split(/(?<=\n)/).map(text => {
    const start = offset; offset += text.length
    const units = unitCache.value.filter(unit => unit.start >= start && unit.start < offset)
    const fragments: { text: string; start: number; unit: boolean }[] = []
    let cursor = start
    for (const unit of units) {
      if (unit.start > cursor) fragments.push({ text: props.text.slice(cursor, unit.start), start: cursor, unit: false })
      fragments.push({ text: props.text.slice(unit.start, unit.end), start: unit.start, unit: true }); cursor = unit.end
    }
    if (cursor < offset) fragments.push({ text: props.text.slice(cursor, offset), start: cursor, unit: false })
    return { start, end: offset, fragments: fragments.length ? fragments : [{ text, start, unit: false }] }
  })
})
async function scrollToCursor() {
  await nextTick()
  const mark = viewport.value?.querySelector<HTMLElement>('[data-current="true"]')
  if (!mark || !viewport.value) return
  viewport.value.scrollTo({ top: Math.max(0, mark.offsetTop - viewport.value.offsetTop - viewport.value.clientHeight / 3), behavior: 'instant' })
}
watch(() => props.snapshot?.reading_position, () => { if (follow.value) void scrollToCursor() })
function returnToCursor() { follow.value = true; void scrollToCursor() }
</script>
<template>
  <section class="teleprompter" aria-label="讲稿双游标视图">
    <div class="prompter-toolbar">
      <span class="cursor-key"><span class="reading-swatch"></span>确认朗读 <span class="prompt-swatch"></span>提示起点</span>
      <label class="font-control">字号 <NSlider v-model:value="fontSize" :min="24" :max="56" :step="2" aria-label="讲稿字号" /></label>
      <label class="follow-control">跟随 <NSwitch v-model:value="follow" aria-label="自动跟随游标" /></label>
    </div>
    <p v-if="atEnd" class="end-note" role="status" aria-live="polite">已到讲稿末尾，不再自动续播；可回读或手动定位。</p>
    <div ref="viewport" class="prompter-body" tabindex="0" role="region" aria-label="演讲稿正文" :style="{ fontSize: `${fontSize}px` }" @wheel.passive="follow = false" @touchmove.passive="follow = false" @keydown="['PageDown','PageUp','ArrowDown','ArrowUp','Home','End'].includes($event.key) && (follow = false)">
      <p v-for="paragraph in paragraphs" :key="paragraph.start" v-memo="[paragraph, snapshot?.reading_position != null && snapshot.reading_position >= paragraph.start && snapshot.reading_position < paragraph.end ? snapshot.reading_position : null, snapshot?.prompt_position != null && snapshot.prompt_position >= paragraph.start && snapshot.prompt_position < paragraph.end ? snapshot.prompt_position : null]"><span v-for="fragment in paragraph.fragments" :key="fragment.start" :data-current="fragment.unit && fragment.start === snapshot?.reading_position" :class="{ 'reading-cursor': fragment.unit && fragment.start === snapshot?.reading_position, 'prompt-cursor': fragment.unit && fragment.start === snapshot?.prompt_position }">{{ fragment.text }}</span></p>
      <p v-if="!text" class="empty-text">从讲稿库选择本次演讲稿</p>
    </div>
    <NButton v-if="!follow" class="return-cursor" size="small" @click="returnToCursor">返回当前位置</NButton>
  </section>
</template>
<style scoped>
.teleprompter{position:relative;border:1px solid var(--line);border-radius:12px;background:var(--surface);overflow:hidden}.prompter-toolbar{display:flex;gap:16px;align-items:center;justify-content:space-between;padding:12px 18px;border-bottom:1px solid var(--line);font-size:12px;color:var(--muted);flex-wrap:wrap}.cursor-key{display:flex;align-items:center;gap:7px}.reading-swatch,.prompt-swatch{width:11px;height:11px;background:var(--cursor-reading);border:1px solid var(--accent);border-radius:2px}.prompt-swatch{background:var(--cursor-prompt);border-color:var(--warning)}.font-control{display:flex;align-items:center;gap:8px;width:150px}.font-control .n-slider{flex:1}.follow-control{display:flex;align-items:center;gap:8px}.prompter-body{position:relative;height:min(54vh,560px);min-height:280px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;padding:28px 32px 50px;line-height:1.9;letter-spacing:.02em;scrollbar-gutter:stable}.prompter-body p{margin:0 0 .6em}.reading-cursor{background:var(--cursor-reading);box-shadow:0 2px 0 var(--accent);border-radius:3px}.prompt-cursor{outline:2px solid var(--warning);outline-offset:1px;border-radius:3px}.end-note{margin:0;padding:10px 18px;border-bottom:1px solid var(--line);font-size:12px;color:var(--muted)}.empty-text{color:var(--muted);font-size:20px}.return-cursor{position:absolute;bottom:14px;right:18px}@media(max-width:700px){.prompter-body{padding:18px;min-height:300px}.font-control{width:120px}}
</style>
