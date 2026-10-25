import { expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import StateMachineMonitor from '../../src/components/studio/StateMachineMonitor.vue'
import type { SessionSnapshot, SessionState } from '../../src/api/session'

// Kept explicit so removing a domain transition cannot silently simplify the drawing.
const transitions: Array<[SessionState, SessionState]> = [
  ['READY', 'LISTENING'], ['READY', 'TERMINATED'],
  ['LISTENING', 'PROMPTING'], ['LISTENING', 'PAUSED'], ['LISTENING', 'TERMINATED'],
  ['PROMPTING', 'LISTENING'], ['PROMPTING', 'PAUSED'], ['PROMPTING', 'TERMINATED'],
  ['PAUSED', 'LISTENING'], ['PAUSED', 'TERMINATED'],
]
type Point = { x: number; y: number }
type Segment = { start: Point; end: Point }
function points(value: string): Point[] {
  return value.split(/\s+/).map((point) => {
    const [x, y] = point.split(',').map(Number)
    return { x: x!, y: y! }
  })
}
function cross(a: Point, b: Point, c: Point) {
  return (b.x - a.x) * (c.y - a.y) - (b.y - a.y) * (c.x - a.x)
}
function onSegment(point: Point, segment: Segment) {
  return cross(segment.start, segment.end, point) === 0
    && point.x >= Math.min(segment.start.x, segment.end.x)
    && point.x <= Math.max(segment.start.x, segment.end.x)
    && point.y >= Math.min(segment.start.y, segment.end.y)
    && point.y <= Math.max(segment.start.y, segment.end.y)
}
function intersects(a: Segment, b: Segment) {
  const a1 = cross(a.start, a.end, b.start)
  const a2 = cross(a.start, a.end, b.end)
  const b1 = cross(b.start, b.end, a.start)
  const b2 = cross(b.start, b.end, a.end)
  return (a1 * a2 < 0 && b1 * b2 < 0)
    || onSegment(b.start, a) || onSegment(b.end, a)
    || onSegment(a.start, b) || onSegment(a.end, b)
}
function distanceToSegment(point: Point, segment: Segment) {
  const dx = segment.end.x - segment.start.x
  const dy = segment.end.y - segment.start.y
  const fraction = Math.max(0, Math.min(1,
    ((point.x - segment.start.x) * dx + (point.y - segment.start.y) * dy) / (dx * dx + dy * dy)))
  return Math.hypot(point.x - segment.start.x - fraction * dx, point.y - segment.start.y - fraction * dy)
}

it('draws every legal transition once, using only straight segments without crossings or shared segments', () => {
  const wrapper = mount(StateMachineMonitor)
  const edges = wrapper.findAll('.state-monitor__edge')
  expect(edges).toHaveLength(transitions.length)
  expect(edges.map((edge) => `${edge.attributes('data-from')}:${edge.attributes('data-to')}`).sort())
    .toEqual(transitions.map(([from, to]) => `${from}:${to}`).sort())
  const edgeSegments = edges.map((edge) => {
    expect(edge.element.tagName.toLowerCase()).toBe('polyline')
    const route = points(edge.attributes('points'))
    return route.slice(1).map((end, index) => ({ start: route[index]!, end }))
  })
  edgeSegments.forEach((segments, index) => {
    edgeSegments.slice(index + 1).forEach((others) => {
      segments.forEach((a) => others.forEach((b) => expect(intersects(a, b)).toBe(false)))
    })
  })
  wrapper.findAll('path').forEach((path) => expect(path.attributes('d')).not.toMatch(/[CQSTA]/i))
  wrapper.unmount()
})

it('keeps arrow ports and every segment outside all five state circles and focus rings', () => {
  const wrapper = mount(StateMachineMonitor)
  const nodes = wrapper.findAll('.state-monitor__node')
  expect(nodes).toHaveLength(5)
  const centers = nodes.map((node) => {
    const [x, y] = node.attributes('transform').match(/[\d.]+/g)!.map(Number)
    return { x: x!, y: y! }
  })
  wrapper.findAll('.state-monitor__edge').forEach((edge) => {
    const route = points(edge.attributes('points'))
    route.slice(1).forEach((end, index) => {
      centers.forEach((center) => expect(distanceToSegment(center, { start: route[index]!, end })).toBeGreaterThanOrEqual(31))
    })
  })
  wrapper.unmount()
})

it.each(transitions)('highlights only the actual %s → %s transition and its destination', (from, to) => {
  const wrapper = mount(StateMachineMonitor, { props: { session: {
    state: to, previous_state: from,
  } as SessionSnapshot } })
  const active = wrapper.findAll('.state-monitor__edge--active')
  expect(active).toHaveLength(1)
  expect(active[0]!.attributes('data-from')).toBe(from)
  expect(active[0]!.attributes('data-to')).toBe(to)
  const current = wrapper.findAll('.state-monitor__node--current')
  expect(current).toHaveLength(1)
  expect(current[0]!.attributes('data-state')).toBe(to)
  expect(active[0]!.attributes('marker-end')).toContain('state-arrow-active-')
  wrapper.unmount()
})

it('describes the chart without a live region and does not invent an initialization transition', async () => {
  const wrapper = mount(StateMachineMonitor)
  const svg = wrapper.get('svg')
  const title = wrapper.get('svg > title')
  const description = wrapper.get('svg > desc')
  expect(svg.attributes('role')).toBe('img')
  expect(svg.attributes('aria-labelledby')).toBe(`${title.attributes('id')} ${description.attributes('id')}`)
  expect(svg.attributes('aria-live')).toBeUndefined()
  expect(title.text()).toBe('五状态机状态关系图')
  expect(description.text()).toContain('全部十条合法迁移')
  expect(description.text()).toContain('当前状态：就绪')
  expect(wrapper.findAll('.state-monitor__edge--active')).toHaveLength(0)
  await wrapper.setProps({ session: { state: 'READY', previous_state: null,
    transition_trigger: 'session_initialized', transition_history: [],
  } as unknown as SessionSnapshot })
  expect(wrapper.text()).toContain('会话初始化')
  expect(wrapper.findAll('.state-monitor__edge--active')).toHaveLength(0)
  wrapper.unmount()
})
