# Whisper Guide 后端

Python 3.12 + FastAPI 后端骨架，使用 uv 管理依赖、SQLAlchemy 连接服务端本地 SQLite。

## 本地开发

以下命令在 WSL Ubuntu 中执行：

```bash
cd backend-core
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

也可以执行 `uv run python main.py` 启动相同的开发服务。Windows 浏览器访问 `http://localhost:8000/docs` 查看 API 文档，`GET /api/health` 检查服务。

配置默认值足以本地启动。需要覆盖时将 `.env.example` 复制为 `.env`；相对数据目录以 `backend-core` 为基准解析，不依赖启动时的工作目录。

## 验证

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

## 当前范围

- 提供应用工厂、启动/关闭生命周期、开发环境 CORS 和健康检查接口。
- 启动时创建本地 `data/whisper-guide.db` 并验证 SQLite 连接；业务表随讲稿和会话功能后续添加。
- 定义五状态名称和正整数毫秒阈值的配置契约，尚不实现状态转换。
- 尚未接入 STT、VAD、TTS、WebSocket 音频传输或用户业务接口，也不下载语音模型。

后续实时会话由单个后端服务进程拥有；推理计算应与接收控制命令分开执行，避免阻塞暂停和打断。
