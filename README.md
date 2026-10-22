# Whisper Guide

面向盲人演讲者的实时语音追踪与提示网站。

第一版需求见 [需求文档](docs/requirements-v1.md)，包含就绪、识别、播报、暂停、终止五个状态，以及可配置的静音提示阈值。

## 当前初始化范围

- Vue 3、TypeScript、Vite 前端，提供无障碍基础布局和后端连接检查。
- Python 3.12、FastAPI 后端，提供健康检查、基础配置、SQLite 连接及测试。
- 纯文本讲稿库：批量上传 `.txt`、`.text`、`.md`，保存正文，选择并预览一份作为本次准备使用的讲稿。
- 本地开发代理、依赖锁文件、代码检查与运行说明。

已实现讲稿管理、五状态会话、浏览器音频采集与播放、服务端语音适配及讲稿追踪；真实识别需要先准备模型。用户录音不持久保存。页面上的状态说明不表示语音功能已经可用。

## 目录

```text
whisper-guide/
├── backend-core/    # FastAPI 后端
├── frontend/        # Vue 前端
└── docs/            # 需求与后续设计文档
```

## 开发环境

使用 WSL Ubuntu，在项目的 Linux 路径内运行以下命令。需要 Python 3.12、uv、Node.js 24 和 npm；避免混用 Windows 与 WSL 的虚拟环境或 node_modules。

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
npm ci
npm run dev -- --host 0.0.0.0
```

Windows 浏览器访问 `http://localhost:5173`。开发服务器将 `/api` 请求代理到后端 `127.0.0.1:8000`。

后端健康检查：`http://localhost:8000/api/health`。前端页面也提供手动检查入口。

前端生产构建及静态预览：

```bash
cd frontend
npm run build
npm run preview -- --host 0.0.0.0
```

本地预览也已配置 `/api` 代理。构建产物的正式部署需要单独将 `/api` 转发到后端；具体部署方案在后续开发中补充。

## 验证

```bash
cd backend-core
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

```bash
cd frontend
npm run build
```

## 第一版技术方向

- 前端：Vue 3 + TypeScript + Vite。
- 后端：Python + FastAPI；实时音频及控制事件计划采用 WebSocket。
- 数据：SQLAlchemy + SQLite 保存讲稿正文和元数据；后续保存设置、本地目录保存模型和生成音频、后端内存管理活跃会话。原始上传文件不重复保存，用户录音不落盘。
- 语音：服务端 STT、VAD、TTS 的具体模型需要验证，当前未下载或集成。
- 当前无需 PostgreSQL、Redis、MinIO 或独立消息队列。

本次初始化只建立工程入口。后续按需求文档接入语音处理和持久化。

## 部署与运维

- [部署说明](docs/deployment.md)：本地开发、模型准备、Docker Compose 与配置。
- [运维手册](docs/operations.md)：健康检查、日志、备份恢复与故障排查。
- [架构说明](docs/architecture.md)：组件职责、双游标和数据边界。
- [第一轮验收](docs/acceptance-round-1.md)：逐项结果和未通过的真实设备项目。

当前已接入会话控制、浏览器 PCM 采集与提示播放；STT 需要先准备官方模型，TTS 需要服务器联网。用户录音不持久保存。运行 npm test 可检查前端音频取消、重采样和 WebSocket 协议。

生产部署只使用一个后端 worker。当前没有用户认证和讲稿权限隔离，默认只绑定 localhost；公网开放前应增加 HTTPS、认证与访问控制。
