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
npm ci
npm run dev -- --host 0.0.0.0
```

访问 http://localhost:5173。浏览器需要授权麦克风。用户录音只在内存实时处理，不写数据库或文件。

## 模型准备

```bash
cd backend-core
uv run python scripts/download_asr_model.py
```

模型不进入 Git，默认放在 backend-core/models。没有模型时，讲稿管理仍可用，开始识别会明确报模型未就绪。TTS 使用 edge-tts，需要服务器联网。

## Docker Compose

需要 Docker Engine 和 Compose v2：

```bash
docker compose build
docker compose run --rm backend uv run python scripts/download_asr_model.py --models-dir /models
docker compose up -d
docker compose ps
```

访问 http://localhost:8080。Nginx 转发 /api/ 和 WebSocket /api/session。SQLite 与模型分别保存在命名卷 speech_data 和 speech_models 中。

当前无用户认证和讲稿权限隔离，默认端口只绑定 localhost。公网使用前需配置 HTTPS、认证和访问控制；浏览器采音也要求 HTTPS 或 localhost。

## 配置

- WHISPER_GUIDE_DATA_DIR：SQLite 目录。
- WHISPER_GUIDE_SPEECH_MODEL_DIR：模型目录。
- WHISPER_GUIDE_SCRIPT_MAX_FILE_BYTES：单文件默认 2 MiB。
- WHISPER_GUIDE_SCRIPT_MAX_UPLOAD_FILES：单批默认 10 份。
- WHISPER_GUIDE_TTS_VOICE：默认 zh-CN-XiaoxiaoNeural。
- WHISPER_GUIDE_CORS_ORIGINS：JSON 格式允许来源数组。

实时会话位于单进程内存，必须使用单个 Uvicorn worker。重启会丢失实时会话，讲稿仍保存在 SQLite，需重新开始会话。
