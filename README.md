# Whisper Guide

实时讲稿追踪与语音提示工作台。第一版需求基线为 [AC-01 至 AC-27](docs/requirements-v1.md)。

## 页面与使用

前端使用 Vue 3、TypeScript、Naive UI、Vue Router 和 Pinia，分为三个视图：

| 地址 | 用途 |
|---|---|
| `/`（`/dashboard` 同样可用） | 最近讲稿、当前生效模型方案与快捷入口 |
| `/studio` | 讲稿选择、实时提词、会话控制、识别对比、五状态机诊断 |
| `/settings/models` | 模型方案的新建、复制、编辑、测试、激活与删除 |

工作台以大字号讲稿为主，分别标记已确认朗读位置和下一次提示起点；raw partial/final 与讲稿校准结果独立显示。顶栏统一显示服务状态，诊断面板区分后端能力、连接、麦克风和实际播放结果。五状态图用不交叉、不共线重叠的直线/折线展示全部合法迁移，并高亮最近一次变化。支持亮色、暗色、跟随系统及全键盘操作。

**离开工作台会立即停止浏览器采集与播放，并请求暂停会话；返回后点击“继续”。** 路由切换保留同一会话及双游标，不自动恢复麦克风。连接异常时保留本地进度并明确提示重连。浏览器刷新后的断线会话可在工作台恢复或明确结束；服务重启仍会清空实时会话。

静音等待默认 `3000 ms`，允许不小于 `1000 ms` 的整数。麦克风就绪且持续传输静音帧时，识别状态在等待到期后自动切换为播报；提示播完后重新开始等待并提示下一句。麦克风断流与定位不可靠时保留暂停/禁止自动提示保护，详见[静音与状态图验收记录](docs/silence-state-2026-10-24.md)。快捷键为 `Alt+Shift+P` 暂停/继续、`Alt+Shift+I` 打断、`Alt+Shift+E` 结束、`Alt+Shift+H` 手动提示；编辑表单时不会触发会话快捷键。

## 处理链路

```text
浏览器内存 PCM16 → WebRTC VAD → SpeechProvider → RecognitionUpdate(partial/final)
                                                       ↓
                               本地 ScriptAligner → 双游标与五状态机 → edge-tts 内存 MP3
                                                       ↑
                              可选 LLMSemanticRefiner（OpenAI 兼容文本接口）
```

- 同一分段 partial 覆盖显示，final 才正常提交位置；静音截止前结算未定稿文本，填充词不触发失配。
- `ScriptAligner` 结合字面、拼音/近音、游标邻近与上下文，保持英文单词及数字边界。
- STT 默认本地 `sherpa-onnx`；云端可选 HTTP JSON 网关或通用 WebSocket 传输配合火山流式 ASR 协议适配器。云端 STT 与文本 LLM 独立配置，厂商预设仅帮助填表；它们不能直接指定游标偏移。
- 活跃会话、PCM 有界队列与提示音频仅在内存；讲稿保存在 SQLite。后端必须单个 Uvicorn worker。

## 目录

```text
whisper-guide/
├── backend-core/
│   ├── app/api/                 # HTTP、WebSocket、模型管理、断线会话恢复
│   ├── app/domain/              # 五状态机、双游标、ScriptAligner
│   ├── app/infrastructure/      # speech / speech_websocket / speech_protocols / llm
│   ├── app/repositories/        # SQLite 讲稿、私有版本化模型方案
│   ├── app/config.py            # 环境默认值与兼容配置迁移
│   ├── scripts/                 # 模型下载与语音验证
│   └── models/                  # 瘦身后的本地 STT 模型（不入 Git）
├── frontend/src/
│   ├── layouts/                 # 统一导航与主题
│   ├── views/                   # Dashboard / Studio / ModelConfigs
│   ├── components/              # health / scripts / studio / models
│   ├── stores/                  # 可展示的跨页面状态
│   ├── services/                # 应用级会话资源管理、轻量健康轮询
│   ├── router/                  # 路由、离开工作台保护与焦点管理
│   ├── api/                     # HTTP / WebSocket 类型与客户端
│   ├── audio/                   # PCM 采集、内存播放与取消令牌
│   └── styles/                  # 亮暗色设计变量与基础布局
├── docs/
├── deploy/                      # 部署与离线打包脚本
├── scripts/                     # SQLite 备份入口
├── Dockerfile
└── docker-compose.yml
```

## 本地启动

在 WSL Linux 路径下使用 Python 3.12、`uv`、Node.js `^22.18.0` 或 `>=24.12.0` 与 `pnpm`。不要混用 Windows 与 WSL 虚拟环境或 node_modules。

```bash
cd backend-core
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

另开终端：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

浏览器打开 `http://localhost:5173`。开发和预览服务代理 `/api` 与 WebSocket 到后端 `8000`；采音需要 localhost 或 HTTPS。

## 管理模型方案

在 `/settings/models` 新建方案，选择本地/云端 STT，再独立设置 `off/fallback/hybrid` 语义校准、服务商预设、Base URL、模型名、API Key 和超时。

1. **保存**只更新方案草稿，不改变生效版本。
2. **测试**对当前表单发起临时请求，不保存、不激活；可以分别测试 STT 与 LLM。 LLM 失败显示安全错误类别与 HTTP 状态，排查见[运维手册](docs/operations.md#大模型连通性测试失败)。
3. **激活**明确更新默认运行方案。任何未终止会话（含暂停）存在时，后端返回 `409 SESSION_ACTIVE`；请先结束或恢复断线会话。
4. **复制**只复制非敏感参数，密钥留空。默认本地方案受保护，当前生效方案不可删除。
5. 密码框留空保留已有凭据，输入新值替换，独立的清除操作才删除凭据；`****` 只是掩码。

填写 Base URL 与 API Key 后，点击**获取模型列表**，使用“筛选模型”和“从模型列表选择”填入真实模型 ID；已有密钥可留空复用。获取列表不要求先填写模型名称或开启对齐模式，不保存、不激活配置。选中后仍需测试调用权限；服务不提供列表时可以手动填写。操作与验收见 [模型发现记录](docs/model-discovery-2026-10-24.md)。

### 豆包流式 ASR + 方舟文本模型

同一方案可以同时配置两个云端环节：STT 选择 `cloud`、协议选择 `volcengine-ws`，填写 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`、语音资源 ID `volc.seedasr.sauc.duration`（ASR 2.0 小时版）及独立语音凭据，并开启音频云端授权。新 `api-key` 鉴权只需**语音服务 API Key**，无需 App ID；旧 `app-token` 模式仍需 App ID 与 Access Token。方舟文本 API Key 不会自动用于语音接口。

语义校准独立选择 `hybrid`，Base URL 填 `https://ark.cn-beijing.volces.com/api/v3`，通过“获取模型列表”选择账号实际可用的 Seed 模型，填写**方舟文本 API Key**。分别测试两个接口，通过后保存、结束已有会话并激活。语音超时独立于 LLM，默认 `5000 ms`，范围 `200–30000 ms`；二次识别 `enable_nonstream` 仅用于 `bigmodel_async`。

语音测试只发送内存短静音，等待最终协议响应；若返回已知空音频码 `45000002`，仅说明鉴权与协议可达，仍需在工作台真实朗读。本轮使用本地 WebSocket 模拟服务验证，未提供真实 ASR 凭据，尚未完成真实火山语音识别验收。

完整接口见 [Provider 与模型方案协议](docs/speech-provider-interface.md)。模型方案与生效快照原子保存于 `WHISPER_GUIDE_DATA_DIR/.model-configs.json`，权限 `0600`。旧 `.model-settings.json` 自动迁移为“现有配置”；新仓储存在后，以其生效快照为准。两种文件都含后端凭据，不能进入 Git、日志或发布包。浏览器不持久化密钥。

云端 STT 接收音频片段，语义 LLM 接收讲稿窗口和识别文本。本服务不保存录音；云端服务的数据处理政策需另行确认。预设不代表账号、配额或真实模型已经可用，需提供有效凭据后测试。

## 容器更新与启动

```bash
test -f .env || cp .env.example .env
mkdir -p data/sqlite data/backups
sudo chown -R 10001:10001 data/sqlite data/backups
sudo chmod -R u+rwX data/sqlite data/backups
docker compose config --quiet
docker compose build && docker compose up -d --force-recreate --wait --wait-timeout 180
```

访问 `http://localhost:8080`；直接刷新 `/studio` 或 `/settings/models` 由 Nginx 回退到同一前端入口。SQLite、模型方案与旧兼容配置持久化到 `./data/sqlite`，备份到 `./data/backups`；更新镜像保留这些目录。STT 核心模型内置镜像，后端始终单 worker。`edge-tts` 仍需服务器联网。

## 检查与验收

```bash
cd backend-core
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

```bash
cd frontend
pnpm test
pnpm typecheck
pnpm build
# 可选：首次安装浏览器后执行端到端验收
pnpm exec playwright install --with-deps chromium
pnpm test:e2e
```

本地真实 STT 和中文闭环此前已验收；本轮路由、会话资源生命周期、多配置及界面回归见 [工作台重构记录](docs/workbench-refactor-2026-10-24.md)。历史结果保留于 [验收报告](docs/acceptance-round-1.md)。真实云端账号、目标麦克风/耳机、长时设备与读屏软件组合仍需单独验收。

- [架构](docs/architecture.md)
- [部署](docs/deployment.md)
- [运维与恢复](docs/operations.md)
- [离线部署](DEPLOY.md)

当前没有用户认证和讲稿权限隔离，默认监听 localhost；公开配置接口前需设置 HTTPS、认证和访问控制。

原生流式语音与双云配置、协议边界、最新测试日志见[实现与验收记录](docs/cloud-asr-2026-10-24.md)。
