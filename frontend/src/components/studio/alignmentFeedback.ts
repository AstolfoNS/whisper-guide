import type { SessionSnapshot } from '../../api/session'

export function recoveryFeedback(snapshot: SessionSnapshot): string {
  if (snapshot.state !== 'LISTENING') return ''
  switch (snapshot.recovery_status) {
    case 'searching': return '正在尝试自动恢复讲稿位置，请继续朗读更完整的一段；自动提示暂缓。'
    case 'ambiguous': return '讲稿中有多处相似内容，请继续朗读以区分位置；自动提示暂缓。'
    case 'confirming': return '已找到候选位置，正在等待后续朗读确认；自动提示暂缓。'
    default: return ''
  }
}
