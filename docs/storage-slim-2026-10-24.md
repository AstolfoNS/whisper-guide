# 模型、依赖与 Docker 瘦身记录

日期：2026-10-24。目标是移除有证据可再生或冗余的内容，保留用户讲稿、私有配置、备份、正在使用的开发环境与完整语音链路。

## 已完成的本地清理

| 项目 | 清理前 | 清理后 / 实际操作 |
|---|---:|---|
| 默认本地模型目录 | 556,522,064 字节 | 198,271,089 字节，减少 358,250,975 字节 |
| uv 下载缓存 | 7,038,648,610 字节（基线目录统计） | `uv cache clean` 报告删除 29,394 个文件、6.6 GiB |
| pnpm 缓存 | 1,170,653,130 字节（基线 store 统计） | prune 报告清除 189 MB / 360 个包的孤立缓存及 720 个 global virtual-store 包 |
| Docker 构建缓存 | 152 条记录 | prune 报告回收 10.85 GB；重新构建后会再生 |

不同工具的容量包含计量单位、共享层与硬链接差异，不能将表中数字直接相加当作物理磁盘释放量。基线见 [storage-before.json](validation/2026-10-24-storage-before.json)，实际清理输出见 [缓存日志](validation/2026-10-24-storage-cache-cleanup.txt)。WSL 与 Docker 内部空间释放不保证 Windows VHDX 文件立即缩小；本轮未进行虚拟磁盘压缩或重置。

## 模型仅保留运行时使用的权重

目录：`backend-core/models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20/`。

| 文件 | 字节数 |
|---|---:|
| `tokens.txt` | 56,317 |
| `encoder-epoch-99-avg-1.int8.onnx` | 181,895,032 |
| `decoder-epoch-99-avg-1.int8.onnx` | 13,091,040 |
| `joiner-epoch-99-avg-1.int8.onnx` | 3,228,404 |
| 四个核心文件合计 | 198,270,793 |

来源 README 296 字节继续保留。删除的是未加载的 FP32 权重、官方示例 WAV 与 BPE 文件；没有删除当前识别器使用的 INT8 权重。清理前后均实例化原生本地识别器并输入 100 帧、每帧 20 ms 数字静音，加载正常。该检查确认模型和原生依赖可用，不是新的真实中文识别准确率或云端验收。详见 [模型清理日志](validation/2026-10-24-storage-model-cleanup.txt)。

下载脚本的变化：

- 解压前验证所有归档条目的路径与类型，并拒绝模型目录下的同名文件、越界、链接和特殊文件。只解压与运行时选择一致的四份文件，非空 INT8 优先，缺少时才保留对应 FP32。
- 目录已完整时直接离线瘦身，无需再次下载。只移除已知冗余权重、BPE 和官方测试样本；保留来源说明。
- 四份文件完整就绪后才清理本模型固定名称的归档与断点；失败保留断点，其他模型下载不受影响。
- 新增 33 项无需联网的专项测试，覆盖文件选择、已有目录清理、安全解压与失败保留行为。

## 依赖与模块处理

移除 `app/speech.py` 旧兼容转发文件，原来三处测试改为直接导入 `app.infrastructure.speech`。移除开发依赖中重复声明的 `httpx`；生产 `httpx` 仍保留。锁文件只更新重复开发声明的元数据，没有变更生产版本或哈希。

前端依赖暂未发现可证明未使用的项目，继续保留。`sherpa-onnx-core` 是原生识别依赖，不是可移除的重复包；`uvicorn[standard]` 与语音、网络、文件上传依赖均保留。没有引入 torch、transformers、CUDA 或新的大型 SDK。

已清理 pytest/Ruff 缓存、源码 `__pycache__`、前端 `dist` 与测试临时目录，删除生成文件共 4,606,177 字节；后续测试或构建会按需再生。详见[生成物清理记录](validation/2026-10-24-storage-artifact-cleanup.json)。`.gitignore` 与 `.dockerignore` 补齐覆盖率及 Vite 缓存规则。

`.venv` 和 `frontend/node_modules` 保留；缓存清理后的自动化回归仍使用这些已安装环境。用户讲稿、SQLite、私有密钥配置与备份均保留。

## Docker 构建边界

Dockerfile 继续使用前后端分别运行的多阶段构建：前端先类型检查再产出静态资源，后端只安装锁定的生产依赖。取消安装期 Python 字节码预编译，运行期继续禁用字节码写入；这会保留首次导入的少量解释开销，避免将可再生 `.pyc` 放入运行镜像。

后端运行层只包含 Python 运行时、必要系统库、已安装生产环境、应用和四份模型文件。uv、锁文件、开发入口 `main.py`、模型下载脚本与测试均留在构建阶段或源码目录。模型文件仍先于业务代码 COPY，进程继续使用 UID 10001 与 `--workers 1`。未使用扩展语法的 Dockerfile 不再额外下载语法前端镜像。

构建上下文排除 docs、测试、用户数据、缓存、开发环境与归档；只允许四份核心模型文件进入镜像，同时保留 Python `app/models/`。这些改动不涉及状态机、双游标、轮次令牌、连接归属或录音持久化策略。

本轮首次 `docker compose build` 已完成前端构建，后端全新依赖下载持续缓慢，主动取消该次冷构建。改从原有后端镜像提取相同路径的生产环境：先在临时依赖镜像执行 `uv sync --frozen --no-dev --no-cache --offline`，核对 41 个生产包，再删除其中 1,378 个 `.pyc`（31,500,432 字节）。正式 Dockerfile 通过 BuildKit 的同名构建上下文复用这份已核对依赖，完成 `backend-runtime` 构建；不复制旧应用代码，也不改生产锁版本或哈希。详见[离线依赖核对](validation/2026-10-24-storage-deps-reuse.txt)及[后端构建日志](validation/2026-10-24-storage-build.txt)。这证明复用依赖的最终运行镜像可构建，不代表全新空缓存联网安装已成功。默认 Dockerfile 的 `uv sync --frozen --no-dev --no-cache` 路径保留。

已有运行容器不会因 build 自动更新。当前服务的更新需先结束演讲并备份，再按[部署说明](deployment.md)重新创建容器。

## 最终实测与交付状态

| 项目 | 清理前（字节） | 清理后（字节） | 变化 |
|---|---:|---:|---|
| 项目目录（含已安装开发环境） | 1,056,016,593 | 693,328,726 | 减少 362,687,867（34.34%） |
| whisper-guide-backend:0.1.0 | 827,645,642 | 781,032,790 | 减少 46,612,852（5.63%） |
| whisper-guide-frontend:0.1.0 | 93,537,775 | 93,537,776 | 基本不变（统计读数相差 1 字节） |

镜像尺寸统一使用 `docker image inspect .Size`，项目目录为 `du -sb` 的当时读数（后续文档、Git 提交会带来少量变化）。后端约 744.85 MiB，低于 1 GB；前端约 89.20 MiB，原本已仅含 Nginx 与静态资源，本轮无可证明有必要删除的前端运行依赖。仅瘦身宿主模型不会等比例减少镜像，因为原 Dockerfile 已只复制 INT8。

隔离容器实测：首页、`/studio`、`/settings/models` 均 HTTP 200，健康接口 `ok`，STT/VAD/TTS 全 ready。讲稿上传 201、列表与正文读取通过；WebSocket 准备、配置、识别、播报、暂停、恢复与终止通过，收到真实 edge-tts 内存 MP3 8,496 字节。原生识别器输入 100 帧数字静音正常，容器内仅四份模型文件，应用与生产虚拟环境内 `.pyc` 为 0、无 pytest/Ruff/uv，UID 10001 与单 worker 校验通过。宿主备份脚本经 stdin 在容器临时卷执行，SQLite `quick_check` 为 `ok`。这些检查没有采集用户录音，也不替代目标设备扬声器试听或新的中文准确率验收。

测试仅使用独立网络与 tmpfs `/data`、`/backups`，执行后全部销毁。原 8080 服务的容器 ID、创建时间和健康状态保持；现有讲稿、模型方案、密钥与备份未挂载进测试容器。结果见[容器日志](validation/2026-10-24-storage-container.txt)及[完整容器结果](validation/2026-10-24-storage-container.json)。

最终 WSL 文件系统 `df -h` 为 32G 已用、924G 可用；这是整个 WSL 文件系统，并非项目占用或 Windows 宿主物理空闲。Docker 报告 Images 1.769 GB、Build Cache 2.057 GB（36 条，含共享层）；保留近期可复用构建缓存，避免下次重复慢下载。收尾又清理了超过 30 分钟未使用的缓存，工具报告 736.7 MB，临时依赖镜像与辅助文件已删除。源码、开发环境、已列下载缓存、Docker 镜像及构建缓存按各自统计相加约 5.4 GB，该数仅作容量预算参考，包含重复/共享数据，不能当作实际物理占用。未统计 WSL 其他项目或 Windows VHDX 文件大小。详情见[清理后空间快照](validation/2026-10-24-storage-after.json)和[收尾清理日志](validation/2026-10-24-storage-final-cache-cleanup.txt)。

当前 `whisper-guide-backend:0.1.0` 与 `whisper-guide-frontend:0.1.0` 已指向新镜像；原在用后端保留标签 `whisper-guide-backend:before-slim-20261024` 供回滚，这只是标签，不复制镜像数据。在用镜像所引用的层不强制删除。结束当前演讲并完成备份后，在项目根目录切换：

```bash
docker compose up -d --force-recreate --wait --wait-timeout 180
```

本轮未重新导出 release。默认冷构建仍需可达的锁定包下载地址；本次后端最终构建明确使用已核对的同锁依赖复用，未将冷构建的网络等待记录为通过。

## 回归结果

| 检查 | 结果 | 证据 |
|---|---|---|
| 后端依赖同步 | `uv sync --locked --offline` 通过 | [backend](validation/2026-10-24-storage-backend.txt) |
| Ruff 静态与格式 | 通过，59 个文件 | [backend](validation/2026-10-24-storage-backend.txt) |
| 后端 pytest | 545 项通过 | [backend](validation/2026-10-24-storage-backend.txt) |
| 前端 Node + Vitest | 19 + 68 = 87 项通过 | [frontend](validation/2026-10-24-storage-frontend.txt) |
| TypeScript 与生产构建 | 通过 | [frontend](validation/2026-10-24-storage-frontend.txt) |
| Chromium E2E | 14 项通过 | [browser](validation/2026-10-24-storage-browser.txt) |
| 原生模型加载前后检查 | 通过 | [model-cleanup](validation/2026-10-24-storage-model-cleanup.txt) |

pytest 保留一条既有 Starlette/httpx TestClient 弃用提示，无失败。浏览器使用隔离测试服务和合成输入，没有采集用户录音。本轮没有重新执行真实云端账号、设备长时或中文准确率验收，不将缓存清理或模拟输入当作识别质量结论。

维护命令与保留范围见[运维手册](operations.md#开发与构建缓存清理)。本轮未重新导出离线包。
