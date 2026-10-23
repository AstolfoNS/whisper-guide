# 部署说明

## 本地开发

后端在 WSL Ubuntu 中执行：

```bash
cd backend-core
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

前端另开终端：

```bash
cd frontend
pnpm install
pnpm dev --host 0.0.0.0
```

访问 http://localhost:5173。浏览器需要授权麦克风。用户录音只在内存实时处理，不写数据库或文件。

## 模型准备

```bash
cd backend-core
uv run python scripts/download_asr_model.py
```

模型目录为 `backend-core/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20`，必须包含 `tokens.txt` 以及 `encoder*.onnx`、`decoder*.onnx`、`joiner*.onnx`。第二阶段验收已确认模型和真实中文 STT 闭环可用。模型不进入 Git，但会在 Docker 构建上下文中复制到镜像；下载脚本支持断点续传，失败时保留 `.part` 文件。

```bash
WHISPER_GUIDE_MODEL_URL='https://your-mirror.example/model.tar.bz2' \
  uv run python scripts/download_asr_model.py
```

脚本会在原子解压后检查 `tokens.txt` 以及非空的 `encoder*.onnx`、`decoder*.onnx`、`joiner*.onnx`。没有完整模型时，讲稿管理仍可用，但开始识别会明确报模型未就绪。TTS 使用 edge-tts，需要服务器联网。

## Docker Compose

需要 Docker Engine 和 Compose v2：

```bash
cp .env.example .env
mkdir -p data/sqlite data/backups
docker compose build
docker compose up -d
docker compose ps
```

`Dockerfile` 使用 `frontend-build`、`backend-deps`、`backend-runtime` 和 `frontend-runtime` 多阶段构建。前端使用 pnpm 构建，后端使用 `uv sync --frozen --no-dev`，模型层在业务代码之前复制并缓存；运行命令固定为单个 Uvicorn worker。Nginx 将 `/api/` 和 `/api/session` WebSocket 转发到后端。

访问 http://localhost:8080。SQLite 目录挂载到 `./data/sqlite`，备份目录挂载到 `./data/backups`；模型不通过卷覆盖，直接使用镜像内置内容。

## 离线发布

在 Docker daemon 可用的构建机执行：

```bash
chmod +x deploy/package.sh
./deploy/package.sh
```

脚本会构建 `whisper-guide-backend:0.1.0` 和 `whisper-guide-frontend:0.1.0`，将两个镜像合并保存为 `release/whisper-guide-image.tar.gz`，并生成 `whisper-guide-release.tar`。目标服务器使用 `DEPLOY.md` 中的 `deploy.sh` 启动，不会重新构建镜像。

当前无用户认证和讲稿权限隔离，默认端口只绑定 localhost。公网使用前需配置 HTTPS、认证和访问控制；浏览器采音也要求 HTTPS 或 localhost。

## 配置

- WHISPER_GUIDE_DATA_DIR：SQLite 目录。
- WHISPER_GUIDE_BACKUP_DIR：备份目录。
- WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS：默认静音阈值。
- WHISPER_GUIDE_SPEECH_MODEL_DIR：模型目录。
- WHISPER_GUIDE_SCRIPT_MAX_FILE_BYTES：单文件默认 2 MiB。
- WHISPER_GUIDE_SCRIPT_MAX_UPLOAD_FILES：单批默认 10 份。
- WHISPER_GUIDE_TTS_VOICE：默认 zh-CN-XiaoxiaoNeural。
- WHISPER_GUIDE_CORS_ORIGINS：JSON 格式允许来源数组。
- WHISPER_GUIDE_SERVER_HOST、WHISPER_GUIDE_SERVER_PORT：后端监听地址和端口。

实时会话位于单进程内存，必须使用单个 Uvicorn worker。重启会丢失实时会话，讲稿仍保存在 SQLite，需重新准备会话。当前没有用户认证和讲稿权限隔离，默认端口只绑定 localhost；公网部署前必须增加 HTTPS、认证、访问控制和资源配额。

## 语音诊断

/api/speech/status 中的 tts=ready 表示 edge-tts 依赖已安装，不等价于当前网络一定能访问语音合成服务。出现无声时，先点击页面的“手动提示”，再在容器内执行真实合成探测：

docker compose -f docker-compose.yml exec backend /app/backend-core/.venv/bin/python -c "import asyncio; from app.infrastructure.speech import SpeechServices; print(len(asyncio.run(SpeechServices().synthesize('测试播报'))))"

手动提示有声而自动提示没有声音时，应优先检查讲稿定位状态；手动提示也失败时，再检查 edge-tts 网络和浏览器播放。

## STT Provider 配置

默认使用本地识别：

```dotenv
WHISPER_GUIDE_STT_PROVIDER=local
WHISPER_GUIDE_STT_PROVIDER_CONFIGS=[]
WHISPER_GUIDE_CLOUD_OPT_IN=false
```

可以在后端注册多个任意兼容网关，前端会在“识别模型服务”面板列出可用 Provider 并保存本次选择。密钥只放在服务端环境变量中，推荐用 `access_token_env` 引用：

```dotenv
WHISPER_GUIDE_STT_PROVIDER=hybrid
WHISPER_GUIDE_STT_PROVIDER_CONFIGS=[{"id":"my-model","label":"我的流式模型","endpoint":"https://gateway.example/v1/stt","model":"streaming-model","access_token_env":"MY_MODEL_TOKEN"}]
WHISPER_GUIDE_CLOUD_OPT_IN=true
```

前端不会接触 access token。远程 Provider 失败时自动回退本地识别；云端模式下浏览器音频片段会离开本机，启用前应取得用户同意。

## 空间控制

构建上下文只允许瘦身后的 int8 模型文件进入镜像；fp32 权重、归档、node_modules、.venv 和 dist 均排除。Docker 构建使用 uv no-cache，日志轮转为单文件 10 MiB、最多 5 个文件。package.sh 在生成外层 release tar 后会删除内部 image.tar.gz，避免磁盘同时保存两份大型镜像归档。
