# 第一轮项目验收

初轮日期：2026-10-22；本轮升级记录：2026-10-23

本文件记录第一版自动化和真实语音验收结果；第二阶段完成后已同步模型和闭环验证状态。

## 初轮基线结果（历史记录）

- 后端现有 78 项 pytest 通过，Ruff 静态与格式检查通过。
- 前端6项自动化测试通过，TypeScript 检查和生产构建通过。
- 前端会话页缺失问题已修复；Vue/TypeScript 生产构建通过。
- sherpa-onnx、edge-tts、webrtcvad 真实依赖可导入。
- 实际调用 edge-tts 合成中文提示，返回 12096 字节 MP3 内存数据。
- 已验证官方中文流式 STT 模型目录完整，包含 `tokens.txt`、`encoder*.onnx`、`decoder*.onnx` 和 `joiner*.onnx`。
- `/api/speech/status` 显示 STT、VAD、TTS 均为就绪；真实中文 PCM16 音频识别通过，`reading_position` 可推进。
- AC-13 口误重复回读场景通过，重新识别后双游标可回到用户实际朗读位置。
- Docker 容器构建和运行验收属于第三阶段配置后的下一步工作。

## 验收边界

领域状态机测试覆盖静音、暂停、打断、双游标、连续片段、回读、定位失败和过期回调。讲稿测试覆盖多文件、编码、原子写入、同名文件和重启读取。

真实麦克风、耳机、Chrome/Edge/Firefox 长时使用与读屏软件组合仍需现场验收。自动化替身用于协议测试，真实 STT 闭环已通过，但不替代不同设备环境的验收。

## 需求逐项检查矩阵

“自动化通过”只表示对应测试验证行为，不替代真实模型和设备验收。

| 需求 | 检查结果 | 证据 |
|---|---|---|
| AC-01 就绪不计时 | 自动化通过 | test_session |
| AC-02 首次沉默提示开头 | 自动化通过 | test_session、test_session_api |
| AC-03 阈值前开口取消 | 自动化通过 | test_session |
| AC-04 从最后单位播至标点 | 自动化通过 | test_session、test_tracking |
| AC-05 英文数字不截断 | 自动化通过 | test_tracking |
| AC-06 持续静音逐段播报 | 自动化通过 | test_session、test_session_api |
| AC-07 等待合成或播放时打断 | 协议与音频取消通过；设备级待验收 | test_session_api、audio.test |
| AC-08 打断后重播 | 自动化通过 | test_session、test_session_api |
| AC-09 两活动状态暂停 | 自动化通过 | test_session |
| AC-10 暂停后重新完整计时 | 自动化通过 | test_session |
| AC-11 播报暂停后先识别 | 自动化通过 | test_session |
| AC-12 正整毫秒参数与状态限制 | 自动化通过 | test_session_options、test_session |
| AC-13 口误重复回读定位 | 真实中文音频和合成文本用例通过 | test_tracking、真实 STT 闭环 |
| AC-14 失配一次反馈与手动定位 | 自动化通过 | test_session |
| AC-15 暂停后的旧结果无效 | 自动化通过 | test_runtime、test_session、audio.test |
| AC-16 同批事件优先级 | 领域自动化通过 | test_session |
| AC-17 断线暂停手动恢复 | WebSocket自动化通过 | test_session_api |
| AC-18 稿末停止续播 | 自动化通过 | test_session |
| AC-19 任意状态终止 | 自动化通过 | test_session |
| AC-20 全键盘操作 | 原生按钮与快捷键已实现；读屏现场待验收 | SessionPanel、ScriptLibrary |
| AC-21 多讲稿持久化 | 自动化及实际HTTP代理通过 | test_scripts、真实联调 |
| AC-22 单选讲稿 | 原生单选已实现；会话绑定协议通过 | ScriptLibrary、test_session_api |
| AC-23 同名不覆盖 | 自动化通过 | test_scripts |
| AC-24 无效文件整批拒绝 | 自动化通过 | test_scripts |
| AC-25 编码与纯文本 | 解码自动化通过；预览使用文本插值 | test_scripts、ScriptLibrary |
| AC-26 固定讲稿与会话 | 协议自动化通过 | test_session_api |
| AC-27 不保存用户录音 | 实现审查通过；录音仅有界内存队列 | api/session、speech、microphone |

## 前后端实际联调

启动真实 Uvicorn 和 Vite 服务后，经前端5173端口验证：
- 页面HTTP200与代理健康接口返回ok。
- 多文本上传HTTP201、列表读取和正文查询成功。
- WebSocket代理完成准备、配置与终止。
- 语音状态准确显示 STT、VAD、TTS 均已就绪。
- 真实中文 STT 音频流完成识别，位置游标推进和 AC-13 回读重定位通过。
- 真实中文TTS合成返回MP3内存数据。
- SQLite备份脚本备份后可恢复正文，并拒绝覆盖已有备份。
- Compose 配置已解析验证；第三阶段仅完成容器化配置，Docker 实际构建和运行验收尚未执行。

## 第一轮结论

软件自动化、HTTP/WebSocket 代理联调和真实中文 STT 闭环通过；目标麦克风和耳机、Chrome/Edge/Firefox 长时使用及读屏现场仍需单独验收，因此整套实时语音产品仍保留设备环境验收边界。

浏览器 UI 工具和物理设备验收不属于本轮自动化范围；模型下载阻塞已通过断点续传脚本和完整文件校验解决。

## 本轮修复记录

- 补齐缺失 SessionPanel，会话页从构建失败恢复为可编译。
- 修复暂停或打断触发旧 onended 的问题，避免错误推进游标。
- 增加音频解码轮次令牌，旧音频不能在取消后出声。
- 音频识别移出控制锁，并使用有界内存队列，暂停不等待推理完成。
- 修复断线重连的连接归属竞争，以及同连接开始新会话时的队列归属。
- 修复重复 audio_ready 重新计算静音时间，以及相同朗读文本可靠性恢复。
- 依赖锁补齐 sherpa-onnx-core，真实原生识别包可加载。
- 默认模型下载判断检查 tokens 与 encoder/decoder/joiner，避免部分资源误判就绪。

## 第二阶段定位与前端体验修复

- 过滤“嗯、呃、啊、那个”等填充词，避免无意义短词让自动提示永久停用。
- 忽略非最终的单字识别假设，降低流式 STT 部分结果造成的误定位。
- 提示合成任务完成后清理任务引用，避免后续提示无法重新生成。
- 前端重新设计状态徽章、定位告警、讲稿预览和会话操作区；未匹配状态明确提供手动提示和重新定位路径。
- 云端 Provider 采用厂商无关的注册表与 HTTP 网关契约；具体供应商只需在后端注册 profile，仍需通过本地讲稿追踪器更新游标。

## 第三阶段 Provider 与流式结果验收

- LocalSherpaProvider 保持默认离线路径，RecognitionUpdate 统一包含 text、is_final、segment_id 和 is_filler_only。
- HttpStreamingProvider 使用后端环境变量和显式云端授权；云端异常会回退 LocalSherpaProvider，旧 generation 结果不会推进游标。
- ScriptAligner 继续作为唯一游标提交入口，云端 Provider 不直接修改 reading_position 或 prompt_position。
- 初期前端提供 Provider 目录；本轮已替换为模型配置面板，可向后端单向提交凭据并读取脱敏状态。实际云端账号需独立联调。

## 2026-10-23 通用模型与状态监控升级

本轮功能已加入代码，最终后端回归与构建结果由本轮执行日志确认，不沿用上述旧测试数量作为本轮结论。

- STT 与语义 LLM 分开配置。通用 `LLMSemanticRefiner` 使用 OpenAI 兼容接口，提供 custom / DeepSeek / Qwen / Doubao / OpenAI / Ollama 可修改预设。
- LLM 返回限于讲稿窗口连续原文；本地校验、`ScriptAligner` 和当前轮次验证仍决定位置。超时、无效结果与旧轮次返回保留本地结果。
- 模型配置 API 支持读取、更新、临时连通性测试；密钥固定脱敏为 `****`，保存至数据目录 `.model-settings.json`，权限 `0600`，不保存用户录音。测试不修改生效配置或持久化文件。
- 五状态快照补充上一状态、原因、ISO 时间、最近 10 条有界流转历史和允许目标；前端显示拓扑、最新箭头与日志。
- 前端移除介绍与装饰卡片，保留四个工具模块；增加双游标原讲稿高亮、raw partial/final 标签、对齐文本及模型配置面板。
- 已执行的前端验证：14 项测试通过，Vue/TypeScript 检查通过，生产构建通过。组件测试覆盖五节点与转换高亮、模型表单原生控件、双游标和纯文本预览；模型客户端测试覆盖配置映射、预设和空白密钥保留。

真实云端 STT/LLM 账号尚需有效 endpoint、模型名与凭据执行独立联调。预设和模拟响应测试不能代表厂商账号连通性、音频质量或模型可用性；本轮没有保存用户录音用于验收。此前已验收的本地模型结论保留，设备/读屏组合边界不变。

本轮最终全量结果：后端 143 项通过，Ruff 静态和格式检查通过（36 个文件）；前端 14 项通过，类型检查与生产构建通过；Compose 配置解析通过。详细文件清单、拓扑和日志见 [本轮升级报告](model-upgrade-report-2026-10-23.md)。
