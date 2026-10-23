<script setup lang="ts">
import { computed } from 'vue'
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
  { state: 'READY', x: 70, y: 60 },
  { state: 'LISTENING', x: 240, y: 60 },
  { state: 'PROMPTING', x: 430, y: 60 },
  { state: 'PAUSED', x: 240, y: 190 },
  { state: 'TERMINATED', x: 430, y: 190 },
]
const nodeByState = Object.fromEntries(nodes.map((node) => [node.state, node])) as Record<SessionState, { state: SessionState; x: number; y: number }>
const edges: Array<{ from: SessionState; to: SessionState; curve?: boolean }> = [
  { from: 'READY', to: 'LISTENING' },
  { from: 'LISTENING', to: 'PROMPTING' },
  { from: 'PROMPTING', to: 'LISTENING', curve: true },
  { from: 'LISTENING', to: 'PAUSED' },
  { from: 'PROMPTING', to: 'PAUSED' },
  { from: 'PAUSED', to: 'LISTENING', curve: true },
  { from: 'READY', to: 'TERMINATED', curve: true },
  { from: 'LISTENING', to: 'TERMINATED', curve: true },
  { from: 'PROMPTING', to: 'TERMINATED' },
  { from: 'PAUSED', to: 'TERMINATED', curve: true },
]

const currentState = computed<SessionState>(() => props.session?.current_state ?? props.session?.state ?? 'READY')
const previousState = computed<SessionState | null>(() => props.session?.previous_state ?? null)
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
function edgePath(edge: { from: SessionState; to: SessionState; curve?: boolean }) {
  const start = nodeByState[edge.from]
  const end = nodeByState[edge.to]
  const dx = end.x - start.x
  const dy = end.y - start.y
  const length = Math.hypot(dx, dy)
  const radius = 31
  const sx = start.x + dx / length * radius
  const sy = start.y + dy / length * radius
  const ex = end.x - dx / length * radius
  const ey = end.y - dy / length * radius
  if (!edge.curve) return `M ${sx} ${sy} L ${ex} ${ey}`
  const bend = edge.from === 'READY' ? 34 : 24
  const cx = (sx + ex) / 2 - dy / length * bend
  const cy = (sy + ey) / 2 + dx / length * bend
  return `M ${sx} ${sy} Q ${cx} ${cy} ${ex} ${ey}`
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
    <div class="state-monitor__graph" role="img" aria-label="五状态机状态关系图">
      <svg viewBox="0 0 510 250" focusable="false" aria-hidden="true">
        <defs>
          <marker id="state-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" fill="currentColor" />
          </marker>
        </defs>
        <path
          v-for="edge in edges"
          :key="`${edge.from}-${edge.to}`"
          :d="edgePath(edge)"
          class="state-monitor__edge"
          :class="{ 'state-monitor__edge--active': isActiveEdge(edge.from, edge.to) }"
          marker-end="url(#state-arrow)"
        />
        <g v-for="node in nodes" :key="node.state" :transform="`translate(${node.x} ${node.y})`" class="state-monitor__node" :class="{ 'state-monitor__node--current': isCurrent(node.state) }">
          <circle r="26" />
          <circle v-if="isCurrent(node.state)" r="31" class="state-monitor__node-ring" />
          <text y="5" text-anchor="middle">{{ labels[node.state] }}</text>
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
.state-monitor__graph svg{width:100%;max-height:260px;color:var(--muted)}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:12px}.section-heading h2{font-size:16px;margin:0}.state-monitor__transition{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:14px 0;font-size:13px}.state-monitor__trigger{color:var(--muted)}.state-monitor__edge{fill:none;stroke:var(--line);stroke-width:1.5}.state-monitor__edge--active{stroke:var(--accent);stroke-width:3}.state-monitor__node circle{fill:var(--soft);stroke:var(--line);stroke-width:1.5}.state-monitor__node text{fill:var(--text);font-size:12px}.state-monitor__node--current circle{fill:var(--cursor-reading);stroke:var(--accent);stroke-width:2}.state-monitor__node .state-monitor__node-ring{fill:none;stroke:var(--accent);stroke-dasharray:4 4}.state-monitor__history h3{font-size:13px}.state-monitor__history ol{list-style:none;margin:0;padding:0;display:grid;gap:8px}.state-monitor__history li{display:grid;grid-template-columns:30px 120px 1fr 80px;gap:8px;font-size:12px}.state-monitor__history-trigger,.state-monitor__history time,.input-help{color:var(--muted)}.input-help{font-size:12px}.state-monitor__current{padding:3px 10px;background:var(--soft);border-radius:6px;font-size:12px}@media(max-width:600px){.state-monitor__history li{grid-template-columns:25px 1fr}.state-monitor__history li time{grid-column:2}}
</style>
