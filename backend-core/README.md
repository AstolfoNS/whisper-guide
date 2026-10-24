# Whisper Guide 后端

Python 3.12 + FastAPI，使用 uv 管理依赖、SQLAlchemy 连接服务端本地 SQLite。

## 本地开发

以下命令在 WSL Ubuntu 中执行：

```bash
cd backend-core
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

也可以执行 `uv run python main.py` 启动相同的开发服务。Windows 浏览器访问 `http://localhost:8000/docs` 查看 API 文档，`GET /api/health` 检查服务。

配置默认值足以本地启动。需要覆盖时将 `.env.example` 复制为 `.env`；相对数据目录以 `backend-core` 为基准解析，不依赖启动时的工作目录。

## 讲稿 API

| 接口 | 功能 |
| --- | --- |
| `GET /api/scripts` | 返回讲稿元数据数组，不返回全文 |
| `POST /api/scripts/upload` | 以 multipart 的重复 `files` 字段上传一份或多份讲稿，返回新增讲稿全文数组 |
| `GET /api/scripts/{id}` | 读取一份讲稿全文，用于演讲前选择和预览 |

讲稿元数据包含 `id`、`name`、`character_count` 和 UTC `created_at`；全文响应增加 `content`。

- 支持 `.txt`、`.text` 和 `.md` 纯文本。Markdown 作为普通讲稿文本处理，不执行或渲染 HTML。
- 支持 UTF-8（含 BOM）、带 BOM 的 UTF-16 和 GB18030。空白文件、包含二进制控制字符的内容及不支持的文件类型会返回 422。
- 默认单文件最多 2 MiB、每批最多 10 份，分别由 `WHISPER_GUIDE_SCRIPT_MAX_FILE_BYTES` 和 `WHISPER_GUIDE_SCRIPT_MAX_UPLOAD_FILES` 配置。文件超限返回 413。
- 整批校验后一次写入；一份无效则整批不保存。同名文件创建独立 ID，不覆盖已有讲稿。
- SQLite 保存解码后的完整文本和元数据，不额外保存原始上传文件。用户录音只用于实时处理，不持久化。
- 本次演讲由前端准备流程选择讲稿，并通过 WebSocket `prepare` 将讲稿绑定到会话；会话开始前必须完成讲稿选择。

## 验证

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## 服务端语音依赖

语音依赖已包含在项目环境中。首次使用 STT 前下载中英流式模型：

```bash
uv run python scripts/download_asr_model.py
```

脚本支持断点续传；下载中断后会保留 `models/.sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20.tar.bz2.part`，下次运行会继续下载。网络受限时可通过 `--url` 或 `WHISPER_GUIDE_MODEL_URL` 指定可用镜像。脚本先验证归档路径、条目类型与重复项，只解压实际选用的 `tokens.txt` 及三份非空权重（INT8 优先，缺少时才选 FP32）。完整就绪后清除未选中权重、官方测试音频和本模型固定名称的归档/断点；失败时保留断点。已有完整目录再次执行会离线瘦身，不重新下载。默认模型当前保留四个核心文件和来源 README，共 198,271,089 字节；原生加载在清理前后均通过。

模型放在 `models/`（已被 Git 忽略）。Docker 运行层只携带生产环境、应用与四份核心模型文件，不带模型下载工具；下载脚本在源码目录执行。语音入口统一使用 `app.infrastructure.speech`，旧 `app.speech` 兼容转发模块已移除。麦克风音频只在内存中处理；TTS
通过 `edge-tts` 生成内存中的 MP3，不保存用户录音或临时提示音频。

## 当前实现状态

- 已提供应用工厂、启动/关闭生命周期、开发环境 CORS、健康检查和讲稿 API。
- 启动时创建本地 `data/whisper-guide.db`、讲稿表并验证 SQLite 连接。
- 已实现五状态会话、正整数毫秒阈值、WebSocket 音频控制、断线后 `attach` 和讲稿追踪。
- 已接入 sherpa-onnx 流式 STT、WebRTC VAD 和 edge-tts。ASR 模型目录缺失时状态接口返回不可用，开始识别会明确失败；TTS 生成依赖服务器网络。

会话由单个后端进程拥有，因此必须使用一个 Uvicorn worker；推理计算在控制锁外执行，避免阻塞暂停和打断。真实 ASR 效果、麦克风、耳机、浏览器和读屏验收仍见[第一轮验收](../docs/acceptance-round-1.md)。

## 实时会话与语音

WebSocket 入口为 /api/session。先发送 prepare 绑定讲稿，再发送 start；浏览器音频准备后发送 audio_ready，之后发送 16 kHz 单声道 PCM16 二进制帧。pause、resume、interrupt、terminate、configure、manual_prompt、reposition、playback_finished 和 playback_failed 管理会话。断线后 attach 同一会话，再由用户主动 resume。

语音适配使用 sherpa-onnx 中英流式模型、WebRTC VAD 和 edge-tts。模型通过 scripts/download_asr_model.py 单独准备，语音状态入口为 GET /api/speech/status。TTS 只生成内存音频，麦克风原始数据不保存。模型不存在时不会假装识别成功。

当前会话为进程内状态，部署单个 worker；重启会丢失活跃会话，讲稿仍保存在 SQLite。系统不保存用户录音。当前接口没有用户认证或讲稿权限隔离，公网部署前必须增加 HTTPS、认证和访问控制。运行 pytest 会同时检查 HTTP、WebSocket、状态机和音频任务取消。

## STT Provider

默认使用 LocalSherpaProvider。工作台通过 `stt_provider=local|cloud` 和显式 `cloud_opt_in` 切换通用语音源；保留 `WHISPER_GUIDE_STT_PROVIDER_CONFIGS` 兼容注册表。远程失败回退本地，所有游标仍由本地 ScriptAligner 更新。

## 模型设置与语义对齐

`GET/PUT /api/settings/models` 读取和更新 STT 与独立 LLM 配置；`POST /api/settings/models/test` 仅测试临时 LLM 配置，不激活、不保存。GET 只返回固定 `****` 和凭据存在标记；空白密钥保留原值。PUT 原子保存 `data/.model-settings.json`，文件权限 `0600`，启动加载其模型字段。配置文件含后端凭据，不能进入 Git、构建上下文或发布包。

`LLMSemanticRefiner` 使用 OpenAI 兼容 HTTP 接口，支持关闭、低分兜底、混合校准；预设只提供可修改 URL 与模型名示例。LLM 只能返回当前讲稿窗口连续原文，本地子串校验、ScriptAligner 与轮次令牌共同决定能否提交。真实云端账号需有效 endpoint、模型名和密钥执行独立联调。详见[Provider 协议](../docs/speech-provider-interface.md)。
