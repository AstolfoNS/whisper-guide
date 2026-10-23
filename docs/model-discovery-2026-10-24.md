# 2026-10-24 模型列表获取与选择

## 操作

1. 在“模型配置”新建或编辑一套方案，填写 Base URL 和 API Key；已有方案的密钥可留空复用。
2. 点击“获取模型列表”。无需先填模型 ID 或开启对齐模式；获取不会保存、激活方案，也不自动调用生成模型。
3. 使用“筛选模型”按名称/ID 搜索，在“从模型列表选择”下拉框中选中模型。原“模型名称”输入仍可手动修改。
4. 选择 `fallback` 或 `hybrid` 后点击“测试大模型连通性”，通过后保存、激活；未终止会话仍会阻止激活。

服务不支持列表、没有返回模型、响应过大或找不到目标模型时，可以继续手动填写 ID。接口列出某个模型并不证明当前密钥具备它的生成权限，实际调用测试仍独立执行。

## 实现与边界

- 新增 `POST /api/settings/model-configs/discover-models`，请求 `{id?,settings?}`；共用测试接口的草稿配置构造，不要求配置名称或模型名称。
- 密钥只在后端复用或接受单向提交；不向前端返回原凭据，不进入列表或持久化浏览器存储。
- 后端 `app/infrastructure/model_catalog.py` 独立处理列表读取，沿用现有轻量 `httpx`、URL 验证和安全错误分类，不增加依赖。
- 从同一 API 根请求 `GET /models`，支持标准 `data`、裸数组及 Ollama 风格 `models` 数组，保留 `/api/v3` 等路径。只读取当前响应，不翻页、不探测其它端点。
- 不跟随重定向，请求上限 30 秒、未压缩响应上限 1 MiB、输出最多 500 项/扫描 5000 项；拒绝强制压缩响应。条目只返回 ID 和可选显示名，去重并过滤非法字符及凭据回显。
- 独立的请求取消和轮次标记保护旧响应。修改地址、输入密钥、清除标记、预设、切换方案或离开页面会取消并清空旧列表，仍可编辑表单。
- 保存/测试会结束列表请求；选择只改变当前草稿。旧模型测试接口和成功响应保持兼容，重复的草稿构造及错误分类已共用。
- 五状态机、双游标、轮次令牌、音频队列、识别与讲稿定位逻辑均未改动。

主要文件：

```text
backend-core/app/
├── api/settings.py
└── infrastructure/
    ├── llm.py                 # 共用连接错误分类
    └── model_catalog.py       # 有界、只读的模型发现
frontend/src/
├── api/modelConfigs.ts
├── components/models/ModelConfigForm.vue
└── views/ModelConfigsView.vue
```

详细契约见 [Provider 与模型方案协议](speech-provider-interface.md#模型发现)。

## 验证

| 检查 | 结果 | 证据 |
|---|---|---|
| 后端 pytest | **275 passed**，其中新增 43 项模型发现测试 | [后端日志](validation/2026-10-24-model-discovery-backend.txt) |
| Ruff 静态/格式 | 全通过，45 个文件格式合规 | 同上 |
| 前端测试 | **14 项 Node + 35 项 Vitest** 全通过 | [前端日志](validation/2026-10-24-model-discovery-frontend.txt) |
| TypeScript / 生产构建 | 全通过，无新增依赖；模型页 JS 25.19 kB（gzip 8.55 kB） | 同上 |
| 浏览器端到端 | **7 passed**：原有 5 项 + 新增 2 项发现流程；6000 字讲稿暂停并切页 277 ms | [浏览器日志](validation/2026-10-24-model-discovery-browser.txt) |
| 真实方舟模型目录 | 新实现获取 **137 个模型，479 ms，未截断** | [只读验证](validation/2026-10-24-model-discovery-ark.txt) |

专项覆盖：API 路径、Bearer、草稿和已存密钥、不落盘/不激活、空目录/重复/无效条目、上限、超时、HTTP 错误、禁止重定向和凭据回显过滤。前端覆盖离页和来源变更取消、迟到响应、搜索选择、手填与错误提示。浏览器在隔离的 5174/8010 服务完成从填写到选择、测试、保存、留空复用密钥与移动端布局的完整流程，使用合成测试凭据和本地模拟接口。

真实方舟检查在临时容器中挂载最终源码及现有配置为只读，调用新的发现实现；容器自动移除。没有调用聊天生成、发送讲稿或录音，也没有保存或激活用户方案，因此不代表目录中每个模型均已完成真实生成验收。现有 8080 服务继续运行。

后端保留一条第三方 Starlette/httpx 弃用警告，测试无失败。没有重建/导出镜像或增加大型依赖；测试服务和临时输出已清理。

## 截图

- [模型列表与选择](screenshots/model-discovery-2026-10-24/models.png)
- [移动端](screenshots/model-discovery-2026-10-24/models-mobile.png)

截图使用模拟模型，不包含真实密钥。既有工作台截图保留原历史版本。

## 应用到现有容器

结束当前演讲后执行：

```bash
cd /home/timeleafing/projects/whisper-guide
docker compose build && docker compose up -d --force-recreate --wait --wait-timeout 180
```

访问 `http://localhost:8080/settings/models`，已有模型方案和密钥保留在原挂载目录中。
