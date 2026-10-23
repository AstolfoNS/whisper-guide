# Whisper Guide 前端

Vue 3 + TypeScript + Vite 工具工作台，包含四个功能模块：

1. 讲稿上传、列表单选与纯文本预览。
2. 会话控制、静音阈值（最小 1000 ms，默认 3000 ms）、双游标高亮、raw partial/final 与已对齐讲稿对比。
3. 五状态机 SVG 拓扑、最近转换原因/时间、允许目标与最近 10 条流转。
4. STT 与 OpenAI 兼容语义大模型配置、连通性测试和保存。

## 开发与检查

在 WSL Linux 路径使用 Node.js `^22.18.0` 或 `>=24.12.0` 与 pnpm：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

后端监听 `8000`，浏览器打开 `http://localhost:5173`。Vite 将 `/api`（含 WebSocket）代理到 `127.0.0.1:8000`。

```bash
pnpm test
pnpm typecheck
pnpm build
pnpm preview
```

`build` 先运行 Vue/TypeScript 检查，再生成 `dist/`；预览端口 `4173` 也带本地 API 代理。测试使用 Node test runner 与已有的 Vite/Vue server renderer，无浏览器重型依赖。

## 接口和凭据

默认请求同源 `/api`。分开部署时可在 `.env.local` 设置 `VITE_API_BASE_URL`（不包含 `/api`），它在构建时写入静态文件，禁止放入 API Key 或 Token。

模型面板调用 `GET/PUT /api/settings/models` 和 `POST /api/settings/models/test`。凭据由密码框向后端提交，后端只返回固定 `****` 与存在标记；留空保留原凭据，前端不将密钥放入 localStorage。测试不保存设置，保存后新会话使用最新配置。预设 URL 和模型名可以修改，真实服务仍需有效账号和凭据。

STT 默认本地 `sherpa-onnx`，云端语音需显式勾选授权。语义 LLM 可选 `off/fallback/hybrid`，独立于 STT。云端 STT 发送音频片段，LLM 只接收讲稿窗口和转写。

## 无障碍与音频

原生按钮、输入框、下拉框和单选保持键盘可操作；保留跳到主要内容、可见焦点、状态播报以及 `Alt+Shift+P/I/E/H` 会话快捷键。双游标用不同底色与边框并配合文字标记；raw 转写和讲稿对齐分开显示。

浏览器只采集内存 PCM16 帧并播放内存 MP3，不保存用户录音。语音权限需要 localhost 或 HTTPS。真实麦克风、耳机及读屏设备组合仍需现场验收。
