# 开发、构建与环境变量参考

已有 Docker 的新服务器部署，统一按根目录[新服务器部署指南](../DEPLOY.md)操作。本文面向源码开发和构建机；目标服务器导入已构建镜像时不需要 uv、Node.js 或 pnpm。文档导航见[文档目录](README.md)，运行限制见[已知问题](known-issues.md)。

## 本地开发

在 WSL Linux 项目目录执行，使用 Python 3.12、uv、Node.js `^22.18.0` 或 `>=24.12.0` 与 pnpm：

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

访问 `http://localhost:5173`。Vite 的 `/api` 与 WebSocket 代理指向后端 8000；浏览器采音需要 localhost 或 HTTPS。`pnpm preview` 使用 4173 端口及相同代理。

## 本地 STT 模型

```bash
cd backend-core
uv run python scripts/download_asr_model.py
```

默认路径为 `backend-core/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20`。模型不进入 Git；Docker 内置已瘦身的 `tokens.txt` 与 encoder/decoder/joiner 三份 int8 权重，不包含冗余 fp32、测试音频或压缩包。此前真实中文音频、游标推进与回读闭环已经验收。

脚本先校验归档条目，只解压与运行时选择规则一致的四个核心文件：非空 INT8 优先，缺少时才保留对应 FP32 版本。模型完整就绪后删除未选中的权重、官方测试音频与本模型固定名称的归档/断点文件；下载失败仍保留断点。已就绪目录重复执行脚本会直接离线瘦身，不重新下载；README 等来源说明保留。当前模型目录为 198,271,089 字节，原生加载校验前后均通过，详见[瘦身记录](storage-slim-2026-10-24.md)。

下载中断时脚本支持断点续传，也可通过 `WHISPER_GUIDE_MODEL_URL` 指定可信镜像：

```bash
WHISPER_GUIDE_MODEL_URL='https://your-mirror.example/model.tar.bz2' \
  uv run python scripts/download_asr_model.py
```

未完整就绪时讲稿管理仍可用，开始识别明确报错。不要通过安装 torch、transformers 或 CUDA 替换现有轻量链路。

## Docker Compose 更新

```bash
cd /home/timeleafing/projects/whisper-guide
test -f .env || cp .env.example .env
mkdir -p data/sqlite data/backups
sudo chown -R 10001:10001 data/sqlite data/backups
sudo chmod -R u+rwX data/sqlite data/backups
docker compose config --quiet
docker compose build && docker compose up -d --force-recreate --wait --wait-timeout 180
```

WSL/Windows 浏览器访问 `http://localhost:8080`，首次更新后强制刷新页面。界面地址为 `/`、`/studio`、`/settings/models`。Nginx 将前端路由回退到 `index.html`，并将 `/api/`（含 WebSocket）代理给后端；不要将 API 404 改写成前端 HTML。

`Dockerfile` 分别构建前端和后端运行镜像；前端先检查类型再构建，后端使用 uv 锁定生产依赖。模型层置于业务代码之前，运行固定单个 Uvicorn worker。运行镜像只复制已安装生产环境、应用与四份核心模型文件，不携带 uv、锁文件、开发入口或模型下载脚本；模型需在宿主机构建前准备。依赖安装不预编译 Python 字节码，运行时也不写入字节码。`.dockerignore` 的模型忽略只能限定 `backend-core/models/`，不能使用 `**/models/**` 误排除 Python `app/models/`。

数据库及私有模型方案挂载至宿主机 `./data/sqlite`，备份挂载至 `./data/backups`。镜像更新继续使用原目录，不删除这些数据。日志单文件 10 MiB、最多 5 个。模型无需启动时下载；`edge-tts` 以及启用的云端服务仍需后端网络。

```bash
docker compose ps
curl -fsS http://localhost:8080/api/health
curl -fsS http://localhost:8080/api/speech/status
curl -fsS http://localhost:8080/studio >/dev/null
curl -fsS http://localhost:8080/settings/models >/dev/null
docker compose logs --tail=100 backend frontend
```

## 三视图与配置使用

1. 在概览进入工作台，选择或上传讲稿，再开始演讲。
2. 在模型配置页面新建方案，独立选择 STT 与语义校准参数。
3. “测试连通性”按当前表单测试 STT 或 LLM，不保存也不激活；STT 本地测试只检查模型能力；云端按所选 HTTP/WebSocket 协议检查鉴权及终止响应，不代表识别准确率。
4. “保存修改”生成方案新修订。结束全部会话后再“设为生效配置”；READY/PAUSED 同样属于未结束会话。
5. 切离工作台会立即停止采集和播放并暂停会话；返回后点击继续。刷新遗留会话可从恢复入口继续或明确结束，不能直接用配置激活绕过会话保护。

当前主要 API 为 `/api/settings/model-configs`；旧 `/api/settings/models` 保留兼容。方案及独立生效快照保存到 `/data/.model-configs.json`，对应宿主机 `data/sqlite/.model-configs.json`，权限 `0600`。升级读取旧 `.model-settings.json` 并保留“现有配置”；新仓储存在后以它为准。两类私有文件都不能进 Git、日志或离线包，SQLite backup 不包含它们。

## 环境变量

路径与初始运行参数：

| 环境变量 | 默认或规则 |
|---|---|
| `WHISPER_GUIDE_DATA_DIR` | 容器 `/data`，SQLite 与私有模型方案目录 |
| `WHISPER_GUIDE_BACKUP_DIR` | 容器 `/backups` |
| `WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS` | 默认 3000；整数且不小于 1000 |
| `WHISPER_GUIDE_SPEECH_MODEL_DIR` | 容器内固定的 sherpa-onnx 模型目录 |
| `WHISPER_GUIDE_SCRIPT_MAX_FILE_BYTES` | 默认 2097152，即 2 MiB |
| `WHISPER_GUIDE_SCRIPT_MAX_UPLOAD_FILES` | 默认 10 |
| `WHISPER_GUIDE_TTS_VOICE` | `zh-CN-XiaoxiaoNeural` |
| `WHISPER_GUIDE_CORS_ORIGINS` | JSON 来源数组 |
| `WHISPER_GUIDE_SERVER_HOST` / `WHISPER_GUIDE_SERVER_PORT` | 本地默认 `127.0.0.1:8000`，容器 `0.0.0.0:8000` |
| `WHISPER_GUIDE_HTTP_PORT` | Compose 对外 localhost 端口，默认 8080 |

模型环境值作为首次配置的默认值；已有私有仓储的生效版本优先：

| 环境变量 | 默认或规则 |
|---|---|
| `WHISPER_GUIDE_STT_PROVIDER` | 默认 `local`；界面支持 `cloud` |
| `WHISPER_GUIDE_CLOUD_OPT_IN` | 默认 `false`，云端音频需授权 |
| `WHISPER_GUIDE_CLOUD_STT_PROTOCOL` | 默认 `http-json`；原生火山流式 ASR 选择 `volcengine-ws` |
| `WHISPER_GUIDE_CLOUD_STT_ENDPOINT` | HTTP 网关 HTTPS；原生推荐 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async` |
| `WHISPER_GUIDE_CLOUD_STT_AUTH_MODE` | 默认 `api-key`，旧接口可选 `app-token` |
| `WHISPER_GUIDE_CLOUD_STT_RESOURCE_ID` | 默认 `volc.seedasr.sauc.duration`（ASR 2.0 小时版），与已开通资源一致 |
| `WHISPER_GUIDE_CLOUD_STT_TIMEOUT_MS` | 默认 5000，严格整数 `200–30000`，独立于 LLM 超时 |
| `WHISPER_GUIDE_CLOUD_STT_ENABLE_NONSTREAM` | 默认 `true`；仅 `bigmodel_async` 启用二次识别 |
| `WHISPER_GUIDE_CLOUD_STT_APP_ID` / `WHISPER_GUIDE_CLOUD_STT_ACCESS_TOKEN` | 新模式仅填语音 API Key 到 ACCESS_TOKEN；旧模式填 App ID 与语音 Access Token |
| `WHISPER_GUIDE_LLM_ALIGN_MODE` | `off` / `fallback` / `hybrid` |
| `WHISPER_GUIDE_LLM_PROVIDER_PRESET` | `custom` / `deepseek` / `qwen` / `doubao` / `openai` / `ollama` |
| `WHISPER_GUIDE_LLM_BASE_URL` | OpenAI 兼容聊天接口基址 |
| `WHISPER_GUIDE_LLM_MODEL` | 账号实际可用模型名或接入点 ID |
| `WHISPER_GUIDE_LLM_API_KEY` | 后端密钥 |
| `WHISPER_GUIDE_LLM_TIMEOUT_MS` | 默认 1500，正整数毫秒 |

环境兼容注册表 `WHISPER_GUIDE_STT_PROVIDER_CONFIGS` 继续可用，详见 [协议](speech-provider-interface.md)。云端语音与聊天模型 Base URL 不是同一协议；语音 API Key 不会自动复用方舟文本 API Key。容器中的 localhost 指向当前容器；Ollama 等外部服务应填写后端容器可访问的地址。预设只是可修改示例，不证明账号已开通。

## 同时启用云端 ASR 与文本校准

在模型配置页选择云端 STT、`volcengine-ws`、`bigmodel_async` 地址、已开通语音资源和独立语音 API Key，勾选音频云端授权；`api-key` 模式不需要 App ID。文本校准独立填方舟 Base URL、文本 API Key 和“获取模型列表”返回的实际模型 ID，模式选择 `hybrid`。分别测试、保存，再结束全部会话并激活。已有私有方案优先于环境默认值，改 `.env` 不会覆盖已激活方案。

STT 为 16 kHz 单声道 PCM16，按 200 ms 分块，分句尾部静音 600 ms；语音连接、缓冲与任务仍受轮次取消和本地回退保护。普通健康状态不代表已完成云端网络探测。短静音测试若提示“空音频”，仅确认鉴权与协议可达，仍需在工作台朗读、暂停、继续及提示播放验收。原生协议自动化使用本地模拟服务；后续实际配置协议及数字静音检查见[云端静音记录](cloud-silence-2026-10-24.md)，不等于真实中文准确率或目标设备长时验收。

后端需要访问配置的 WSS 语音服务、HTTPS 文本接口及 edge-tts 服务；不跟随语音重定向，不能将聊天模型地址填进 STT 栏。完整协议、安全边界和受控失败说明见 [Provider 接口](speech-provider-interface.md#原生-websocket-流式-asr)。

## 语音诊断

`/api/speech/status` 的 `tts=ready` 表示依赖可用，不等价于真实合成或浏览器扬声器正常。先在工作台主动手动提示，再检查网络和播放权限；常规健康轮询不会自动合成声音或调用付费模型。

```bash
docker compose exec -T backend /app/backend-core/.venv/bin/python -c "import asyncio; from app.infrastructure.speech import SpeechServices; print(len(asyncio.run(SpeechServices().synthesize('测试播报'))))"
```

## 离线发布与容量

在 Docker daemon 可用的构建机执行 `./deploy/package.sh`。它将后端与前端合并保存为一个 gzip 镜像包，外层使用 tar；生成外层归档后移除内部大镜像副本。构建上下文排除文档、测试、凭据、SQLite、node_modules、.venv、dist、测试音频和归档；应用的 `app/models/` 仍作为 Python 源码保留。开发依赖只用于构建或本地检查，不进入后端运行环境。目标服务器的导出传输、权限、SSH 隧道、初始化、健康检查、备份迁移和回滚均见[新服务器部署指南](../DEPLOY.md)。脚本最终交付物为根目录 `whisper-guide-release.tar`，构建机 `release/` 内的大 gzip 在外层打包后已被删除，不可直接复制该目录当完整发布包。已有验证镜像时可按指南直接导出交付目录，避免再次联网构建及生成第二份大型归档。

当前无用户认证与讲稿权限隔离，Compose 默认只绑定 localhost。远程使用需可信网络、HTTPS、认证和访问控制；不要仅修改端口暴露配置接口。
