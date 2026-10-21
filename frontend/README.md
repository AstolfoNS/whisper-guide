# Whisper Guide 前端

Vue 3 + TypeScript + Vite。当前提供初始化页面和真实的后端健康检查；尚未实现演讲会话、麦克风采集或语音功能，也不会申请麦克风权限。

## 本地开发

使用 Node.js `^22.18.0` 或 `>=24.12.0`。在 WSL Ubuntu 中执行：

```bash
cd frontend
npm ci
npm run dev
```

首次安装且尚未生成锁文件时使用 `npm install`。Windows 浏览器访问 `http://localhost:5173`。

先按后端说明启动端口 `8000` 的服务。开发服务器将 `/api`（包括后续 WebSocket）代理到 `http://127.0.0.1:8000`；页面会请求 `GET /api/health`。后端未启动时，会显示连接失败并提供重试按钮。

## 检查与构建

```bash
npm run typecheck
npm run build
npm run preview
```

`build` 先运行 Vue / TypeScript 类型检查，再生成 `dist/`。预览地址为 `http://localhost:4173`，已单独配置同样的 `/api` 代理；预览服务器仅用于本地检查构建结果。

## API 地址

默认使用同源 `/api`，本地开发不需要创建环境文件。如果前后端使用不同地址，可以复制 `.env.example` 为 `.env.local` 并设置 `VITE_API_BASE_URL`，例如 `http://localhost:8000`，然后重启开发服务器。

该变量是 API 服务的根地址，不包含 `/api`。它在构建时写入前端文件，不能用于保存任何密钥；跨域访问需要后端允许对应前端来源。

部署时通过同源反向代理将 `/api` 转给后端，或在构建前配置 API 根地址。生产静态文件不包含 Vite 的开发或预览代理。

## 交互基础

- 页面使用中文文档语言、语义化标题和原生按钮。
- 支持跳到主要内容、键盘焦点和服务连接状态播报。
- 展示五种会话状态及默认阈值 `3000` 毫秒，均为需求说明，不代表已建立演讲会话。
