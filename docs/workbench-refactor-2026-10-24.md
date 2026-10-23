# 2026-10-24 工作台与多配置重构记录

## 范围

本轮在 AC-01 至 AC-27 基线上实现三路由界面、统一组件与主题、应用级会话资源管理和多配置管理。没有更换 STT 模型，没有引入 torch、transformers、CUDA 或其他大型推理依赖，也没有改变五状态、双游标、轮次令牌、事件优先级与录音仅内存规则。

## 当前结构

```text
frontend/src/
├── layouts/AppLayout.vue
├── views/
│   ├── DashboardView.vue
│   ├── StudioView.vue
│   └── ModelConfigsView.vue
├── components/
│   ├── health/ServiceHealthBar.vue
│   ├── scripts/ScriptLibraryPanel.vue
│   ├── studio/                 # 提词、控制、识别对比、状态拓扑
│   └── models/                 # 配置列表、表单、草稿转换
├── stores/                     # session / scripts / health / modelConfigs / preferences
├── services/                   # sessionController / healthMonitor
├── router/
├── api/
├── audio/
└── styles/

backend-core/app/
├── api/settings.py             # 多配置及兼容接口
├── api/recovery.py             # 无连接会话恢复元数据/明确终止
├── api/session.py              # 配置快照与会话准备共享保护
└── repositories/model_configs.py
```

## 实现要点

- 概览提供最近讲稿与生效模型快捷入口；工作台聚焦讲稿、双游标、明确状态与主要控制；模型页采用列表/详情编辑。
- Naive UI、Vue Router、Pinia 统一交互；服务状态入口、亮暗色/系统主题、路由标题与焦点、键盘操作共用基础布局。
- 应用级控制器持有唯一采集/连接/播放器。页面离开停止音频并等待暂停确认，不自动恢复录音；旧 generation 与过期播放器结果仍被拒绝。
- 模型草稿保存不激活，测试不保存；显式激活受全部未终止会话保护。生效版本与未激活修订分别展示，会话记录准备时的配置版本。
- 新建/复制不继承密钥；空白保留、新值替换、明确清除。GET 恒定掩码、校验错误不回显秘密；服务端私有文件权限 `0600`。
- 多配置仓储保留独立生效快照，草稿在重启后不会意外生效；旧单套配置自动迁移，兼容写入失败回滚。
- 断线恢复 API 仅返回元数据；终止前在运行时锁内复查连接归属和注册表身份，取消异步任务并清空有界音频队列。在线会话不能被恢复接口终止。
- 文档与 `.env.example` 注释同步更新，私有配置/临时文件继续排除 Git 和构建上下文。

## 验证

| 检查 | 本轮结果 | 日志 |
|---|---|---|
| 后端 pytest | **181 passed**；无失败 | [backend](validation/2026-10-24-workbench-backend.txt) |
| Ruff 静态/格式 | 全通过，42 个文件格式合规；浏览器测试服务改用自动清理的临时目录后另行通过专项检查 | 同上 |
| 前端 Node 回归 | **13 passed**，保留音频、轮次和 WebSocket 协议回归 | [frontend](validation/2026-10-24-workbench-frontend.txt) |
| Vitest 交互/控制器 | **21 passed**，含并发恢复串行化、失败释放、跨页取消及配置表单 | 同上 |
| TypeScript / 生产构建 | 全通过；主入口 451.84 kB（gzip 139.12 kB），工作台延迟加载 | 同上 |
| Chromium 端到端 | **5 passed**；6000 字讲稿暂停并切换页面实测 **231 ms**，断言上限 2000 ms | [browser](validation/2026-10-24-workbench-browser.txt) |
| Docker 构建与隔离启动 | 两镜像构建通过，两个容器 healthy；三个路由 HTTP 200，health=ok，STT/VAD/TTS=ready | [container](validation/2026-10-24-workbench-container.txt) |
| 脚本与配置 | 两个发布脚本 `bash -n`、`docker compose config --quiet`、`git diff --check` 全通过 | 终端检查 |

浏览器验收使用隔离的 5174/8010 服务、临时数据库、模拟麦克风及确定性识别/内存合成音频，覆盖上传、双游标、提示/打断、快捷键、路由暂停与继续、刷新后显式恢复、单个麦克风实例、配置测试/复制/删除/激活、亮暗主题、移动端抽屉和键盘焦点。测试接口不是生产路由，生产镜像不包含测试服务。

容器验收使用单独 Compose 项目、18080 端口和临时挂载数据。除基础健康外，已验证真实 sherpa 流初始化、讲稿上传 201/读取、WebSocket READY/配置 1000 ms/LISTENING/PAUSED/TERMINATED、多配置 CRUD、保存不激活、激活版本重启持久化和私有文件 `0600`。最终源码镜像又完成一次健康与路由检查；所有隔离容器、网络和临时挂载已清理，原 8080 服务保持运行。

最终镜像：`whisper-guide-backend:0.1.0` **827,560,020 字节**；`whisper-guide-frontend:0.1.0` **93,520,867 字节**。两者各自低于 1 GB。既有 Docker 缓存基线已为 9.232 GB，构建后约 9.597 GB，因此不能声称整机 Docker 存储满足历史 8 GB 约束；没有擅自清理其他项目镜像或全局缓存，也没有导出新的大型离线归档。

后端存在 1 条第三方 Starlette/httpx 弃用警告；Ruff 无告警、测试无失败。

## 界面截图

- [首页](screenshots/workbench-2026-10-24/dashboard.png)
- [工作台与双游标](screenshots/workbench-2026-10-24/studio.png)
- [模型方案管理](screenshots/workbench-2026-10-24/models.png)
- [暗色主题](screenshots/workbench-2026-10-24/models-dark.png)
- [移动端](screenshots/workbench-2026-10-24/studio-mobile.png)

## 结构清理与部署

- 删除旧单页组件 `SessionPanel`、`ScriptLibrary`、`ModelConfigPanel` 和根目录旧状态机组件，其职责由路由视图与专门组件承接。
- 删除未引用的 `api/health.ts`、`api/providers.ts`、旧 HTTP 配置保存封装与整页样式；共享类型、预设和领域行为保留。
- 移除重复的 `package-lock.json`，统一 pnpm 锁和版本；旧 SSR/Provider 展示测试由真实挂载交互与新协议测试替代，原音频和协议回归保留。
- Docker 与 CI 使用同一 pnpm 锁；新增独立 Chromium 验收任务。
- 修复离线部署脚本在仓库 `deploy/` 与发布包根目录两种位置的路径推导，脚本统一 LF 并增加 Git 规则，避免 Linux 的 CRLF 语法错误。
- 恢复请求覆盖整个等待确认周期的互斥保护，避免连续恢复多个会话留下连接归属；失败后允许用户重试。

已构建新镜像，当前 8080 的旧运行容器仍保持原状。要使用新界面，在结束当前演讲后执行：

```bash
cd /home/timeleafing/projects/whisper-guide
docker compose up -d --force-recreate --wait --wait-timeout 180
```

随后访问 `http://localhost:8080`。源码在后续再次修改时，应先执行 `docker compose build`。需要离线交付时，另行重新运行现有打包脚本。

## 验收边界

此前真实本地 STT 与中文识别结论保留。本轮协议/组件模拟测试不代表真实厂商账号已经连通；需要有效 endpoint、模型名、凭据后另测。目标麦克风/耳机、跨浏览器长时演讲及读屏软件组合仍需现场验收。

本轮已经重建并隔离验证镜像，尚未替换原 8080 运行容器；未重新导出离线发布包。
