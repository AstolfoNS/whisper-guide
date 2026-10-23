# Whisper Guide

实时讲稿追踪与语音提示工作台。第一版需求基线见 [AC-01 至 AC-27](docs/requirements-v1.md)。

当前界面只保留讲稿库、会话与双游标、五状态机监控、模型与识别引擎配置。原生控件、键盘焦点与 `Alt+Shift` 会话快捷键继续可用。

## 处理链路

```text
浏览器 PCM16 → WebRTC VAD → SpeechProvider → RecognitionUpdate(partial/final)
                                                  ↓
                            ScriptAligner 讲稿约束对齐 → 双游标与五状态机 → edge-tts
                                                  ↑
                         可选 LLMSemanticRefiner（任意 OpenAI 兼容接口）
```

- 同一语音分段的 partial 覆盖显示，final 才正常提交位置；填充词不触发失配。
- `ScriptAligner` 结合字面、拼音/近音、游标邻近与上下文处理同音错字和重复句，仍是唯一游标提交入口。
- STT 默认使用本地 `sherpa-onnx`；通用云端接口与语义大模型独立配置，厂商预设仅用于填表。
- 活跃会话、PCM 队列与提示 MP3 均在内存中；讲稿保存在 SQLite。始终使用一个 Uvicorn worker。

## 目录

```text
whisper-guide/
├── backend-core/
│   ├── app/api/              # 讲稿、会话、模型配置 API
│   ├── app/domain/           # 五状态机、双游标、ScriptAligner
│   ├── app/infrastructure/   # speech.py 与通用 llm.py
│   ├── app/repositories/     # SQLite 讲稿仓储
│   ├── app/config.py         # 环境默认值、运行时模型配置持久化
│   ├── scripts/              # 模型下载、备份与验证
│   └── models/               # 已瘦身本地 STT 模型（不入 Git）
├── frontend/src/
│   ├── api/                  # HTTP / WebSocket 类型与客户端
│   ├── audio/                # PCM 采集、内存播放和取消令牌
│   └── components/           # 讲稿、会话、状态机、模型配置面板
├── docs/
├── deploy/                   # 部署和打包脚本
├── Dockerfile
└── docker-compose.yml
```

## 本地启动

在 WSL Linux 路径下使用 Python 3.12、`uv`、Node.js `^22.18.0` 或 `>=24.12.0`、`pnpm`。勿混用 Windows 与 WSL 的虚拟环境或 node_modules。

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

打开 `http://localhost:5173`。开发及预览服务器代理 `/api` 与 WebSocket 到后端 `8000`；浏览器采音需要 localhost 或 HTTPS。

静音等待阈值默认 `3000 ms`，最小 `1000 ms`；前端输入、会话接口与服务端默认配置统一校验。

## 模型与识别引擎配置

页面的“模型与识别引擎”支持：

- STT：`local` / `cloud`，Cloud Endpoint、App ID、Access Token、云端音频授权。
- 语义对齐：`off` / `fallback` / `hybrid`。
- 大模型：`custom`、`deepseek`、`qwen`、`doubao`、`openai`、`ollama` 预设；Base URL、自由模型名称、API Key、正整数超时毫秒。
- 测试连通性与保存配置。测试使用临时配置，不改变正在生效或保存的配置。

配置默认来自 `WHISPER_GUIDE_*` 环境变量；保存后原子写入后端数据目录 `.model-settings.json`（权限 `0600`），重启会加载已保存的模型字段并覆盖环境默认值。该文件含后端凭据，应与 `.env` 一样保护。GET 只返回密钥存在标记和固定 `****`；密码框留空保留原密钥。云端 STT 未授权时强制使用本地识别。

真实云端服务需自行提供可用 Endpoint、模型名和凭据。预设的 URL/模型名只是可修改示例，不代表账号已开通或已完成真实云端联调。LLM 仅接收讲稿窗口和识别文本，云端 STT 接收音频片段；本项目不保存用户录音。

## 容器启动

```bash
cp .env.example .env
mkdir -p data/sqlite data/backups
docker compose build
docker compose up -d
```

打开 `http://localhost:8080`。镜像内置已验证的 `backend-core/models/` 核心模型，SQLite 与 `.model-settings.json` 持久化到 `./data/sqlite`，备份到 `./data/backups`。容器仅使用一个 worker。离线发布及权限修复见 [部署说明](docs/deployment.md) 和 [DEPLOY.md](DEPLOY.md)。

## 验证

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
```

本地真实 STT 模型与中文闭环已完成此前验收；本轮新增模型配置、语义对齐、状态监控与前端简化，最终回归结果记录在 [验收报告](docs/acceptance-round-1.md)。实际云端账号、目标麦克风/耳机和读屏设备需单独验收。

- [架构说明](docs/architecture.md)
- [语音与语义 Provider 协议](docs/speech-provider-interface.md)
- [部署说明](docs/deployment.md)
- [运维手册](docs/operations.md)

当前没有用户认证与讲稿权限隔离，默认绑定 localhost；公开部署模型配置接口前需配置 HTTPS、认证与访问控制。`edge-tts` 合成需要服务器联网。
