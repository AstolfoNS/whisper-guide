# Whisper Guide

面向盲人演讲者的实时语音追踪与提示网站。

第一版需求见 [需求文档](docs/requirements-v1.md)，包含就绪、识别、播报、暂停、终止五个状态，以及可配置的静音提示阈值。

## 当前第一版实现状态

- Vue 3、TypeScript、Vite 前端，提供无障碍基础布局、讲稿库和会话控制。
- Python 3.12、FastAPI 后端，提供健康检查、SQLite 讲稿库、五状态会话和 WebSocket 协议。
- 浏览器采集 16 kHz 单声道 PCM，服务端接入 sherpa-onnx、WebRTC VAD 和 edge-tts，提示音频只在内存中生成。
- 纯文本讲稿支持批量上传、持久保存、选择和全文预览；用户录音不持久保存。

第一版核心流程已经接入。第二阶段验收确认 ASR 模型、VAD、TTS 和真实中文识别闭环已就绪；TTS 仍需要服务器联网，真实麦克风、耳机、浏览器和读屏设备边界见[第一轮验收](docs/acceptance-round-1.md)。

## 目录

```text
whisper-guide/
├── backend-core/
│   ├── app/api/             # HTTP 与 WebSocket 控制器
│   ├── app/domain/          # 五状态机、双游标与讲稿追踪
│   ├── app/infrastructure/  # sherpa-onnx、VAD、edge-tts 适配
│   ├── app/repositories/    # SQLite 讲稿仓储
│   └── scripts/             # 模型下载与运维脚本
├── frontend/                # Vue 前端
├── docs/                    # 需求、架构、部署与验收文档
├── Dockerfile               # backend-runtime/frontend-runtime 多阶段构建
└── docker-compose.yml       # 本地容器编排
```

## 开发环境

使用 WSL Ubuntu，在项目的 Linux 路径内运行以下命令。需要 Python 3.12、uv、Node.js 24 和 pnpm；避免混用 Windows 与 WSL 的虚拟环境或 node_modules。

Python 环境由 `backend-core/.python-version` 指定，uv 会在需要时准备对应解释器。

## 启动

第一个终端启动后端：

```bash
cd backend-core
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

第二个终端启动前端：

```bash
cd frontend
pnpm install
pnpm dev --host 0.0.0.0
```

Windows 浏览器访问 `http://localhost:5173`。开发服务器将 `/api` 请求代理到后端 `127.0.0.1:8000`。

后端健康检查：`http://localhost:8000/api/health`。前端页面也提供手动检查入口。

前端生产构建及静态预览：

```bash
cd frontend
pnpm build
pnpm preview --host 0.0.0.0
```

本地预览也已配置 `/api` 代理。生产部署使用 Docker Compose 和 Nginx，将 `/api`（包括 WebSocket）转发到单个后端 worker；详细步骤见[部署说明](docs/deployment.md)。

容器启动：

```bash
cp .env.example .env
mkdir -p data/sqlite data/backups
docker compose build
docker compose up -d
```

容器镜像在构建时内置 `backend-core/models/` 下的已验证 STT 模型；SQLite 和备份分别持久化到 `./data/sqlite` 与 `./data/backups`。

离线发布使用 `deploy/package.sh` 生成 `release/` 和 `whisper-guide-release.tar`；目标服务器解包后运行 `./deploy.sh whisper-guide-image.tar.gz`。

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

## 第一版技术实现

- 前端：Vue 3 + TypeScript + Vite。
- 后端：Python + FastAPI；实时音频和控制事件通过 WebSocket 传输。
- 数据：SQLAlchemy + SQLite 保存讲稿正文和元数据；模型保存在服务端目录，活跃会话保存在单进程内存，原始上传文件和用户录音不落盘。
- 语音：服务端已接入 sherpa-onnx 流式 STT、WebRTC VAD 和 edge-tts；容器镜像从 `backend-core/models/` 内置已验证模型。
- 当前无需 PostgreSQL、Redis、MinIO 或独立消息队列。

第一版需求基线没有改变；未完成事项主要是不同目标设备、读屏软件和生产并发规模的现场验收。

## 部署与运维

- [部署说明](docs/deployment.md)：本地开发、模型准备、Docker Compose 与配置。
- [运维手册](docs/operations.md)：健康检查、日志、备份恢复与故障排查。
- [架构说明](docs/architecture.md)：组件职责、双游标和数据边界。
- [第一轮验收](docs/acceptance-round-1.md)：自动化、真实 STT 闭环和设备边界。

当前已接入会话控制、浏览器 PCM 采集与提示播放；模型目录在容器构建时复制到 `/models`，TTS 需要服务器联网。用户录音不持久保存。运行 `pnpm test` 可检查前端音频取消、重采样和 WebSocket 协议。

生产部署只使用一个后端 worker。当前没有用户认证和讲稿权限隔离，默认只绑定 localhost；公网开放前应增加 HTTPS、认证与访问控制。

### 语音定位与播报说明

识别结果先经过填充词过滤和短文本稳定性检查，再交给讲稿追踪器匹配；未匹配时页面会明确显示“自动提示已暂停”，用户可以手动提示或重新定位。当前版本仍由本地讲稿追踪器决定 reading_position 和 prompt_position，通用 STT 文本不会直接改变讲稿游标。

服务端可通过 WHISPER_GUIDE_STT_PROVIDER_CONFIGS 注册任意兼容网关，前端从 /api/speech/providers 选择已配置的模型；密钥只保存在后端环境变量中。远程 Provider 只输出 RecognitionUpdate，不能直接修改讲稿游标；超时或断网时回退本地 Provider。

## 语音处理架构

实时链路为：VAD 分段 → partial/final 流式识别 → ScriptAligner 结合讲稿窗口进行字面、拼音/近音和游标约束对齐 → reading_position 与 prompt_position 推进 → TTS 播报。

STT Provider 默认使用本地 LocalSherpaProvider。服务端可通过 WHISPER_GUIDE_STT_PROVIDER_CONFIGS 注册任意兼容网关，前端从 /api/speech/providers 选择已配置的模型；密钥只保存在后端环境变量中。远程 Provider 只输出 RecognitionUpdate，不能直接修改讲稿游标；超时或断网时回退本地 Provider。
