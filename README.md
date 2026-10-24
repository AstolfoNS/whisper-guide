# 芯创客无障碍语音提示助手

面向照稿演讲的实时语音追踪与提示工作台。浏览器采集音频，服务端识别并跟踪讲稿；停顿后通过耳机提示后续内容，支持打断、暂停、回读和失锁自动恢复。

## 文档入口

| 任务 | 文档 |
|---|---|
| 讲稿、快捷键、实时定位、自动恢复与限制 | [使用指南](docs/user-guide.md) |
| 本地/云端识别、大模型、模型发现与诊断 | [模型配置](docs/model-configuration.md) |
| 新服务器、离线发布、升级、迁移与回滚 | [部署手册](DEPLOY.md) |
| 源码开发、模型准备、构建与环境变量 | [开发指南](docs/development.md) |
| 健康检查、备份恢复、故障排查与缓存 | [运维指南](docs/maintenance.md) |
| 模块边界、定位、并发与存储 | [架构说明](docs/architecture.md) |
| HTTP、WebSocket、识别事件与模型方案契约 | [接口参考](docs/speech-provider-interface.md) |
| 状态机、双游标、隐私与行为基线 | [行为规范](docs/requirements.md) |
| 检查命令、测试范围与当前验证结果 | [测试指南](docs/testing.md) |

## 运行与目录

已有Docker的服务器使用离线包，目标机不需要Python、uv、Node.js或pnpm；源码开发在WSL/Linux分别启动前后端。完整步骤分别维护在部署手册和开发指南。

默认容器入口为localhost:8080。麦克风需要localhost或HTTPS；当前没有用户认证和讲稿权限隔离，远程部署需要HTTPS和认证层。

```text
backend-core/  FastAPI、语音适配、定位与状态机
frontend/      Vue工作台、采音、播放和浏览器测试
deploy/        Nginx、离线打包与部署脚本
scripts/       SQLite备份工具
docs/          按用途维护的现行文档
```

讲稿和私有模型方案位于数据目录；会话、录音帧和提示音频仅在内存。提交源码或构建镜像不会自动更新运行容器。旧实现过程、验收日志与截图可通过Git历史查阅。
