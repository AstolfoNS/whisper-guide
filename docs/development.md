# 开发与构建指南

目标服务器导入镜像和离线包见[部署手册](../DEPLOY.md)，维护见[运维指南](maintenance.md)。

## 开发环境

在WSL/Linux项目目录使用Python 3.12、uv、Node.js ^22.18.0或>=24.12.0，以及package.json指定版本的pnpm。不要混用Windows与WSL的虚拟环境和node_modules。

后端：

```bash
cd backend-core
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

另一个终端启动前端：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

浏览器访问localhost:5173，后端OpenAPI在localhost:8000/docs。Vite将/api和WebSocket代理到8000；pnpm preview使用4173并保留代理。VITE_API_PROXY_TARGET覆盖开发代理目标；VITE_API_BASE_URL用于不同API地址，会进入前端构建，不能放密钥。

后端默认读取backend-core/.env，需要覆盖时复制同目录.env.example；相对数据、备份和模型路径按backend-core解析。监听地址与端口使用Uvicorn命令行参数。

## 项目标题

统一使用后端环境变量WHISPER_GUIDE_APP_TITLE，默认whisper-guide；未设置、空字符串或纯空白均回到默认。导航品牌、浏览器路由标题和API文档标题采用同一值。标题是纯文本，不作为HTML执行，也不属于模型方案配置。

Docker在项目根目录.env加入：

```dotenv
WHISPER_GUIDE_APP_TITLE="我的语音提示助手"
```

首次使用新版本按构建与发布步骤更新镜像。此后只改标题时，结束会话后执行：

```bash
docker compose up -d --no-build --pull never --force-recreate --wait --wait-timeout 180 backend
```

刷新浏览器后生效，不需要重编译前端。不要只用docker compose restart，它不会读取新的容器环境。离线包同样读取部署目录.env，旧.env不会被模板自动覆盖。

源码开发时在backend-core/.env设置同名变量并重启Uvicorn，前端不需要VITE标题变量。页面启动异步请求/api/app-config；失败时保留默认标题，不影响工作台。固定服务标识、数据库、会话存储键和Docker项目名不随显示标题改变。

## STT模型

```bash
cd backend-core
uv run python scripts/download_asr_model.py
```

默认目录为models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20，不进Git。脚本验证归档路径、类型及重复项，只保留tokens.txt、INT8 encoder、FP32 decoder和INT8 joiner。decoder必须优先FP32：现用模型的INT8 decoder在原始中文对照中会产生明显叠字，不能只按体积选择。

下载中断保留断点，可通过--url或WHISPER_GUIDE_MODEL_URL指定归档镜像。已有旧INT8-only目录也应重新运行脚本：只补约13.9MB的FP32 decoder，校验大小和固定SHA-256后原子安装；失败保留旧可用模型。单文件镜像使用--decoder-url或WHISPER_GUIDE_DECODER_URL。已经有FP32 decoder时重复执行不联网，并清理未选权重、官方测试音频与固定残留归档。保留来源说明，不手工按通配符删除onnx。

模型未就绪时讲稿功能仍可使用，开始识别明确失败。使用sherpa-onnx、WebRTC VAD与edge-tts，不需要torch、transformers或CUDA；edge-tts仍需网络。

## 构建与发布

```bash
# 项目根目录；只构建，不重启服务
docker compose config --quiet
docker compose build

# 制作离线发布包
./deploy/package.sh
```

package.sh输出根目录whisper-guide-release.tar；生成外层包后删除release内的重复大镜像归档。交付、权限、初始化和回滚统一见部署手册。

Docker前端构建先检查类型；后端只安装锁定生产依赖。运行层不含开发工具、测试和模型下载脚本，固定单worker。构建前准备模型，模型层位于业务源码之前。Docker固定复制FP32 decoder；旧模型目录缺此文件时先运行模型更新脚本，再构建镜像。忽略规则不能误排除Python的app/models包；数据、凭据与测试产物不进入镜像。

## 环境变量

路径与初始运行参数：

| 环境变量 | 默认或规则 |
|---|---|
| `WHISPER_GUIDE_APP_TITLE` | 显示标题，默认whisper-guide；后端启动读取，前端运行时加载 |
| `WHISPER_GUIDE_DATA_DIR` | 容器 `/data`，SQLite 与私有模型方案目录 |
| `WHISPER_GUIDE_BACKUP_DIR` | 容器 `/backups` |
| `WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS` | 默认 3000；整数且不小于 1000 |
| `WHISPER_GUIDE_SPEECH_MODEL_DIR` | 容器内固定的 sherpa-onnx 模型目录 |
| `WHISPER_GUIDE_SCRIPT_MAX_FILE_BYTES` | 默认 2097152，即 2 MiB |
| `WHISPER_GUIDE_SCRIPT_MAX_UPLOAD_FILES` | 默认 10 |
| `WHISPER_GUIDE_TTS_VOICE` | `zh-CN-XiaoxiaoNeural` |
| `WHISPER_GUIDE_CORS_ORIGINS` | JSON 来源数组 |
| `WHISPER_GUIDE_HTTP_PORT` | Compose宿主发布端口，默认8080；按当前配置对外监听 |

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

环境兼容注册表 `WHISPER_GUIDE_STT_PROVIDER_CONFIGS` 继续可用，详见 [接口参考](speech-provider-interface.md)。云端语音与聊天模型 Base URL 不是同一协议；语音 API Key 不会自动复用方舟文本 API Key。容器中的 localhost 指向当前容器；Ollama 等外部服务应填写后端容器可访问的地址。预设只是可修改示例，不证明账号已开通。

Compose参数以根.env.example与docker-compose.yml为准，后端本地默认值以app/config.py为准。已有私有仓储的生效快照优先于模型环境默认值，改环境不会隐式激活新方案。界面设置见[模型配置](model-configuration.md)，检查见[测试指南](testing.md)。
