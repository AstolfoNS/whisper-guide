# 第一轮项目验收

日期：2026-10-22

## 当前结果

- 后端现有 78 项 pytest 通过，Ruff 静态与格式检查通过。
- 前端6项自动化测试通过，TypeScript 检查和生产构建通过。
- 前端会话页缺失问题已修复；Vue/TypeScript 生产构建通过。
- sherpa-onnx、edge-tts、webrtcvad 真实依赖可导入。
- 实际调用 edge-tts 合成中文提示，返回 12096 字节 MP3 内存数据。
- 官方 STT 模型下载遇到网络读取超时，真实 STT 尚未通过。
- Docker 未安装，容器运行验收暂未执行；提供了部署配置及文档。

## 验收边界

领域状态机测试覆盖静音、暂停、打断、双游标、连续片段、回读、定位失败和过期回调。讲稿测试覆盖多文件、编码、原子写入、同名文件和重启读取。

真实麦克风、耳机、Chrome/Edge/Firefox 长时使用与中文识别准确度必须在模型准备后完成设备验收。自动化替身用于协议测试，不代表真实识别已通过。

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
| AC-13 口误重复回读定位 | 合成文本用例通过；真实STT准确度待验收 | test_tracking |
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
- 语音状态准确显示STT模型未就绪、VAD就绪、TTS已安装。
- 真实中文TTS合成返回MP3内存数据。
- SQLite备份脚本备份后可恢复正文，并拒绝覆盖已有备份。
- Compose配置已解析验证，Docker实际构建运行因本机未安装Docker尚未执行。

## 第一轮结论

软件自动化与HTTP/WebSocket代理联调通过。整套实时语音产品不能宣布完全验收通过：
真实ASR模型下载超时，且未在目标麦克风、耳机和三种浏览器完成现场试读。完成这些前置项后，需要执行第二轮真实语音验收。

浏览器 UI 工具本轮因 Windows helper 初始化失败而不可用，因此没有把页面点击、读屏或物理设备行为标为通过。实际联调使用真实 HTTP/WebSocket 服务与代理；模型下载保留约174 MiB断点文件，仍未完成。

## 本轮修复记录

- 补齐缺失 SessionPanel，会话页从构建失败恢复为可编译。
- 修复暂停或打断触发旧 onended 的问题，避免错误推进游标。
- 增加音频解码轮次令牌，旧音频不能在取消后出声。
- 音频识别移出控制锁，并使用有界内存队列，暂停不等待推理完成。
- 修复断线重连的连接归属竞争，以及同连接开始新会话时的队列归属。
- 修复重复 audio_ready 重新计算静音时间，以及相同朗读文本可靠性恢复。
- 依赖锁补齐 sherpa-onnx-core，真实原生识别包可加载。
- 默认模型下载判断检查 tokens 与 encoder/decoder/joiner，避免部分资源误判就绪。
