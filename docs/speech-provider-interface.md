# 语音与语义 Provider 接口

语音识别与大模型语义校准独立配置。两者均不能直接修改 `reading_position` 或 `prompt_position`；本地 `ScriptAligner` 是游标提交入口。

```text
PCM16 → VAD → LocalSherpaProvider / HttpStreamingProvider
                         ↓ RecognitionUpdate(partial/final)
                     ScriptAligner → 双游标 / 五状态机
                         ↑ 经本地验证的讲稿原文
                 LLMSemanticRefiner（可选 OpenAI 兼容接口）
```

## STT 统一事件

```python
@dataclass(frozen=True, slots=True)
class RecognitionUpdate:
    text: str
    is_final: bool
    segment_id: int
    is_filler_only: bool
```

同一 `segment_id` 的 partial 覆盖前次假设；final 后重置流并递增分段号。填充词不提交讲稿位置。默认 `local` 使用 `sherpa-onnx`；`cloud` 需配置通用网关与 `cloud_opt_in=true`，未授权时锁定本地。

通用 HTTP 语音网关接收：

```json
{"audio":"<base64 PCM16>","sample_rate":16000,"segment_id":0,"turn_token":3}
```

返回 `text` / `transcript`、`is_final` / `final`，可选 `segment_id`。这不是任一厂商的原生协议；需要 WebSocket 或专用鉴权的厂商可通过网关或独立适配器转换。不得将凭据放进 URL 查询参数。

兼容保留 `WHISPER_GUIDE_STT_PROVIDER_CONFIGS` 注册表；主要工作台使用 `local` / `cloud` 及模型设置 API。`GET /api/speech/providers` 返回能力与隐私信息，不包含密钥。请求失败、超时或断网时回退本地，返回仍经过本地讲稿对齐和轮次校验。

## 通用语义校准

`LLMSemanticRefiner` 使用 `httpx.AsyncClient` 调用 OpenAI 兼容 `POST {base_url}/chat/completions`。主机根地址会补 `/v1`，已含路径的 Base URL 直接追加 `/chat/completions`；也可填写完整 endpoint。

输入仅包含讲稿窗口与粗糙转写；输出约束为：

```json
{"matched_quote":"讲稿窗口内的连续原文","confidence":0.92}
```

返回必须是窗口中的连续原文，置信度有效，且任务仍属于当前会话轮次；随后由本地对齐器确认位置。`off` 不调用；`fallback` 用于本地低置信度；`hybrid` 在有效句末异步校准。超时默认 1500 ms，错误或无效输出静默保留本地结果。

内置预设为 `custom`、`deepseek`、`qwen`、`doubao`、`openai`、`ollama`。预设只填充推荐 URL 和示例模型名，用户可自由改写模型名与地址；它们不证明真实账号、配额或模型访问权限已经可用。

## 运行时模型配置 API

| 接口 | 行为 |
|---|---|
| `GET /api/settings/models` | 返回当前配置、预设、固定密钥掩码 `****` 与 `has_*` / `*_configured` 标记 |
| `PUT /api/settings/models` | 验证配置副本，原子持久化后激活；新会话使用最新设置 |
| `POST /api/settings/models/test` | 对临时配置发送极简 LLM 请求，返回 `ok`、`latency_ms`、安全错误消息；不改变配置 |

前端使用平面字段；API 同时接受嵌套 `stt` / `llm` 对象。平面字段显式提供时优先。可配置字段：

```json
{
  "stt_provider": "local",
  "cloud_opt_in": false,
  "cloud_stt_endpoint": "",
  "cloud_stt_app_id": "",
  "cloud_stt_access_token": "",
  "llm_align_mode": "off",
  "llm_provider_preset": "custom",
  "llm_base_url": "",
  "llm_model": "",
  "llm_api_key": "",
  "llm_timeout_ms": 1500
}
```

API Key 与 Token 只向后端单向提交，GET 不回传明文，空白或 `****` 保留已有密钥。配置保存于 `WHISPER_GUIDE_DATA_DIR/.model-settings.json`（`0600`），重启优先加载其中模型字段；此文件含服务端凭据，不得提交版本库或进入发布包。浏览器不将密钥保存到 localStorage。

云端 STT 会发送音频片段；语义 LLM 仅发送讲稿窗口和转写。麦克风 PCM 和 TTS MP3 均不写入 SQLite、临时文件或日志。真实云端服务仍需用户提供有效凭据并执行账号联调。
