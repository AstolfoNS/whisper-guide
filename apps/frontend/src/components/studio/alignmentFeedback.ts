import type { SessionSnapshot } from '../../api/session'

export function recoveryFeedback(snapshot: SessionSnapshot): string {
  if (snapshot.state !== 'LISTENING') return ''
  const suffix = snapshot.silence_prompt_policy === 'saved_position'
    ? '；静音等待到期后从已保存的提示起点播报。' : '；自动提示暂缓。'
  switch (snapshot.recovery_status) {
    case 'searching': return `正在尝试自动恢复讲稿位置，请继续朗读更完整的一段${suffix}`
    case 'ambiguous': return `讲稿中有多处相似内容，请继续朗读以区分位置${suffix}`
    case 'confirming': return `已找到候选位置，正在等待后续朗读确认${suffix}`
    default: return ''
  }
}
