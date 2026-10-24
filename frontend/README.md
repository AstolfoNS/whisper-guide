# 芯创客无障碍语音提示助手 前端

Vue 3 + TypeScript + Naive UI + Vue Router + Pinia 的三视图提词器工作台。

| 路由 | 内容 |
|---|---|
| `/`、`/dashboard` | 最近讲稿、当前生效方案、快捷入口 |
| `/studio` | 讲稿库、可调字号提词、双游标、会话控制、raw/final 对比、折叠状态机 |
| `/settings/models` | 多配置新建/复制/编辑/测试/激活/删除 |

`/studio?panel=scripts` 打开讲稿管理，`/studio?script=<id>` 选择讲稿；未终止会话仍保持讲稿绑定。统一导航支持亮色、暗色和跟随系统，不使用外部字体。

## 开发与检查

在 WSL Linux 路径使用 Node.js `^22.18.0` 或 `>=24.12.0` 与 pnpm：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

后端监听 `8000`，浏览器打开 `http://localhost:5173`。Vite 将 `/api` 和 WebSocket 代理到后端；`pnpm preview` 的 `4173` 端口同样配置代理。

```bash
pnpm test          # Node 兼容回归 + Vitest 交互测试
pnpm test:unit     # 单独执行 Vitest + Vue Test Utils
pnpm typecheck
pnpm build
# 首次浏览器验收需安装 Chromium；依赖隔离的后端开发环境
pnpm exec playwright install --with-deps chromium
pnpm test:e2e
```

浏览器测试在 5174/8010 启动独立前后端，使用临时 SQLite、模拟麦克风与确定性识别/内存音频；退出清理测试目录，不调用真实付费模型，不修改已运行的 8080 服务。`VITE_API_PROXY_TARGET` 仅用于指定开发代理目标。

构建先检查 Vue/TypeScript，再产出 `dist/`。容器由 Nginx `try_files ... /index.html` 支持嵌套路由刷新。

## 目录与运行时

- `layouts/`：导航与主题，`views/`：三个路由视图。
- `components/health|scripts|studio|models/`：按工具职责分组。
- `services/sessionController.ts`：唯一应用级音频/连接资源管理，路由卸载不再创建或丢弃另一套会话。
- `stores/`：可展示状态，`router/`：安全离开与页面标题/焦点管理。
- `api/`：HTTP/WebSocket 类型；`audio/`：内存采集/播放与轮次取消。
- `styles/`：主题颜色、响应式布局和可见焦点。

离开工作台立即停止采集/播放，等待服务端暂停确认；返回后由用户点击继续。刷新后的断线会话可恢复或明确结束；服务重启后需重新准备。静音等待最小 `1000 ms`、默认 `3000 ms`，始终使用正整数。

## 模型方案与凭据

主要接口为 `/api/settings/model-configs`，操作和迁移规则见 [Provider 协议](../docs/speech-provider-interface.md)。选择方案只编辑；保存不激活；测试不保存；激活需要结束所有未终止会话，暂停也会阻止激活。多标签页修订冲突不会覆盖服务器内容。存在未保存草稿时，切换方案和路由会提示确认。

“获取模型列表”使用当前 Base URL 和凭据，经后端请求 `/models`；支持筛选与选择，也保留模型名称手动输入。对齐关闭、模型名未填、新建未保存方案都可以发现。列表只在页面内存，接口或凭据变化会取消旧请求；选择只改草稿，仍需测试、保存和激活。

复制清空凭据。密码框中的 `****` 仅表示后端已配置，不能当成新密钥提交；空白保留，独立清除才删除。浏览器不把凭据放入 localStorage 或 sessionStorage；主题及会话恢复标识可单独保存。不要在 `VITE_*` 中放 API Key。

默认请求同源 `/api`；分开部署可设置 `VITE_API_BASE_URL`（不包含 `/api`），构建时会写入静态产物。云端 STT 和语义 LLM 独立；语音网关不是聊天模型 Base URL，真实服务需要有效账号和协议兼容。

## 无障碍、状态与性能

保留跳到主要内容、可见焦点、路由页面标题、键盘导航与 `Alt+Shift+P/I/E/H` 会话快捷键。编辑输入时不触发演讲命令；抽屉和弹窗保持焦点管理。双游标分别使用底色、边框及文字标记，partial 不逐字触发读屏播报。

顶栏区分服务能力、WebSocket、设备权限和播放结果。后台状态轮询不调用付费模型或自动播放声音；模型测试由用户主动发起。长讲稿按原始字符偏移映射，局部游标更新不重新分词，不改变英文数字边界。

麦克风 PCM16 和提示 MP3 只在内存，采音需要 localhost 或 HTTPS。真实麦克风、耳机、长时浏览器与读屏设备仍需现场验收。

云端 STT 可选择通用 HTTP 网关或火山原生 WebSocket；“填入推荐设置”使用优化双向接口，支持独立语音 API Key、资源 ID、旧 App ID/Token、二遍复核和语音超时。新版语音 Key 不要求 App ID，也不自动借用文本模型 Key。运行态回退会在服务指示器中明确显示。完整配置和验收见[原生流式语音记录](../docs/cloud-asr-2026-10-24.md)。
