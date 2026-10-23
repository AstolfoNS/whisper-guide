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

返回 `ok`、`latency_ms`、安全消息和 `verified`：`network` 表示完成真实协议请求；`capability` 仅表示本地模型能力可用；`none` 表示未通过。本地 STT 测试不验证识别准确率；云端 STT 测试向网关发送短静音 PCM 并验证响应，不等价于真实朗读验收。

更新必须携带当前 `revision`，冲突为 `409 REVISION_CONFLICT`。激活遇到 READY/LISTENING/PROMPTING/PAUSED 为 `409 SESSION_ACTIVE`，不能通过暂停绕过；同一锁保护会话准备与激活。默认本地方案只读；当前生效方案不可删除。选中、保存、测试、激活四种动作互不隐式替代。

API 接受平面模型字段，也兼容嵌套 `stt`/`llm`；显式平面字段优先。云端 STT 使用 `cloud_stt_endpoint`、`cloud_stt_app_id`、`cloud_stt_access_token`，与语义 `llm_*` 分开。API Key 与 Token 只单向提交，GET 不回传明文。空白或 `****` 保留旧值；`clear_llm_api_key=true` / `clear_cloud_stt_access_token=true` 才清除。不得把掩码当作密码输入的实际值。

## 持久化与旧接口

全部方案及独立生效快照保存于 `WHISPER_GUIDE_DATA_DIR/.model-configs.json`，使用 `0600` 权限和原子替换。首次加载旧 `.model-settings.json` 生成“现有配置”；新仓储落盘后，以其生效快照为准，待激活草稿不会在重启时意外生效。文件损坏时禁止静默覆盖。

`GET/PUT /api/settings/models` 和 `POST /api/settings/models/test` 保留兼容。旧 GET 读取实际生效值；旧 PUT 明确保存并激活，也受未终止会话保护；旧 test 仍测试 LLM。新界面使用多方案接口。

两类私有文件都含凭据，不得进入版本库、镜像、发布包或日志。SQLite 讲稿备份不包含它们。浏览器不持久化 API Key/Token。

## 断线恢复

`GET /api/sessions/recoverable` 仅列出无连接的未终止会话元数据。`POST /api/sessions/{id}/terminate` 是用户明确结束操作，已被其他连接接管返回 409，不存在返回 404。终止通过领域事件增加 generation、取消异步任务并清空内存队列，不保存音频。协议及使用说明见 [运维手册](operations.md)。

云端 STT 发送音频片段，文本 LLM 只发送讲稿窗口及转写。麦克风 PCM 和 TTS MP3 不写 SQLite、临时文件或日志。真实厂商账号仍需有效凭据和独立验收。
