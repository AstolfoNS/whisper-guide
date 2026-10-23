# Speech Provider 接口

Whisper Guide 的语音输入由 Provider 负责把音频转换为统一的 `RecognitionUpdate`，再交给本地 `ScriptAligner` 和五状态会话机。Provider 从不修改 `reading_position` 或 `prompt_position`，也不能绕过轮次令牌和 AC-27 的内存边界。

```text
浏览器 PCM16 → VAD → ProviderRegistry 选择 Provider
                         ├─ LocalSherpaProvider
                         └─ HttpStreamingProvider（任意后端注册的云端模型）
                              ↓
                    RecognitionUpdate(partial/final)
                              ↓
                  ScriptAligner → 双游标/提示状态机
```

## 统一契约

```python
@dataclass(frozen=True, slots=True)
class RecognitionUpdate:
    text: str
    is_final: bool
    segment_id: int
    is_filler_only: bool
```

同一 `segment_id` 内的 `partial` 是可修订的完整假设，服务端覆盖显示内容而不是增量拼接；端点产生 `final` 后 Provider 重置流并递增分段号。填充词只用于打断静音计时和更新界面，不提交讲稿游标。

## Provider 注册

后端通过 `WHISPER_GUIDE_STT_PROVIDER_CONFIGS` 注册远程 Provider。配置是 JSON 数组，每项至少包含 `id`、`label`、`endpoint` 和凭据；推荐使用 `access_token_env` 引用服务器环境变量，避免把密钥写进配置文件：

```dotenv
WHISPER_GUIDE_STT_PROVIDER=local
WHISPER_GUIDE_STT_PROVIDER_CONFIGS=[{"id":"my-model","label":"我的流式模型","endpoint":"https://gateway.example/v1/stt","model":"streaming-model","access_token_env":"MY_MODEL_TOKEN"}]
WHISPER_GUIDE_CLOUD_OPT_IN=false
```

远程网关接收 JSON：

```json
{"audio":"<base64 PCM16>","sample_rate":16000,"segment_id":0,"turn_token":3}
```

并返回 `text`（或 `transcript`）、`is_final`（或 `final`）和可选 `segment_id`。如果厂商使用 WebSocket、专用鉴权或不同消息格式，请在网关层或单独适配器中转换为该契约；核心代码不绑定任何厂商名称。

`GET /api/speech/providers` 只返回 Provider ID、标签、模型、能力和隐私说明，不返回 endpoint 中的 token。前端可以选择当前会话使用的 Provider，新增 Provider 仍由服务端环境变量完成。未显式 `WHISPER_GUIDE_CLOUD_OPT_IN=true` 时，远程 Provider 会显示为未配置并强制回退本地。

## 回退与会话安全

`local` 直接使用 `sherpa-onnx`。选择远程 Provider 或 `hybrid` 时，远程请求携带当前会话 generation 作为 `turn_token`；网络超时、HTTP 错误或会话打断会立即关闭远程流并回退本地。回退结果仍必须经过 `ScriptAligner`，旧 generation 的结果会被丢弃。

TTS 继续使用 `edge-tts`，音频只在内存中生成并通过 WebSocket 返回。麦克风 PCM 不写入 SQLite、临时文件或日志。