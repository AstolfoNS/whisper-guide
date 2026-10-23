# 运维手册

## 日常检查

```bash
curl -fsS http://localhost:8080/api/health
curl -fsS http://localhost:8080/api/speech/status
docker compose ps
docker compose logs --tail=100 backend frontend
```

健康接口确认 Web 服务，语音状态接口确认模型和依赖。TTS 包已安装不代表网络永远可用，合成失败会暂停会话。
当 `/api/speech/status` 返回 `stt=unavailable` 时，讲稿上传、列表和预览仍可用，但不能开始识别会话。

## 启停与更新

```bash
docker compose up -d
docker compose restart backend
docker compose down
docker compose build --pull
docker compose up -d
```

更新前备份数据。不要执行 docker compose down -v，除非确实要永久删除数据库和模型卷。

## 一致性备份

```bash
python scripts/backup.py data/sqlite/whisper-guide.db data/backups/whisper-guide-20261022.db
```

脚本使用 SQLite backup API 和 quick_check，不覆盖已存在的目标文件。容器部署可将脚本复制到容器或使用 SQLite backup API 在数据卷中执行，然后将备份复制到宿主机安全位置。备份中无用户录音。

## 恢复

1. 停止服务并保留当前数据库副本。
2. 验证备份 quick_check 为 ok。
3. 恢复数据目录中的 whisper-guide.db。
4. 启动服务并读取 /api/scripts 检查正文，再检查语音服务。
5. 用户重新准备会话。

## 故障排查

| 现象 | 检查项 |
|---|---|
| 页面打不开 | frontend 容器、8080 端口、Nginx 日志 |
| API 502 | backend 健康检查、反代配置 |
| WebSocket 断开 | Upgrade 头、/api/session、单 worker |
| 上传失败 | 格式、2 MiB/10 份限制、SQLite 权限 |
| 无法开始识别 | 模型目录、原生依赖、语音状态接口 |
| 没有提示音 | edge-tts 网络、耳机输出、浏览器播放权限 |
| 重启后进度丢失 | 活跃会话只在内存中，需重新准备 |

应用日志不要输出音频帧、Base64 音频或完整用户录音。生产需要受信网络、HTTPS 和认证层。

## 运行时模型配置备份

前端保存的模型配置位于 `data/sqlite/.model-settings.json`，文件权限为 `0600`，包含后端 API Key 和 Token。SQLite 备份脚本只备份讲稿数据库，不包含该文件；需要迁移模型设置时，另行复制到受限位置，并保持文件所有者与 `0600` 权限。不要把配置文件放入日志、Git 或离线发布包。恢复后重启 backend，GET `/api/settings/models` 只返回脱敏状态。

模型配置更新失败且返回 503 时，检查 `data/sqlite` 是否允许容器 UID `10001` 写入。连通性测试失败不会改写已保存或生效的配置，先核对 Base URL、模型名称、凭据和超时，再检查后端网络。
