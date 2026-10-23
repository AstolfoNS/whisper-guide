# 运维手册

## 日常与更新

```bash
curl -fsS http://localhost:8080/api/health
curl -fsS http://localhost:8080/api/speech/status
docker compose ps
docker compose logs --tail=100 backend frontend
```

顶栏诊断区分后端能力、WebSocket、麦克风和最近播放结果。`tts=ready` 仅表明依赖可用；真实合成或浏览器播放失败需单独处理。常规轮询不调用付费模型、不录音、不自动试听。

保留数据目录后更新：

```bash
docker compose build && docker compose up -d --force-recreate --wait --wait-timeout 180
```

更新前备份讲稿与私有配置。容器重启丢失实时会话，讲稿和已激活模型配置仍从挂载加载。不要删除 `data/sqlite` 或 `data/backups`。

## 讲稿备份与恢复

在项目根目录执行：

```bash
python3 scripts/backup.py data/sqlite/whisper-guide.db \
  data/backups/whisper-guide-$(date +%Y%m%d-%H%M%S).db
```

脚本使用 SQLite backup API 和 quick_check，不覆盖同名目标。目标目录必须对执行用户可写，容器 UID 为 10001。数据库备份不含录音，也不含下述模型方案。

恢复前停止 backend、保留当前数据库副本，验证备份 quick_check，再替换 `whisper-guide.db`。不要在 SQLite 仍有写入连接时覆盖文件；恢复后检查 `/api/scripts`、`/api/health` 和语音状态，重新准备演讲。

## 模型方案备份与迁移

当前文件 `data/sqlite/.model-configs.json` 包括全部方案、密钥和独立生效快照；旧兼容文件为 `.model-settings.json`。两者均按敏感凭据保护，权限 `0600`，只备份到受限目录，不进入 Git、发布包或日志。

首次升级读取旧配置并生成“现有配置”和无凭据的“默认本地离线模式”；新仓储落盘后以其生效快照为准。迁移时保留新仓储整份文件、所有者和权限，停止 backend 后恢复并重启。只恢复 SQLite 不会恢复模型方案。

编辑不改变生效版本，保存后需主动激活。测试使用未保存草稿，不改写文件或生效状态。凭据留空保留，新值替换，明确清除才删除；复制方案不复制凭据。修订冲突返回 409，应保留本地草稿并刷新比较。

配置损坏时显示仓储不可用。先停止服务、保留损坏文件，再从受限备份修复，不要直接删除私有文件解除错误。503 保存失败时检查磁盘余量和 UID 10001 的目录写权限。

## 断线会话恢复与配置激活

工作台提供恢复/明确结束入口，避免浏览器刷新遗留暂停会话阻止激活。恢复不自动打开麦克风或播放音频，之后由用户开始或继续。

```bash
curl -fsS http://localhost:8080/api/sessions/recoverable
```

只列出无连接且未终止的会话元数据：`session_id`、`script_name`、`state`、`config_name`，不返回正文或识别文本。明确放弃某会话时指定 ID：

```bash
curl -fsS -X POST http://localhost:8080/api/sessions/<session-id>/terminate
```

已被其他页面连接的会话返回 `409 SESSION_CONNECTED`，需在所属工作台结束；不存在返回 404。接口不会自动结束在线会话。所有未终止状态（包括 READY 与 PAUSED）都会使模型激活返回 `409 SESSION_ACTIVE`；恢复后继续或明确结束即可解除。

## 故障排查

| 现象 | 检查项 |
|---|---|
| 页面打不开 | frontend 容器、8080 端口、Nginx 日志 |
| 嵌套路由刷新 404 | Nginx `try_files $uri $uri/ /index.html`，不要改写 `/api/` |
| API 502 | backend 健康与代理配置 |
| `app.models.script` 缺失 | 只忽略 `backend-core/models/`，保留应用 `app/models/` |
| SQLite 无法打开 / 配置保存 503 | UID 10001 的挂载目录写权限和磁盘余量 |
| 不能激活模型 | 结束全部未终止会话，检查断线恢复列表 |
| 修订冲突 | 其他标签页已修改，刷新并比较草稿 |
| 不能开始识别 | 模型完整性、原生依赖、语音状态 |
| 无提示音 | 合成网络、浏览器授权、输出设备、最近播放结果 |
| 云端测试失败 | 凭据、协议、模型名、超时；语音网关不能使用聊天 Base URL |
| WebSocket 断开 | Upgrade 头、网络、单 worker；回工作台手动重连 |
| 重启后进度丢失 | 实时会话仅在内存，需重新准备 |

修复目录权限：

```bash
sudo chown -R 10001:10001 data/sqlite data/backups
sudo chmod -R u+rwX data/sqlite data/backups
docker compose up -d --wait --wait-timeout 180
```

日志不得输出音频帧、Base64 音频或密钥。当前无身份隔离，配置与恢复接口只向可信访问者开放；公开部署需要 HTTPS 和认证层。
