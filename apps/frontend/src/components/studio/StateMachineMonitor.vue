<script setup lang="ts">
import { computed, useId } from 'vue'
import type { SessionSnapshot, SessionState, TransitionRecord } from '../../api/session'

const props = defineProps<{ session?: SessionSnapshot }>()

const labels: Record<SessionState, string> = {
  READY: '就绪',
  LISTENING: '识别',
  PROMPTING: '播报',
  PAUSED: '暂停',
  TERMINATED: '终止',
}
const nodes: Array<{ state: SessionState; x: number; y: number }> = [
  { state: 'READY', x: 75, y: 95 },
  { state: 'LISTENING', x: 270, y: 95 },
  { state: 'PROMPTING', x: 505, y: 95 },
  { state: 'PAUSED', x: 390, y: 215 },
  { state: 'TERMINATED', x: 390, y: 365 },
]
// Independent circle ports separate opposite directions. The outer READY route
// leaves the inner K4 layout planar, so no two edges cross or share a segment.
const edges: Array<{ from: SessionState; to: SessionState; points: string }> = [
  { from: 'READY', to: 'LISTENING', points: '107,95 238,95' },
  { from: 'LISTENING', to: 'PROMPTING', points: '300,87 475,87' },
  { from: 'PROMPTING', to: 'LISTENING', points: '475,103 300,103' },
  { from: 'LISTENING', to: 'PAUSED', points: '298,111 374,188' },
  { from: 'PROMPTING', to: 'PAUSED', points: '478,113 415,194' },
  { from: 'PAUSED', to: 'LISTENING', points: '362,200 286,123' },
  { from: 'READY', to: 'TERMINATED', points: '75,127 75,365 358,365' },
  { from: 'LISTENING', to: 'TERMINATED', points: '258,125 375,337' },
  { from: 'PROMPTING', to: 'TERMINATED', points: '493,125 404,337' },
  { from: 'PAUSED', to: 'TERMINATED', points: '390,247 390,333' },
]
const graphId = useId()
const graphTitleId = `state-graph-title-${graphId}`
const graphDescriptionId = `state-graph-description-${graphId}`
const arrowId = `state-arrow-${graphId}`
const activeArrowId = `state-arrow-active-${graphId}`

const currentState = computed<SessionState>(() => props.session?.current_state ?? props.session?.state ?? 'READY')
const previousState = computed<SessionState | null>(() => props.session?.previous_state ?? null)
const activeEdge = computed(() => edges.find((edge) => edge.from === previousState.value && edge.to === currentState.value))
const transitionTrigger = computed(() => props.session?.transition_trigger || (props.session ? 'session_initialized' : ''))
const transitionAt = computed(() => props.session?.transition_at || '')
const history = computed<TransitionRecord[]>(() => {
  const records = props.session?.transition_history ?? []
  if (records.length || !props.session || !transitionTrigger.value) return records.slice(-10).reverse()
  return [{ from_state: previousState.value, to_state: currentState.value, trigger: transitionTrigger.value, timestamp: transitionAt.value }]
})

function isCurrent(state: SessionState) { return currentState.value === state }
function isActiveEdge(from: SessionState, to: SessionState) {
  return previousState.value === from && currentState.value === to
}
function formatTime(value: string | number | null | undefined) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('zh-CN', { hour12: false })
}
function triggerLabel(value: string) {
  const labelsByTrigger: Record<string, string> = {
    session_initialized: '会话初始化',
    session_started: '开始演讲',
    manual_prompt: '手动提示',
    service_fault: '语音服务异常',
    user_speech_started: '检测到人声',
    silence_threshold_reached: '达到静音阈值',
    recognition_wait_timeout: '静音到期，按保存起点提示',
    prompt_audio_finished: '提示音播放完成',
    user_paused: '用户暂停',
    user_resumed: '用户继续',
    speech_interrupted_prompt: '朗读打断播报',
    ws_disconnected: '连接断开',
    user_terminated: '用户结束',
  }
  return (labelsByTrigger[value] ?? value) || '—'
}
</script>

<template>
  <section class="state-monitor" aria-labelledby="state-monitor-title">
    <div class="section-heading">
      <div>
        <h2 id="state-monitor-title">五状态机</h2>
      </div>
      <span class="state-monitor__current" :class="`state-monitor__current--${currentState.toLowerCase()}`" role="status" aria-live="polite">
        当前：{{ labels[currentState] }}
      </span>
    </div>

    <div class="state-monitor__transition" role="status" aria-live="polite">
      <template v-if="props.session">
        <span class="state-monitor__state">{{ previousState ? labels[previousState] : '—' }}</span>
        <span aria-hidden="true">→</span>
        <span class="state-monitor__trigger">{{ triggerLabel(transitionTrigger) }}</span>
        <span aria-hidden="true">→</span>
        <span class="state-monitor__state state-monitor__state--current">{{ labels[currentState] }}</span>
        <time v-if="transitionAt" :datetime="transitionAt || undefined">{{ formatTime(transitionAt) }}</time>
      </template>
      <span v-else class="state-monitor__empty">尚未创建会话</span>
    </div>

    <p v-if="props.session?.allowed_transitions?.length" class="input-help">允许切换：{{ props.session.allowed_transitions.map((state) => labels[state]).join("、") }}</p>
    <div class="state-monitor__graph">
      <svg viewBox="0 0 570 420" role="img" :aria-labelledby="`${graphTitleId} ${graphDescriptionId}`" focusable="false">
        <title :id="graphTitleId">五状态机状态关系图</title>
        <desc :id="graphDescriptionId">
          五个节点展示全部十条合法迁移，箭头表示迁移方向。当前状态：{{ labels[currentState] }}。
          <template v-if="activeEdge">最近迁移：{{ labels[activeEdge.from] }}到{{ labels[activeEdge.to] }}，以高亮箭头表示。</template>
          详细触发原因与流转时间见下方记录。
        </desc>
        <defs>
          <marker :id="arrowId" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" class="state-monitor__arrow" />
          </marker>
          <marker :id="activeArrowId" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" class="state-monitor__arrow state-monitor__arrow--active" />
          </marker>
        </defs>
        <polyline
          v-for="edge in edges"
          :key="`${edge.from}-${edge.to}`"
          :points="edge.points"
          :data-from="edge.from"
          :data-to="edge.to"
          class="state-monitor__edge"
          :class="{ 'state-monitor__edge--active': isActiveEdge(edge.from, edge.to) }"
          :marker-end="`url(#${isActiveEdge(edge.from, edge.to) ? activeArrowId : arrowId})`"
        >
          <title>{{ labels[edge.from] }} → {{ labels[edge.to] }}</title>
        </polyline>
        <g v-for="node in nodes" :key="node.state" :data-state="node.state" :transform="`translate(${node.x} ${node.y})`" class="state-monitor__node" :class="{ 'state-monitor__node--current': isCurrent(node.state) }" aria-hidden="true">
          <circle r="26" />
          <circle v-if="isCurrent(node.state)" r="31" class="state-monitor__node-ring" />
          <text y="7" text-anchor="middle">{{ labels[node.state] }}</text>
        </g>
      </svg>
    </div>

    <div class="state-monitor__history">
      <h3>最近流转</h3>
      <p v-if="!history.length" class="empty-state">会话开始后显示状态变化。</p>
      <ol v-else aria-label="最近状态流转记录">
        <li v-for="(entry, index) in history" :key="`${entry.timestamp}-${index}`">
          <span class="state-monitor__sequence">#{{ history.length - index }}</span>
          <span>{{ entry.from_state ? labels[entry.from_state] : '—' }} → {{ labels[entry.to_state] }}</span>
          <span class="state-monitor__history-trigger">{{ triggerLabel(entry.trigger) }}</span>
          <time :datetime="entry.timestamp || undefined">{{ formatTime(entry.timestamp) }}</time>
        </li>
      </ol>
    </div>
  </section>
</template>

<style scoped>
.state-monitor__graph{max-width:570px;margin-inline:auto}.state-monitor__graph svg{display:block;width:100%;height:auto;color:var(--muted)}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px}.section-heading h2{font-size:16px;margin:0}.state-monitor__transition{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:14px 0;font-size:13px}.state-monitor__trigger{color:var(--muted)}.state-monitor__edge{fill:none;stroke:var(--muted);stroke-width:1.5;stroke-linejoin:miter}.state-monitor__arrow{fill:var(--muted)}.state-monitor__arrow--active{fill:var(--accent)}.state-monitor__edge--active{stroke:var(--accent);stroke-width:3}.state-monitor__node circle{fill:var(--soft);stroke:var(--line);stroke-width:1.5}.state-monitor__node text{fill:var(--text);font-size:20px;font-weight:600}.state-monitor__node--current circle{fill:var(--cursor-reading);stroke:var(--accent);stroke-width:2}.state-monitor__node .state-monitor__node-ring{fill:none;stroke:var(--accent);stroke-dasharray:4 4}.state-monitor__history h3{font-size:13px}.state-monitor__history ol{list-style:none;margin:0;padding:0;display:grid;gap:8px}.state-monitor__history li{display:grid;grid-template-columns:30px 120px 1fr 80px;gap:8px;font-size:12px}.state-monitor__history-trigger,.state-monitor__history time,.input-help{color:var(--muted)}.input-help{font-size:12px}.state-monitor__current{padding:3px 10px;background:var(--soft);border-radius:6px;font-size:12px}@media(max-width:600px){.state-monitor__history li{grid-template-columns:25px 1fr}.state-monitor__history li time{grid-column:2}}
</style>
