# 语音与语义 Provider 接口

语音识别与大模型语义校准独立配置。两者均不能直接修改 `reading_position` 或 `prompt_position`；本地 `ScriptAligner` 是游标提交入口。

```text
PCM16 → VAD → LocalSherpaProvider / HttpStreamingProvider / WebSocketStreamingProvider
                         ↓ RecognitionUpdate(partial/final)
                     AudioEvidence 分段声学证据
                         ↓
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
    start_ms: int | None = None
    end_ms: int | None = None
```

可选 `start_ms/end_ms` 为本连接 PCM 音频时间，完整有效区间用于匹配人声特征；不完整/缺失时使用近期活动及新增证据水位。已认可段的迟到修订/定稿仍可使用已锁存证据。原生 sherpa 的累计时钟不随 endpoint/reset 归零，云端回退仅保留受信任的连接时钟，外部相对时间不混用。

同一 `segment_id` 的 partial 覆盖前次假设，final 才正常提交位置。填充词不提交讲稿位置。本地 sherpa 端点后重置流并递增分段号；WebSocket 云端分句定稿只递增分段号，保留同一连接。默认 `local` 使用 `sherpa-onnx`；`cloud` 需选择语音协议、独立凭据与 `cloud_opt_in=true`，未授权时锁定本地。

通用 HTTP 语音网关接收：

```json
{"audio":"<base64 PCM16>","sample_rate":16000,"segment_id":0,"turn_token":3}
```

返回 `text` / `transcript`、`is_final` / `final`，可选 `segment_id`。此 `http-json` 网关契约与厂商原生协议分开；公网地址要求 HTTPS，本机开发可用环回 HTTP。不得将凭据放进 URL 查询参数。

兼容保留 `WHISPER_GUIDE_STT_PROVIDER_CONFIGS` 注册表；主要工作台使用 `local` / `cloud` 及模型设置 API。`GET /api/speech/providers` 返回能力与隐私信息，不包含密钥。请求失败、超时或断网时回退本地，返回仍经过本地讲稿对齐和轮次校验。

## 原生 WebSocket 流式 ASR

`WebSocketStreamingProvider` 管理通用有界传输，`VolcengineSpeechProtocol` 只负责火山 ASR v3 的二进制帧、鉴权头和转写事件。新增厂商协议可扩展适配器注册表，无需把语音源绑定到某个文本模型或让云端直接操作领域游标。网络实现使用轻量 `websockets`，不引入厂商 SDK。

推荐配置：

| 配置字段 | 值或规则 |
|---|---|
| `stt_provider` / `cloud_opt_in` | `cloud` / `true` |
| `cloud_stt_protocol` | `volcengine-ws`；旧网关使用默认 `http-json` |
| `cloud_stt_endpoint` | `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async` |
| `cloud_stt_auth_mode` | 新 `api-key`，或旧 `app-token` |
| `cloud_stt_access_token` | 新模式填独立语音 API Key；旧模式填语音 Access Token |
| `cloud_stt_app_id` | 仅旧 `app-token` 模式必需 |
| `cloud_stt_resource_id` | `volc.seedasr.sauc.duration`，ASR 2.0 小时版；须与已开通资源一致 |
| `cloud_stt_timeout_ms` | 独立语音超时，默认 5000，严格整数 `200–30000` |
| `cloud_stt_enable_nonstream` | 默认 `true`，仅 `bigmodel_async` 启用二次识别；其他端点忽略此开关 |

`api-key` 模式发 `X-Api-Key`；旧模式发 `X-Api-App-Key` 和 `X-Api-Access-Key`。两者都携带资源 ID 与请求标识。语音凭据与方舟文本 `llm_api_key` 独立，后端不自动借用或回传明文；创建/复制/测试仍遵守方案凭据隔离规则。

音频为 16 kHz、单声道、PCM16，每 200 ms 发送一块。分句尾部静音窗口为 600 ms，早于至少 1000 ms 的本地提示等待。请求 `result_type=single`，同一句每次返回的完整修订假设覆盖原 partial；只有 `utterances[].definite=true` 或服务端最终包定稿，绝不把修订文本当增量拼接。云端结果统一转为 `RecognitionUpdate`，继续经过填充词/异常叠字处理、本地 `ScriptAligner` 与可选文本 LLM `hybrid` 校准；最终游标由本地提交。

每条连接的音频缓冲最多 1 秒、结果队列最多 32 项，WebSocket 传输最多 4 个活跃线程。超时、断网或有界队列溢出触发本地 sherpa 回退；暂停、终止及轮次切换立即失效旧连接与队列，迟到结果不能推进游标。不落盘录音、音频帧、识别内容或密钥。公网只允许 WSS，本机开发允许环回 WS；禁止 URL 凭据、查询参数、片段及重定向，避免自定义鉴权头转发到其他地址。

`target=stt` 的测试使用当前草稿和独立语音超时，建立真实协议连接、发送短静音与结束包，并等到服务端最终包；初始 ACK 不能单独判定成功。已知空音频业务码 `45000002` 可返回 `ok=true, verified=network, probe_empty_audio=true`，固定提示只确认鉴权和协议可达。其他错误仍失败并以受控类别/数字状态码展示，绝不透传上游正文。测试不保存、不激活，也不允许本地回退伪装成云端通过。

本轮自动化验证使用本地 WebSocket 模拟服务，覆盖帧协议、partial/final、回退、取消与配置安全；本轮未使用真实语音 API Key，**未完成真实火山 ASR 准确率和生产账号验收**。本地 sherpa 此前真实识别结果不替代云端验收。

## 通用语义校准

`LLMSemanticRefiner` 使用 `httpx.AsyncClient` 调用 OpenAI 兼容 `POST {base_url}/chat/completions`。主机根地址会补 `/v1`，已含路径的 Base URL 直接追加 `/chat/completions`；也可填写完整 endpoint。

输入仅包含讲稿窗口与粗糙转写；输出约束为：

```json
{"matched_quote":"讲稿窗口内的连续原文","confidence":0.92}
```

返回必须是窗口中的连续原文，置信度有效，且任务仍属于当前会话轮次；随后由本地对齐器确认位置。`off` 不调用；`fallback` 用于本地低置信度；`hybrid` 在有效句末异步校准。超时默认 1500 ms，错误或无效输出静默保留本地结果。

内置预设为 `custom`、`deepseek`、`qwen`、`doubao`、`openai`、`ollama`。预设只填充推荐 URL 和示例模型名，用户可自由改写模型名与地址；它们不证明真实账号、配额或模型访问权限已经可用。

## 识别过滤诊断

会话快照新增 `recognition_filtered`、`recognition_filter_reason`、`recognition_filtered_count`。无声学支持的文本仍可在 `raw_transcript` 展示并保留自己的 `raw_transcript_is_final`，但不改变已接受假设、游标、可靠性、静音时钟或 LLM 任务。计数及单条过滤文本仅存于本轮有界内存，换轮清零，不写日志/SQLite。前端应将“已过滤”与“无法匹配讲稿”分开；过滤噪声不能自动恢复原已不可靠的位置。

完整规则、噪声能力边界与回归见[无声识别门控记录](noise-guard-2026-10-24.md)。

## 多方案管理 API

| 接口 | 行为 |
|---|---|
| `GET /api/settings/model-configs` | 方案列表、active_id/active_revision、脱敏的实际 active_profile、仓储可用状态 |
| `GET /api/settings/model-configs/{id}` | 单套方案草稿与修订号，凭据恒定掩码 |
| `POST /api/settings/model-configs` | 以 `{name,description,settings}` 创建独立草稿 |
| `PUT /api/settings/model-configs/{id}` | 以 `{revision,name?,description?,settings}` 更新草稿，不激活 |
| `POST /api/settings/model-configs/{id}/duplicate` | 复制非敏感参数，可传 `{name}`，不复制密钥 |
| `DELETE /api/settings/model-configs/{id}` | 删除非默认、非生效方案 |
| `POST /api/settings/model-configs/{id}/activate` | 显式激活，可传 `{revision}`；未终止会话存在时 409 |
| `POST /api/settings/model-configs/test` | 测试已保存方案或未保存表单；`target` 为 `stt` 或 `llm` |
| `POST /api/settings/model-configs/discover-models` | 使用已保存配置或当前草稿获取模型列表，不保存、不激活 |

列表中的 `configs` 是可编辑草稿；当 `pending_activation=true` 时，草稿参数与实际生效参数不同。顶栏、首页应读取独立 `active_profile`，不能把新草稿当作当前模型。

创建示例：

```json
{
  "name":"本地识别与文本兜底",
  "settings":{
    "stt_provider":"local",
    "cloud_opt_in":false,
    "llm_align_mode":"fallback",
    "llm_provider_preset":"custom",
    "llm_base_url":"https://gateway.example/v1",
    "llm_model":"your-model-name",
    "llm_api_key":"<通过密码框输入>",
    "llm_timeout_ms":1500
  }
}
```

测试示例（省略 `id` 时从不含凭据的本地默认方案开始）：

```json
{"id":"<profile-id>","target":"llm","settings":{"llm_model":"unsaved-model"}}
```

返回 `ok`、`latency_ms`、安全消息和 `verified`：`network` 表示完成真实协议请求；`capability` 仅表示本地模型能力可用；`none` 表示未通过。本地 STT 测试不验证识别准确率；云端 STT 按所选 HTTP/WebSocket 协议发送短静音 PCM 并验证响应，不等价于真实朗读验收。WebSocket 的空音频结果说明见上文。

LLM 测试失败时额外返回受控 `error_code`：`CONFIG_INCOMPLETE`、`TIMEOUT`、`HTTP_ERROR`、`NETWORK_ERROR`、`INVALID_RESPONSE` 或 `UNKNOWN_ERROR`。`HTTP_ERROR` 可包含 `http_status`；`message` 由本服务的固定诊断文案生成，不透传上游正文、异常或凭据。`verified=none` 表示未通过本次验证，界面明确显示失败。实际排查见 [运维手册](operations.md#大模型连通性测试失败)。

更新必须携带当前 `revision`，冲突为 `409 REVISION_CONFLICT`。激活遇到 READY/LISTENING/PROMPTING/PAUSED 为 `409 SESSION_ACTIVE`，不能通过暂停绕过；同一锁保护会话准备与激活。默认本地方案只读；当前生效方案不可删除。选中、保存、测试、激活四种动作互不隐式替代。

API 接受平面模型字段，也兼容嵌套 `stt`/`llm`；显式平面字段优先。云端 STT 使用 `cloud_stt_endpoint`、`cloud_stt_protocol`、`cloud_stt_auth_mode`、`cloud_stt_resource_id`、`cloud_stt_timeout_ms`、`cloud_stt_enable_nonstream` 及独立凭据，与语义 `llm_*` 分开。嵌套 `stt` 对应短字段为 `protocol`、`auth_mode`、`resource_id`、`timeout_ms`、`enable_nonstream`。API Key 与 Token 只单向提交，GET 不回传明文。空白或 `****` 保留旧值；`clear_llm_api_key=true` / `clear_cloud_stt_access_token=true` 才清除。不得把掩码当作密码输入的实际值。

## 模型发现

```json
{"id":"<可选的已保存方案 ID>","settings":{"llm_base_url":"https://ark.cn-beijing.volces.com/api/v3","llm_api_key":"<新输入或省略以复用已存密钥>"}}
```

不要求方案名称、`llm_model` 或启用对齐模式。省略 `id` 时使用无凭据的本地默认方案；传 `id` 时，空白/掩码密钥沿用该方案原凭据，明确清除标志则本次请求也不使用该密钥。没有鉴权要求的本地服务可以不填 API Key。

后端请求 `GET {api_root}/models`，主机根地址补 `/v1`；保留 `/api/v3`、`/compatible-mode/v1`，完整 `/chat/completions` 地址会先去掉该尾段。仅请求这个地址，不自动尝试其它端点或翻页，不跟随重定向。

返回示例：

```json
{"ok":true,"models":[{"id":"actual-model-id","name":"可选显示名"}],"latency_ms":479,"message":"模型列表已获取；选择后仍需测试模型调用权限。","truncated":false}
```

兼容 `data` 数组、裸数组及 Ollama 风格 `models` 数组，只输出规范化的 ID 和可选名称。条目去重，过滤控制字符与凭据回显；最多返回 500 个模型、扫描 5000 项，响应体最多 1 MiB，请求采用所配置超时且上限 30 秒。请求未压缩响应，服务强制返回压缩内容时按格式不兼容处理并提示手填。

空列表为 `ok=true`，不清除现有模型 ID；失败为 `ok=false`、`models=[]` 并返回受控诊断，额外支持 `RESPONSE_TOO_LARGE`。404/405 提示接口不可用时手填。列表可见不等于有生成权限，选择后必须单独测试连通性。

浏览器列表仅存在编辑页内存；修改 Base URL、输入密钥、清除标记、预设、切换方案或离开路由会取消旧请求并丢弃迟到结果。新地址会接收当前草稿或后端保留的凭据，页面明确提示目标地址。获取列表、选择模型、保存、激活是独立操作。

## 持久化与旧接口

全部方案及独立生效快照保存于 `WHISPER_GUIDE_DATA_DIR/.model-configs.json`，使用 `0600` 权限和原子替换。首次加载旧 `.model-settings.json` 生成“现有配置”；新仓储落盘后，以其生效快照为准，待激活草稿不会在重启时意外生效。文件损坏时禁止静默覆盖。

`GET/PUT /api/settings/models` 和 `POST /api/settings/models/test` 保留兼容。旧 GET 读取实际生效值；旧 PUT 明确保存并激活，也受未终止会话保护；旧 test 仍测试 LLM。新界面使用多方案接口。

两类私有文件都含凭据，不得进入版本库、镜像、发布包或日志。SQLite 讲稿备份不包含它们。浏览器不持久化 API Key/Token。

## 断线恢复

`GET /api/sessions/recoverable` 仅列出无连接的未终止会话元数据。`POST /api/sessions/{id}/terminate` 是用户明确结束操作，已被其他连接接管返回 409，不存在返回 404。终止通过领域事件增加 generation、取消异步任务并清空内存队列，不保存音频。协议及使用说明见 [运维手册](operations.md)。

云端 STT 发送音频片段，文本 LLM 只发送讲稿窗口及转写。麦克风 PCM 和 TTS MP3 不写 SQLite、临时文件或日志。真实厂商账号仍需有效凭据和独立验收。
