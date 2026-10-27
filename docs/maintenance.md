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

## .env修改后未生效

先区分文件读取、生效方案和页面默认：Docker读取部署目录.env，源码后端默认读取apps/backend-core/.env；环境中的同名Shell导出值可以覆盖文件。只重启容器不会重新注入.env，结束演讲后在原部署目录重新创建：

```bash
docker compose --env-file .env up -d --no-build --pull never --force-recreate --wait --wait-timeout 180
```

刷新页面后查看公开配置（只包含标题、静音默认和上传限制，不含模型凭据）：

```bash
curl -fsS http://localhost:8080/api/app-config
```

新会话取新的静音默认，手动输入和恢复旧会话仍保留其参数。旧版前端曾固定3000ms/10份/2MiB，需部署包含本次修复的前后端镜像，不能靠删除数据库解决。deploy.sh只导入离线包内的镜像，不会重新编译本机源码；删镜像后加载同一个旧包，得到的仍是旧程序。

WHISPER_GUIDE_ENVIRONMENT仅保留兼容校验，没有调试、日志、安全或识别分支，已从模板与Compose移除。WHISPER_GUIDE_BACKUP_DIR只指定Docker备份挂载的容器目标；不触发自动备份。

如果只有STT/LLM配置不变，核对模型页面的生效方案。已有私有方案覆盖环境引导值，按保存并激活更新，不删data/sqlite、.model-configs.json或密钥文件强制刷新。完整逐项作用及无业务开关字段见[环境变量表](development.md#环境变量与生效顺序)。

## 备份与恢复

在部署目录使用容器Python运行备份脚本：

```bash
BACKUP_NAME="whisper-guide-$(date +%Y%m%d-%H%M%S).db"
docker compose exec -T backend /app/backend-core/.venv/bin/python - \
  /data/whisper-guide.db "/backups/$BACKUP_NAME" < scripts/backup.py
```

脚本用SQLite backup API和quick_check，不覆盖同名文件，宿主输出为data/backups。数据库备份只包含讲稿。

这组命令对应默认容器目标/data与/backups；如果修改了WHISPER_GUIDE_DATA_DIR或WHISPER_GUIDE_BACKUP_DIR，备份调用中的源/目标路径也应使用实际容器路径。backup.py接受明确路径参数，不会因BACKUP_DIR变量而自动创建或调度备份。

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

## 核对部署版本

新版离线包根目录VERSION是部署版本，release.json记录Git标签、完整提交SHA、镜像ID、平台及OCI标签。部署脚本先校验SHA256SUMS与加载镜像再启动，不受.env中的源码构建版本变量影响。可用cat VERSION与docker compose images核对；不要在排障中打印完整.env或compose config，可能包含凭据。源码根Compose默认dev不是正式版本，正式交付按[部署手册](../DEPLOY.md)创建版本包。

升级/回滚沿用原部署目录data挂载，保留原.env与私有模型方案，不能切到空目录后启动同名项目。旧版不含manifest，使用其随包旧脚本，不把新deploy.sh单独混入旧包。

## 缓存维护

模型、SQLite、备份和私有方案是运行数据；.venv和node_modules是开发环境，不因体积大就删除。先查看占用和正在执行的构建：

```bash
du -sh apps/backend-core/models apps/backend-core/.venv apps/frontend/node_modules
du -sh "$(uv cache dir)"
pnpm --dir apps/frontend store path
docker system df
docker buildx du
```

按需清理可再生下载/构建缓存：

```bash
uv cache clean
pnpm --dir apps/frontend store prune
docker buildx prune --filter until=24h
```

缓存可能跨项目共享，下次构建可能重新联网。不要使用docker system prune -a --volumes作为本项目维护步骤。模型冗余用开发指南中的下载脚本清理，容器内没有该工具。

浏览器截图与报告写入apps/frontend/test-results、apps/frontend/playwright-report等忽略目录，不写版本化文档。保留需要排查的trace后可清理测试产物。

## 旧发布产物与本地生成文件

当前打包只在`release/<版本>/linux-amd64/`生成版本化tar与对应.sha256，不在根目录生成`whisper-guide-release.tar`，也不更新`release/`根下的平铺部署文件。历史平铺的`docker-compose.yml`、`DEPLOY.md`、`.env.example`、`deploy.sh`和`scripts/backup.py`只在确认是旧打包副本后清理；同名仓库源码文件仍需保留。

2026-10-27清理了本工作区旧根tar、上述平铺副本、空的`release/scripts/`，以及源码中的`__pycache__`、`.pytest_cache`、`.ruff_cache`、前端`test-results`和`dist`，共149个文件、约290.84MiB。保留`release/0.1.3/linux-amd64/`及校验文件，清理前后SHA256一致。旧产物和缓存均被Git忽略，删除只作用于本工作区，不会通过commit自动同步到其他机器。

清理前确认目录用途：实际部署目录中的.env、data、backups、私有模型方案、当前版本包和回滚资产不能按“旧平铺文件”批量删除。保留.venv、node_modules、模型和IDE配置；dist仅为前端构建输出，删除后使用Vite preview前需重新`pnpm build`，已运行的Docker镜像不依赖该目录。测试缓存和报告会在下次运行时重新生成。
