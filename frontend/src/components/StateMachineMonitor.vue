<script setup lang="ts">
import { computed } from 'vue'
import type { SessionSnapshot, SessionState, TransitionRecord } from '../api/session'

const props = defineProps<{ session?: SessionSnapshot }>()

const labels: Record<SessionState, string> = {
  READY: '就绪',
  LISTENING: '识别',
  PROMPTING: '播报',
  PAUSED: '暂停',
  TERMINATED: '终止',
}
const stateOrder: SessionState[] = ['READY', 'LISTENING', 'PROMPTING', 'PAUSED', 'TERMINATED']
const nodes: Array<{ state: SessionState; x: number; y: number }> = [
  { state: 'READY', x: 88, y: 50 },
  { state: 'LISTENING', x: 275, y: 50 },
  { state: 'PROMPTING', x: 462, y: 50 },
  { state: 'PAUSED', x: 275, y: 140 },
  { state: 'TERMINATED', x: 462, y: 140 },
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

const currentState = computed<SessionState>(() => props.session?.state ?? 'READY')
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
function point(node: { x: number; y: number }, endpoint: 'start' | 'end') {
  const radius = 26
  return { x: node.x + (endpoint === 'start' ? radius : -radius), y: node.y }
}
function edgePath(edge: { from: SessionState; to: SessionState; curve?: boolean }) {
  const start = point(nodeByState[edge.from], 'start')
  const end = point(nodeByState[edge.to], 'end')
  if (!edge.curve) return `M ${start.x} ${start.y} L ${end.x} ${end.y}`
  const midX = (start.x + end.x) / 2
  const offset = start.y === end.y ? -32 : 20
  return `M ${start.x} ${start.y} Q ${midX} ${start.y + offset} ${end.x} ${end.y}`
}
function formatTime(value: string) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString('zh-CN', { hour12: false })
}
function triggerLabel(value: string) {
  const labelsByTrigger: Record<string, string> = {
    session_initialized: '会话初始化',
    user_speech_started: '检测到人声',
    silence_threshold_reached: '达到静音阈值',
    prompt_audio_finished: '提示音播放完成',
    user_paused: '用户暂停',
    user_resumed: '用户继续',
    speech_interrupted_prompt: '朗读打断播报',
    ws_disconnected: '连接断开',
    user_terminated: '用户结束',
  }
  return labelsByTrigger[value] ?? value || '—'
}
</script>

<template>
  <section class="state-monitor" aria-labelledby="state-monitor-title">
    <div class="section-heading">
      <div>
        <p class="eyebrow">会话诊断</p>
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
        <time v-if="transitionAt" :datetime="transitionAt">{{ formatTime(transitionAt) }}</time>
      </template>
      <span v-else class="state-monitor__empty">尚未连接会话</span>
    </div>

    <div class="state-monitor__graph" role="img" aria-label="五状态机状态关系图">
      <svg viewBox="0 0 550 190" focusable="false" aria-hidden="true">
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
      <p class="state-monitor__legend">箭头表示允许的状态转换；高亮节点为当前状态，高亮箭头为最近一次转换。</p>
    </div>

    <div class="state-monitor__history">
      <h3>最近流转</h3>
      <p v-if="!history.length" class="empty-state">会话开始后显示状态变化。</p>
      <ol v-else aria-label="最近状态流转记录">
        <li v-for="(entry, index) in history" :key="`${entry.timestamp}-${index}`">
          <span class="state-monitor__sequence">#{{ history.length - index }}</span>
          <span>{{ entry.from_state ? labels[entry.from_state] : '—' }} → {{ labels[entry.to_state] }}</span>
          <span class="state-monitor__history-trigger">{{ triggerLabel(entry.trigger) }}</span>
          <time :datetime="entry.timestamp">{{ formatTime(entry.timestamp) }}</time>
        </li>
      </ol>
    </div>
  </section>
</template>
