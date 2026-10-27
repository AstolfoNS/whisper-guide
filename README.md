# whisper-guide

面向照稿演讲的实时语音追踪与提示工作台。浏览器采集音频，服务端识别并跟踪讲稿；停顿后通过耳机提示后续内容，支持打断、暂停、回读和失锁自动恢复。

## 文档入口

| 任务                                    | 文档                                          |
| --------------------------------------- | --------------------------------------------- |
| 上传/删除讲稿、快捷键、实时定位与限制   | [使用指南](docs/user-guide.md)                |
| 本地/云端识别、大模型、模型发现与诊断   | [模型配置](docs/model-configuration.md)       |
| 新服务器、离线发布、升级、迁移与回滚    | [部署手册](DEPLOY.md)                         |
| 源码开发、模型准备、构建与环境变量      | [开发指南](docs/development.md)               |
| 健康检查、备份恢复、故障排查与缓存      | [运维指南](docs/maintenance.md)               |
| 模块边界、定位、并发与存储              | [架构说明](docs/architecture.md)              |
| HTTP、WebSocket、识别事件与模型方案契约 | [接口参考](docs/speech-provider-interface.md) |
| 状态机、双游标、隐私与行为基线          | [行为规范](docs/requirements.md)              |
| 检查命令、测试范围与当前验证结果        | [测试指南](docs/testing.md)                   |

## 项目标题

显示标题由环境变量WHISPER_GUIDE_APP_TITLE控制，默认whisper-guide。在根目录.env设置标题后重建后端容器并刷新页面即可；前端运行时读取配置，无需按标题重新编译。首次使用该功能需部署包含新接口和界面的版本，详见[开发指南](docs/development.md#项目标题)。

## 环境配置

Docker使用部署目录.env，源码后端使用apps/backend-core/.env；运行参数修改需重新创建容器并刷新页面，restart不重新注入环境。静音默认与上传限制由后端公开配置同步，模型环境值只作初始方案引导，已有生效方案优先。各变量作用、元数据/协议约束及覆盖顺序见[环境变量表](docs/development.md#环境变量与生效顺序)，其中明确区分有效参数、有条件生效字段和无业务作用的兼容字段，无需删除数据库排障。

## 版本发布

Git标签vX.Y.Z自动关联GitHub Release、双镜像X.Y.Z标签和版本化离线包。先运行python3 scripts/release.py prepare X.Y.Z，审核提交版本清单后再创建并推送标签；Actions检查同一SHA通过后构建/打包、创建草稿、上传tar与.sha256并发布。稳定版本与linux/amd64限定、下载部署和升级回滚见[部署手册](DEPLOY.md)。历史v0.1.0/v0.1.1保持不变；本功能不会自动创建标签或更新服务器。

## 运行与目录

已有Docker的服务器使用离线包，目标机不需要Python、uv、Node.js或pnpm；源码开发在WSL/Linux分别启动前后端。完整步骤分别维护在部署手册和开发指南。

默认容器入口为localhost:8080。麦克风需要localhost或HTTPS；当前没有用户认证和讲稿权限隔离，远程部署需要HTTPS和认证层。

```text
apps/
  backend-core/  独立的FastAPI应用：语音适配、定位与状态机
  frontend/      独立的Vue应用：工作台、采音、播放和浏览器测试
deploy/          Nginx、离线打包与部署脚本
scripts/         运维工具，例如SQLite备份
docs/            按用途维护的现行文档
data/            容器挂载的持久数据，不属于应用源码
release/         离线发布产物，不属于应用源码
```

apps中的两个应用各自维护依赖和开发命令；部署编排仍在项目根目录。源码开发的模型、数据和备份默认相对apps/backend-core解析；models和backups属于运行资源或数据，不是应用模块。容器内/app/backend-core、/data、/backups、/models及宿主data挂载保持原有约定，目录整理不迁移Docker宿主持久数据或改服务/镜像名；旧检出中的本地忽略文件与开发环境更新见[开发指南](docs/development.md#从旧目录更新)。

讲稿和私有模型方案位于数据目录；会话、录音帧和提示音频仅在内存。提交源码或构建镜像不会自动更新运行容器。旧实现过程、验收日志与截图可通过Git历史查阅。
