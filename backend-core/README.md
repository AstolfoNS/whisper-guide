# Whisper Guide 后端

Python 3.12 + FastAPI，使用 uv 管理依赖、SQLAlchemy 连接服务端本地 SQLite。

## 本地开发

以下命令在 WSL Ubuntu 中执行：

```bash
cd backend-core
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
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
- SQLite 保存解码后的完整文本和元数据，不额外保存原始上传文件。当前不保存用户录音；未来实时音频只用于临时处理，不持久化。
- 本次演讲选择由前端准备流程管理，尚未实现实际演讲会话绑定接口。

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

模型放在 `models/`（已被 Git 忽略）。麦克风音频只在内存中处理；TTS
通过 `edge-tts` 生成内存中的 MP3，不保存用户录音或临时提示音频。

## 当前范围

- 提供应用工厂、启动/关闭生命周期、开发环境 CORS 和健康检查接口。
- 启动时创建本地 `data/whisper-guide.db`、讲稿表并验证 SQLite 连接。
- 定义五状态名称和正整数毫秒阈值契约，尚不实现状态转换。
- 尚未接入 STT、VAD、TTS、WebSocket 音频传输，也不下载语音模型。

后续实时会话由单个后端服务进程拥有；推理计算应与接收控制命令分开执行，避免阻塞暂停和打断。

## 实时会话与语音

WebSocket 入口为 /api/session。先发送 prepare 绑定讲稿，再发送 start；浏览器音频准备后发送 audio_ready，之后发送 16 kHz 单声道 PCM16 二进制帧。pause、resume、interrupt、terminate、configure、manual_prompt、reposition、playback_finished 和 playback_failed 管理会话。断线后 attach 同一会话，再由用户主动 resume。

语音适配使用 sherpa-onnx 中英流式模型、WebRTC VAD 和 edge-tts。模型通过 scripts/download_asr_model.py 单独准备，语音状态入口为 GET /api/speech/status。TTS 只生成内存音频，麦克风原始数据不保存。模型不存在时不会假装识别成功。

当前会话为进程内状态，部署单个 worker。数据仅持久保存讲稿，不提供用户录音存储。运行 pytest 会同时检查 HTTP、WebSocket、状态机和音频任务取消。
