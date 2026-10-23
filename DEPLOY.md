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
`http://服务器地址:8080`。

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

前端保存的模型配置位于 `data/sqlite/.model-settings.json`，文件权限为 `0600`，包含后端 API Key 和 Token。SQLite 备份脚本只备份讲稿数据库，不包含该文件；需要迁移模型设置时，另行复制到受限位置，并保持文件所有者与 `0600` 权限。不要把配置文件放入日志、Git 或离线发布包。恢复后重启 backend，GET `/api/settings/models` 只返回脱敏状态。

模型配置更新失败且返回 503 时，检查 `data/sqlite` 是否允许容器 UID `10001` 写入。连通性测试失败不会改写已保存或生效的配置，先核对 Base URL、模型名称、凭据和超时，再检查后端网络。
