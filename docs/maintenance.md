# 运维与故障排查

首次部署、离线升级、完整迁移和回滚见[部署手册](../DEPLOY.md)，模型诊断见[模型配置](model-configuration.md)，工作台状态与限制见[使用指南](user-guide.md)。

## 健康与更新

```bash
docker compose ps
curl -fsS http://localhost:8080/api/health
curl -fsS http://localhost:8080/api/speech/status
docker compose logs --tail=100 backend frontend
```

健康与ready只说明能力/连接，不证明实际识别准确率、TTS合成或浏览器播放成功。常规轮询不调用付费模型或自动试听。

更新前结束演讲并备份。源码目录可以构建并重建容器：

```bash
docker compose build && docker compose up -d --force-recreate --wait --wait-timeout 180
```

离线交付目录没有源码，导入镜像后使用部署手册的--no-build流程。重启清空实时会话，讲稿和方案从挂载加载；不要删除data/sqlite或data/backups。

## 备份与恢复

在部署目录使用容器Python运行备份脚本：

```bash
BACKUP_NAME="whisper-guide-$(date +%Y%m%d-%H%M%S).db"
docker compose exec -T backend /app/backend-core/.venv/bin/python - \
  /data/whisper-guide.db "/backups/$BACKUP_NAME" < scripts/backup.py
```

脚本用SQLite backup API和quick_check，不覆盖同名文件，宿主输出为data/backups。数据库备份只包含讲稿。

data/sqlite/.model-configs.json包括全部方案、凭据和生效快照，旧.model-settings.json可能仍用于迁移。两者权限0600，只备份到受限目录，不进Git、镜像、发布包或日志。

完整迁移时停止服务并复制整个data目录，包括隐藏文件及SQLite附属文件，按部署手册保留属主/权限。恢复前保留当前副本并检查备份，不在数据库仍写入时覆盖。恢复后检查讲稿、健康、语音状态，重新准备演讲。

方案损坏会显示仓储不可用，不会静默覆盖；停止服务、保留损坏文件并从备份恢复，不删除文件绕过错误。保存503时检查磁盘及UID 10001写权限。

## 遗留会话

工作台可恢复或明确结束断线会话；恢复不会自动开麦或播放。未终止READY/PAUSED也阻止模型激活。

```bash
curl -fsS http://localhost:8080/api/sessions/recoverable
SESSION_ID=replace-with-session-id
curl -fsS -X POST "http://localhost:8080/api/sessions/$SESSION_ID/terminate"
```

列表仅含无连接会话元数据。已有连接返回409，不存在返回404；不要结束其他页面正在使用的会话来绕过激活保护。

## 故障排查

| 现象 | 检查 |
|---|---|
| 页面打不开、API 502 | 容器、8080端口、后端健康与Nginx代理 |
| 嵌套路由刷新404 | 页面回退index.html，/api错误不能改成HTML |
| app.models.script缺失 | 忽略规则是否误排除Python的app/models |
| 数据库无法打开、保存503 | UID 10001的写权限与磁盘余量 |
| 配置无法激活、修订冲突 | 未结束会话、其他页面修改；保留草稿并刷新比较 |
| 无法开始识别 | 核心模型、原生依赖与语音状态 |
| 播报状态但无声 | 耳机/系统输出、浏览器权限、播放结果与TTS网络 |
| WebSocket断线 | Upgrade头、网络、单worker；主动重连继续 |
| 定位pending/mismatch | 原始转写、过滤标记与恢复状态，继续读正文 |
| 识别来源变本地 | 云端超时、网络或缓冲保护触发回退 |
| 重启后进度丢失 | 会话仅在内存，重新准备；讲稿仍在SQLite |

已确认挂载权限问题时：

```bash
sudo chown -R 10001:10001 data/sqlite data/backups
sudo chmod -R u+rwX data/sqlite data/backups
docker compose up -d --wait --wait-timeout 180
```

不记录录音帧、Base64或凭据。当前无用户隔离，公开部署应增加HTTPS、认证与访问控制。

## 缓存维护

模型、SQLite、备份和私有方案是运行数据；.venv和node_modules是开发环境，不因体积大就删除。先查看占用和正在执行的构建：

```bash
du -sh backend-core/models backend-core/.venv frontend/node_modules
du -sh "$(uv cache dir)"
pnpm --dir frontend store path
docker system df
docker buildx du
```

按需清理可再生下载/构建缓存：

```bash
uv cache clean
pnpm --dir frontend store prune
docker buildx prune --filter until=24h
```

缓存可能跨项目共享，下次构建可能重新联网。不要使用docker system prune -a --volumes作为本项目维护步骤。模型冗余用开发指南中的下载脚本清理，容器内没有该工具。

浏览器截图与报告写入frontend/test-results、playwright-report等忽略目录，不写版本化文档。保留需要排查的trace后可清理测试产物。
