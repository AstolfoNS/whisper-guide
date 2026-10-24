import type { SpeechFallbackReason } from '../api/session'

const stages: Record<string, string> = {
  configuration: '配置检查', capacity: '并发容量', connect: '连接握手', initialize: '服务初始化',
  send: '发送音频', result: '等待识别结果', receive: '接收识别结果', buffer: '音频缓冲', closed: '连接关闭',
}
const errors: Record<string, string> = {
  TIMEOUT: '请求超时', HTTP_ERROR: '云端语音请求失败', PROVIDER_ERROR: '云端语音服务返回错误',
  UPSTREAM_ERROR: '云端语音服务返回错误', PROTOCOL_ERROR: '语音协议响应无效',
  CONFIG_INCOMPLETE: '云端语音配置不完整', NETWORK_ERROR: '云端语音连接失败', UNKNOWN_ERROR: '云端语音不可用',
}
const safeInteger = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0

/** Only known labels and numeric diagnostics are displayed; never upstream text, keys or URLs. */
export function speechFallbackMessage(reason?: SpeechFallbackReason | null): string {
  if (!reason) return ''
  const stage = typeof reason.stage === 'string' && Object.hasOwn(stages, reason.stage) ? stages[reason.stage] : ''
  const error = typeof reason.error_code === 'string' && Object.hasOwn(errors, reason.error_code)
    ? errors[reason.error_code] : '云端语音不可用'
  const message = reason.error_code === 'TIMEOUT' && stage ? `${stage}超时` : error
  const details: string[] = []
  if (reason.error_code !== 'TIMEOUT' && stage) details.push(`阶段：${stage}`)
  if (safeInteger(reason.timeout_ms) && reason.timeout_ms! > 0) details.push(`${reason.timeout_ms} ms`)
  if (safeInteger(reason.http_status) && reason.http_status! >= 100 && reason.http_status! <= 599) details.push(`HTTP ${reason.http_status}`)
  if (safeInteger(reason.provider_error_code)) details.push(`业务码 ${reason.provider_error_code}`)
  return details.length ? `${message}（${details.join('，')}）` : message
}
