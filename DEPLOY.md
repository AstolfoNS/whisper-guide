# Whisper Guide 离线部署

## 目标服务器要求

- Linux 服务器，已安装并启动 Docker Engine 和 Compose v2。
- 可写的部署目录；应用数据保存在 `data/sqlite` 和 `data/backups`。
- 浏览器访问端口默认为 `8080`。公网使用前请配置 HTTPS、认证和访问控制。

## 解包和启动

```bash
tar -xf whisper-guide-release.tar
cd release
cp .env.example .env
mkdir -p data/sqlite data/backups
chmod +x deploy.sh
./deploy.sh whisper-guide-image.tar.gz
```

`deploy.sh` 会检查 Docker daemon、加载两个离线镜像、创建数据目录，并使用
`docker compose up -d --no-build` 启动服务。启动后访问：
`http://localhost:8080`。Compose 默认只绑定服务器本机；远程可通过 SSH 隧道访问，再在本地浏览器使用 localhost。不要直接公开无认证的配置接口。

## 停止和查看日志

```bash
docker compose -f docker-compose.yml ps
docker compose -f docker-compose.yml logs --tail=100 backend frontend
docker compose -f docker-compose.yml down
```

## SQLite 备份和恢复

备份前停止写入或短暂停止服务：

```bash
docker compose -f docker-compose.yml stop backend
python3 scripts/backup.py data/sqlite/whisper-guide.db \
  data/backups/whisper-guide-$(date +%Y%m%d-%H%M%S).db
docker compose -f docker-compose.yml start backend
```

恢复时先停止服务，保留当前数据库副本，再替换数据库文件：

```bash
docker compose -f docker-compose.yml stop backend
cp data/sqlite/whisper-guide.db data/sqlite/whisper-guide.before-restore.db
cp data/backups/<backup-file>.db data/sqlite/whisper-guide.db
docker compose -f docker-compose.yml start backend
```

恢复后检查 `/api/scripts` 和 `/api/health`。活跃会话只在进程内存中，重启后需要重新准备会话。

部署脚本会自动将 data/sqlite 和 data/backups 设置为容器 UID 10001 可写，避免 SQLite 启动时报 unable to open database file。

## 运行时模型配置备份

模型方案与独立生效快照位于 `data/sqlite/.model-configs.json`（`0600`），包含后端 API Key 和 Token。升级时旧 `.model-settings.json` 自动迁移为“现有配置”；新仓储存在后以其生效快照为准。SQLite 备份不包含这些私有文件，迁移需另行复制到受限位置并保持所有者与权限。不要把凭据放入日志、Git 或离线包。恢复后重启 backend，配置 API 只返回固定掩码。

模型配置更新失败且返回 503 时，检查 `data/sqlite` 是否允许容器 UID `10001` 写入。连通性测试失败不会改写已保存或生效的配置，先核对 Base URL、模型名称、凭据和超时，再检查后端网络。

## 可选云端语音与方舟文本模型

离线包内置本地 sherpa 模型，但云端 STT、文本 LLM 和 edge-tts 仍需服务器网络。原生火山流式 STT 选择 `volcengine-ws`，推荐 `wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`，资源 ID 填已开通的 `volc.seedasr.sauc.duration`（ASR 2.0 小时版）。新 `api-key` 模式只填独立**语音 API Key**，无需 App ID；旧 `app-token` 支持 App ID 与语音 Access Token。方舟文本模型另填自己的 Base URL、模型 ID 和 API Key，语义对齐可选 `hybrid`；两类凭据不会自动复用。

`WHISPER_GUIDE_CLOUD_STT_TIMEOUT_MS` 默认 5000，允许 `200–30000 ms`；`WHISPER_GUIDE_CLOUD_STT_ENABLE_NONSTREAM=true` 仅作用于 `bigmodel_async`。`.env.example` 提供首次默认值，已有私有方案优先；通常直接在模型页编辑、分别测试语音与文本，保存后结束当前会话再激活。

短静音探测等待最终协议响应；若提示空音频 `45000002`，只证明鉴权与协议可达，不能证明真实识别正确。本轮只有本地 WebSocket 模拟验证，真实语音账号和识别效果仍待部署者在工作台朗读验收。语音公网要求 WSS，禁止重定向；断网/超时继续使用本地识别保护会话。升级后需重新构建并打包镜像，旧发布包不自动包含该适配器。

## 更新后的界面与会话

访问 `/` 概览、`/studio` 工作台、`/settings/models` 模型方案管理。离开工作台会停止采集和播放并请求暂停，返回后手动继续。刷新遗留的断线会话可以恢复或明确结束；暂停仍阻止模型激活。

配置的保存与激活独立，测试不保存、不激活。默认本地方案不可修改，复制不会复制密钥；模型状态、真实网络请求及扬声器播放分别检查。全量规则见源码仓库的 `docs/speech-provider-interface.md`。

本轮源码更新不会自动更新旧离线镜像；构建机需重新运行 `deploy/package.sh` 才生成包含新界面的发布包。目标服务器继续使用原数据目录，加载新镜像并重新创建容器。

## 镜像与空间维护

运行镜像内置四份选定的 INT8 模型文件，后端不携带开发依赖、uv 或模型下载工具，应用与生产依赖不额外预编译 Python 字节码。模型必须在构建机源码目录准备并验证；部署机直接加载镜像，无需重新下载模型。升级镜像仍需保留 `data/sqlite`、`data/backups` 及私有配置。

离线包内的大镜像 gzip 只保留一份；不要将旧发布归档复制回构建上下文。日常日志已配置轮转，不能用删除数据卷或数据库的方式释放空间。构建缓存与开发缓存维护方法见源码的 `docs/operations.md`，本轮实测记录见 `docs/storage-slim-2026-10-24.md`。
