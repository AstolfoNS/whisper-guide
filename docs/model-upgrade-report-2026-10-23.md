# 通用模型、运行时配置与状态监控升级验收

日期：2026-10-23。Python 3.12，WSL Ubuntu。以下结论对应本次代码与自动化检查，真实云端账号尚未提供。

## 功能与安全边界

- STT 使用 local / 通用云端 JSON 网关；语义 LLM 使用可修改的 OpenAI 兼容 Base URL 和 Model，两者独立配置。
- 通用 LLM 支持 off / fallback / hybrid；仅发送当前讲稿窗口和识别文本，返回连续原文子串与置信度。子串校验、0.68 置信度门槛、generation 与识别修订号检查之后，仍交由本地 ScriptAligner 提交游标。
- partial 仅覆盖界面并取消等待；final 或实际静音截止时的结算才进入定位。补充低音量 VAD 漏判测试，避免静音时重复 partial 重置时钟。
- 云端 STT 合并 400 ms 音频块；每流一个请求、最多一秒待发 PCM，后台网络工作线程最多四个。本地流持续预热；失败后映射分段 ID，暂停/结束丢弃过期结果。所有录音只在有界内存中处理。
- 五状态机、事件优先级、双游标、音频取消令牌及单 worker 部署保留。新增元数据不改变原有状态图。
- 界面保留讲稿库、会话控制与双游标、状态拓扑、可折叠模型设置四个模块；保留原生控件、键盘快捷键和 aria 状态反馈。

## 模型配置 API 与字段

| 接口 | 行为 |
| --- | --- |
| GET /api/settings/models | 返回生效设置、六个预设和密钥是否配置；密钥提示固定为 **** |
| PUT /api/settings/models | 验证后原子保存并激活；空值/脱敏密钥保留旧密钥 |
| POST /api/settings/models/test | 使用本次表单临时配置测试 LLM，返回 ok、latency_ms、message，不写入配置 |

STT：stt_provider、cloud_stt_endpoint、cloud_stt_app_id、cloud_stt_access_token、cloud_opt_in。

LLM：llm_align_mode、llm_provider_preset、llm_base_url、llm_model、llm_api_key、llm_timeout_ms（默认 1500，严格正整数）。

预设：custom、deepseek、qwen、doubao、openai、ollama。预设只是可修改的示例，模型可用性取决于账号与服务。

配置保存在 DATA_DIR/.model-settings.json，权限 0600；容器对应宿主机 data/sqlite/.model-settings.json。已加入 Git 与 Docker 忽略规则。启动时读取并覆盖环境默认值。保存会刷新 Provider 注册表，下一新会话使用更新后的 STT；活动会话的音频流不在中途替换。模型面板在活动会话中锁定。

管理接口无独立身份认证，沿用本地可信工作台部署边界；远程使用需在网关配置 HTTPS、认证与访问控制。云端音频需要显式授权，外部 STT 端点使用 HTTPS。

## 状态节点、连线与广播

节点：READY（就绪）、LISTENING（识别）、PROMPTING（提示）、PAUSED（暂停）、TERMINATED（终止）。

十条合法连线：

1. READY → LISTENING
2. READY → TERMINATED
3. LISTENING → PROMPTING
4. LISTENING → PAUSED
5. LISTENING → TERMINATED
6. PROMPTING → LISTENING
7. PROMPTING → PAUSED
8. PROMPTING → TERMINATED
9. PAUSED → LISTENING
10. PAUSED → TERMINATED

PAUSED 恢复时先进入 LISTENING；不直接恢复播放，防止旧音频/旧计时回调推进游标。

WebSocket 同步 current_state、previous_state、transition_trigger、transition_at（UTC ISO 8601）、transition_history（最多 10 条）、allowed_transitions。监控面板高亮当前节点和上一跳有向边，并展示原因、时间与有界日志。

## 全量验证

| 检查 | 结果 |
| --- | --- |
| uv sync --frozen --offline --python 3.12 | 依赖与锁文件同步通过 |
| uv run --frozen ruff check . | 通过，零错误 |
| uv run --frozen ruff format --check . | 36 个文件已符合格式 |
| uv run --frozen pytest -q | 143 passed，零失败、零跳过 |
| pnpm test | 14 passed，零失败、零跳过 |
| pnpm typecheck | 通过 |
| pnpm build | 通过；CSS 9.77 kB，JS 113.12 kB |
| docker compose config --quiet | 通过，新增环境变量可透传 |
| git diff --check | 通过 |

pytest 有一条上游 Starlette TestClient/httpx 弃用提示，不影响用例结果；未为消除提示增加新客户端依赖。

日志：[后端](validation/2026-10-23-backend.txt)、[前端](validation/2026-10-23-frontend.txt)。

新增用例覆盖模型配置读取/脱敏/持久化/重启、不同 URL 与自由模型名、OpenAI 兼容请求、总超时、非法原文/置信度、旧轮次丢弃、本地回退与连续分段、WebSocket 状态历史、前端拓扑渲染及表单请求。云端测试使用模拟 HTTP 响应，未宣称已完成任何付费云端账号或真实语音质量验收。

## 完整文件清单

- 修改：`.dockerignore`
- 修改：`.env.example`
- 修改：`.gitignore`
- 修改：`DEPLOY.md`
- 修改：`README.md`
- 修改：`backend-core/.env.example`
- 修改：`backend-core/README.md`
- 修改：`backend-core/app/api/scripts.py`
- 修改：`backend-core/app/api/session.py`
- 新增：`backend-core/app/api/settings.py`
- 修改：`backend-core/app/config.py`
- 修改：`backend-core/app/domain/session.py`
- 修改：`backend-core/app/domain/tracking.py`
- 修改：`backend-core/app/infrastructure/__init__.py`
- 修改：`backend-core/app/infrastructure/llm.py`
- 修改：`backend-core/app/infrastructure/speech.py`
- 修改：`backend-core/app/main.py`
- 修改：`backend-core/app/speech.py`
- 修改：`backend-core/scripts/download_asr_model.py`
- 新增：`backend-core/tests/test_llm_protocol.py`
- 新增：`backend-core/tests/test_llm_settings.py`
- 新增：`backend-core/tests/test_provider_fallback.py`
- 修改：`backend-core/tests/test_runtime.py`
- 新增：`backend-core/tests/test_semantic_runtime.py`
- 修改：`backend-core/tests/test_session.py`
- 修改：`backend-core/tests/test_speech.py`
- 修改：`backend-core/tests/test_tracking.py`
- 新增：`backend-core/tests/test_transition_monitor.py`
- 修改：`backend-core/uv.lock`
- 修改：`docker-compose.yml`
- 修改：`docs/acceptance-round-1.md`
- 修改：`docs/architecture.md`
- 修改：`docs/deployment.md`
- 新增：`docs/model-upgrade-report-2026-10-23.md`
- 修改：`docs/operations.md`
- 修改：`docs/speech-provider-interface.md`
- 新增：`docs/validation/2026-10-23-backend.txt`
- 新增：`docs/validation/2026-10-23-frontend.txt`
- 修改：`frontend/README.md`
- 修改：`frontend/pnpm-workspace.yaml`
- 修改：`frontend/src/App.vue`
- 修改：`frontend/src/api/models.ts`
- 修改：`frontend/src/api/session.ts`
- 修改：`frontend/src/components/ModelConfigPanel.vue`
- 删除：`frontend/src/components/ProviderSettings.vue`
- 修改：`frontend/src/components/ScriptLibrary.vue`
- 修改：`frontend/src/components/SessionPanel.vue`
- 修改：`frontend/src/components/StateMachineMonitor.vue`
- 修改：`frontend/src/style.css`
- 新增：`frontend/tests/components.test.mjs`
- 新增：`frontend/tests/models.test.mjs`
