# 开发与构建指南

目标服务器导入镜像和离线包见[部署手册](../DEPLOY.md)，维护见[运维指南](maintenance.md)。

## 开发环境

apps/frontend与apps/backend-core是两个独立应用，分别维护package.json/pnpm与pyproject.toml/uv依赖。以下cd命令均从仓库根目录执行；两个终端不共用应用工作目录。deploy和根scripts用于部署/运维，数据、备份、模型及release发布产物不混入应用源码。

在WSL/Linux项目目录使用Python 3.12、uv、Node.js ^22.18.0或>=24.12.0，以及package.json指定版本的pnpm。不要混用Windows与WSL的虚拟环境和node_modules。

后端：

```bash
cd apps/backend-core
uv sync --locked
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

另一个终端启动前端：

```bash
cd apps/frontend
pnpm install --frozen-lockfile
pnpm dev
```

浏览器访问localhost:5173，后端OpenAPI在localhost:8000/docs。Vite将/api和WebSocket代理到8000；pnpm preview使用4173并保留代理。VITE_API_PROXY_TARGET覆盖开发/预览代理目标，可在apps/frontend/.env或对应模式文件中设置，启动进程中的同名环境优先；修改后重启Vite。VITE_API_BASE_URL用于不同API地址，会进入前端构建，修改后需重新构建，不能放密钥。

后端默认读取apps/backend-core/.env，需要覆盖时复制同目录.env.example；相对数据、备份和模型路径按apps/backend-core解析（不是按仓库根目录或当前工作目录解析）。监听地址与端口使用Uvicorn命令行参数。源码开发默认数据与备份分别为apps/backend-core/data和apps/backend-core/backups；已有自定义绝对路径继续使用原值；本地默认数据、模型和私有配置随应用目录保留，Docker宿主持久目录data不迁移。容器内/app/backend-core与/data、/backups、/models路径及Compose挂载不变。

## 从旧目录更新

源码根目录的frontend和backend-core现移入apps；分别从apps/frontend、apps/backend-core运行开发命令。Playwright的../backend-core仍指向同级后端，不需要改成仓库根目录。Docker只改变构建输入路径，Compose服务名、镜像名、容器/app/backend-core和data挂载不变。

Git只移动版本化文件。其他机器更新后，旧目录可能留下未跟踪的.env、data、backups或models：先确认旧文件与新位置，再将需要保留的本地资源移到对应应用目录；新位置已有内容时先比较和备份，不覆盖数据库、模型方案或密钥。自定义绝对路径继续使用原值；相对路径现在基于apps/backend-core，使用旧相对跨目录路径时需重新核对。Docker宿主持久目录data不需要移动。

Python虚拟环境包含旧绝对路径，不能直接搬迁后继续使用；在apps/backend-core重新创建.venv，再运行uv sync --locked。前端在apps/frontend运行pnpm install --frozen-lockfile，缓存和依赖不进入Git。本次工作区已将本地资源随应用一起保留，并重建了Python环境；其他检出或部署机器需按各自文件状态处理。

## 项目标题

统一使用后端环境变量WHISPER_GUIDE_APP_TITLE，默认whisper-guide；未设置、空字符串或纯空白均回到默认。导航品牌、浏览器路由标题和API文档标题采用同一值。标题是纯文本，不作为HTML执行，也不属于模型方案配置。

Docker在项目根目录.env加入：

```dotenv
WHISPER_GUIDE_APP_TITLE="我的语音提示助手"
```

首次使用新版本按构建与发布步骤更新镜像。此后只改标题时，结束会话后执行：

```bash
docker compose up -d --no-build --pull never --force-recreate --wait --wait-timeout 180 backend
```

刷新浏览器后生效，不需要重编译前端。不要只用docker compose restart，它不会读取新的容器环境。离线包同样读取部署目录.env，旧.env不会被模板自动覆盖。

源码开发时在apps/backend-core/.env设置同名变量并重启Uvicorn，前端不需要VITE标题变量。页面启动异步请求/api/app-config；失败时保留默认标题，不影响工作台。固定服务标识、数据库、会话存储键和Docker项目名不随显示标题改变。

## STT模型

```bash
cd apps/backend-core
uv run python scripts/download_asr_model.py
```

从后端目录运行时，模型默认位于models/sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20，即仓库内apps/backend-core/models下，不进Git。脚本验证归档路径、类型及重复项，只保留tokens.txt、INT8 encoder、FP32 decoder和INT8 joiner。decoder必须优先FP32：现用模型的INT8 decoder在原始中文对照中会产生明显叠字，不能只按体积选择。

下载中断保留断点，可通过--url或WHISPER_GUIDE_MODEL_URL指定归档镜像。已有旧INT8-only目录也应重新运行脚本：只补约13.9MB的FP32 decoder，校验大小和固定SHA-256后原子安装；失败保留旧可用模型。单文件镜像使用--decoder-url或WHISPER_GUIDE_DECODER_URL。已经有FP32 decoder时重复执行不联网，并清理未选权重、官方测试音频与固定残留归档。保留来源说明，不手工按通配符删除onnx。

模型未就绪时讲稿功能仍可使用，开始识别明确失败。使用sherpa-onnx、WebRTC VAD与edge-tts，不需要torch、transformers或CUDA；edge-tts仍需网络。

## 构建与发布

```bash
# 项目根目录；只构建，不重启服务
docker compose config --quiet
docker compose build

# 制作离线发布包：先按DEPLOY.md完成prepare、版本提交和标签创建。
# 仅在v0.1.2标签对应的干净工作区执行：
./deploy/package.sh --tag v0.1.2
```

package.sh从Git标签生成release/0.1.2/linux-amd64/whisper-guide-release-0.1.2-linux-amd64.tar及同名.sha256，版本来自标签，不再写死0.1.0；拒绝脏工作区、版本不一致、已有版本镜像与产物覆盖。根Compose默认dev标签，WHISPER_GUIDE_VERSION/REVISION仅用于源码构建；发布包Compose固定版本且不包含build。完整prepare→commit→tag→push操作、Actions发布、服务器部署与回滚见[部署手册](../DEPLOY.md)。第一版仅稳定X.Y.Z、linux/amd64，不支持预发布后缀或ARM。

Docker前端构建先检查类型；后端只安装锁定生产依赖。运行层不含开发工具、测试和模型下载脚本，固定单worker。构建前准备模型，模型层位于业务源码之前。Docker固定复制FP32 decoder；旧模型目录缺此文件时先运行模型更新脚本，再构建镜像。忽略规则不能误排除Python的app/models包；数据、凭据与测试产物不进入镜像。

发布工具只用Python 3.12标准库、Git、uv、Docker和gzip，不新增应用运行依赖。prepare只同步项目自身版本，保留第三方依赖解析结果，并用uv lock --locked --offline严格验证。prepare失败回滚前后端清单与uv.lock；pnpm锁不存根应用版本，无需随版本号改写。ci.yml支持workflow_call检查指定SHA；release.yml的package任务依赖checks成功，publish任务仅在打包后获contents:write并上传草稿附件后发布。当前流程不推镜像仓库，不远程部署；不要把个人密钥塞进公开工作流。

发布Compose使用卷长语法保存`type/source/target`，防止Compose 2.38.2在`--no-interpolate`规范化短语法时把`${VAR:-default}`目标截断。生成后仍须用部署.env重新解析，确认运行默认值与相对宿主挂载完整；不能只验证渲染步骤退出成功。兼容验证需覆盖Compose 2.38.2及本机版本，具体结果见[测试记录](testing.md)。

修复已推标签中的代码缺陷后，不要直接重跑旧标签期待使用main新代码；保留失败标签，用新版本提交和新标签发布。`v0.1.2`首次发布失败后的`v0.1.3`示例及定位日志的方法见[部署手册](../DEPLOY.md#发布失败后的恢复)。

## 环境变量与生效顺序

2026-10-27按公开模板与代码逐项核对。变量为空不等于无效：例如云端地址、密钥可在部署环境或模型方案中填写；是否使用取决于对应能力是否启用。不要把生产.env、完整docker inspect或完整compose config粘贴到日志/工单，可能包含凭据。

先确认编辑的是哪份文件：

| 运行方式 | 配置入口 | 读取者与时机 |
|---|---|---|
| Docker源码构建/运行 | 仓库根.env | Compose读取已声明的插值变量；构建元数据在build时，容器环境在创建时传入 |
| 离线发布包 | 解包部署目录.env | 随包deploy.sh显式选择同目录.env，使用已校验的包内镜像重建容器 |
| WSL/Linux直接运行后端 | apps/backend-core/.env | 后端Settings启动时读取，进程环境优先于此文件 |
| 前端开发/预览 | apps/frontend/.env、.env.local、.env.[mode]及启动进程环境 | VITE_API_BASE_URL进入构建；VITE_API_PROXY_TARGET由Vite加载，用于开发/预览代理，模式文件优先于通用文件、进程环境优先；后端WHISPER_GUIDE变量不由Vite直接读取 |

Compose的导出Shell变量仍优先于.env；例如当前终端曾export同名旧值，编辑文件后该Shell值仍可能覆盖它。检查时只查看所需非敏感键，不输出全部环境。源码启动后端不会自动读取仓库根.env；根.env也不会自动把任意自定义键传入容器，必须在Compose的environment/env_file中显式传递。

### 构建与编排变量

| 变量 | 作用与边界 |
|---|---|
| WHISPER_GUIDE_VERSION | 源码Compose镜像标签及Docker构建APP_VERSION，默认dev；不是运行时应用设置。正式包由Git标签生成并固定镜像版本，此变量不能切换包内版本 |
| WHISPER_GUIDE_REVISION | 源码构建VCS_REF/OCI提交标签，默认unknown；正式包由已验证Git SHA生成，不控制业务 |
| WHISPER_GUIDE_HTTP_PORT | 宿主发布端口，默认8080；不修改容器内部8080端口，也不改变后端Uvicorn监听8000 |

### 启动参数与页面默认值

这些字段不属于持久模型方案。修改运行参数后重新创建容器或重启直接运行的后端；标题、会话提示默认和上传限制由页面刷新获取，其他字段由各自后端/编排消费者读取。已有会话参数和用户当前编辑值不会被迟到配置覆盖。

| 变量 | 当前消费者与规则 |
|---|---|
| WHISPER_GUIDE_APP_TITLE | 后端OpenAPI标题与公开页面标题，空白回退whisper-guide；前端运行时读取 |
| WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS | 新会话初始静音等待，默认3000且>=1000整数；通过公开配置同步工作台，创建会话使用相同值；手动设置和恢复会话保留自身参数 |
| WHISPER_GUIDE_DEFAULT_PROMPT_MODE | 新会话提示模式，silence（默认）或progress；进度模式允许有声无进展时接管 |
| WHISPER_GUIDE_DEFAULT_PROGRESS_TIMEOUT_MS | 新会话进度等待，默认4000，整数2000–10000；与静音和云端超时独立 |
| WHISPER_GUIDE_DEFAULT_CONTINUOUS_PROMPT | 进度模式是否持续代读，默认false；静音模式保持原逐段静音续播 |
| WHISPER_GUIDE_SCRIPT_MAX_FILE_BYTES | 单份讲稿上传上限，正整数；默认2097152。后端强制校验，页面同步显示和预检查 |
| WHISPER_GUIDE_SCRIPT_MAX_UPLOAD_FILES | 单次上传数量上限，1–1000整数；默认10，超过框架multipart上限会在启动时拒绝配置。后端强制校验，页面同步预检查 |
| WHISPER_GUIDE_CORS_ORIGINS | 后端允许的HTTP来源JSON数组；不提供身份认证，不替代HTTPS或反向代理访问控制 |
| WHISPER_GUIDE_DATA_DIR | SQLite与私有模型方案的容器内目录；源码相对路径按apps/backend-core解析，Docker应使用绝对容器路径 |
| WHISPER_GUIDE_BACKUP_DIR | 本次修复后用于Docker备份挂载的容器内目标，默认/backups；源码后端仅保留兼容路径字段/解析值，没有备份消费者。不是定时/自动备份开关，backup.py仍由destination参数指定输出 |
| WHISPER_GUIDE_SPEECH_MODEL_DIR | 后端查找本地STT权重的目录；容器内默认/models/...。改变量不会下载、复制或挂载模型，新路径必须实际存在并可读 |
| WHISPER_GUIDE_SPEECH_SAMPLE_RATE | 当前协议约束，只允许16000；不是声卡采样率调节旋钮，其他值启动校验失败 |
| WHISPER_GUIDE_TTS_VOICE | edge-tts提示合成声音，默认zh-CN-XiaoxiaoNeural；与STT/LLM方案独立，需要有效语音名称和后端联网 |

Docker的宿主数据位置仍是部署目录data/sqlite和data/backups。DATA_DIR/BACKUP_DIR改变的是容器内挂载目标，不会迁移宿主数据；切换运行方式、自定义模型挂载或相对跨目录路径时，需核对实际路径与UID10001权限。不得通过删除data刷新页面默认值。

静音值曾存在前端固定3000覆盖后端默认的缺陷，上传页面也曾固定10份/2MiB。本次修复从服务端公开配置加载这些值，配置响应不包含模型密钥；未手动调整的新会话省略客户端超时，由后端采用当前默认，即使公开配置请求失败也不覆盖后端。不能仅用环境值已进入容器来推断旧前端已正确使用。

Nginx原固定22MiB请求体上限已移除，由后端执行文件大小与数量校验，避免提高上传变量后仍被代理提前拒绝。上传限制不替代访问控制；部署到可信访问范围。

### 模型方案的初始默认值

下列字段是模型配置的环境引导值。没有私有方案仓储时，初始“现有配置”取环境设置；已有.model-configs.json时，其独立生效快照优先。改变.env不会覆盖用户已保存/激活的方案，也不会让未激活草稿自动生效。请在模型配置页面保存并激活需要的修改，不删除方案文件或数据库强制刷新。

| 变量 | 作用与启用条件 |
|---|---|
| WHISPER_GUIDE_STT_PROVIDER | 选择local、cloud、hybrid或已注册ID；云端还需CLOUD_OPT_IN=true与可用注册配置，未授权时保持本地 |
| WHISPER_GUIDE_CLOUD_OPT_IN | 是否允许向云端发送音频；地址和密钥齐全也不能绕过该授权 |
| WHISPER_GUIDE_CLOUD_STT_PROTOCOL | http-json或volcengine-ws，决定语音传输协议；不是文本模型协议 |
| WHISPER_GUIDE_CLOUD_STT_ENDPOINT | 语音服务地址，公网使用HTTPS/WSS；仅地址有值不能保证服务配置完整 |
| WHISPER_GUIDE_CLOUD_STT_AUTH_MODE | api-key或app-token；后者还需要App ID，前者不要求App ID |
| WHISPER_GUIDE_CLOUD_STT_RESOURCE_ID | 原生WebSocket服务资源编号，需与账户开通项匹配；HTTP JSON网关不使用该字段 |
| WHISPER_GUIDE_CLOUD_STT_TIMEOUT_MS | 云端识别连接/传输/结果等待超时，默认5000、整数200–30000；与静音等待、文本LLM超时独立 |
| WHISPER_GUIDE_CLOUD_STT_ENABLE_NONSTREAM | 原生bigmodel_async二遍复核开关；其他端点不使用，不控制本地识别 |
| WHISPER_GUIDE_CLOUD_STT_APP_ID | app-token鉴权的App ID；api-key鉴权可留空 |
| WHISPER_GUIDE_CLOUD_STT_ACCESS_TOKEN | api-key模式的独立语音API Key，或app-token模式的Access Token；仅后端使用 |
| WHISPER_GUIDE_LLM_ALIGN_MODE | off/fallback/hybrid；非off且BASE_URL与MODEL完整后才可调用语义校准 |
| WHISPER_GUIDE_LLM_PROVIDER_PRESET | 方案/界面的服务商预设ID和填表辅助元数据；只设qwen/openai等不会自动填地址、模型或启用调用 |
| WHISPER_GUIDE_LLM_BASE_URL | 文本Chat Completions兼容API根地址，实际地址由该字段决定，不由预设名称决定 |
| WHISPER_GUIDE_LLM_MODEL | 文本模型ID或服务商接入点ID；需与该地址及密钥匹配 |
| WHISPER_GUIDE_LLM_API_KEY | 文本模型独立凭据；不自动复用语音密钥 |
| WHISPER_GUIDE_LLM_TIMEOUT_MS | 单次文本辅助请求超时，默认1500且为正整数，不修改静音等待 |

### 后端兼容配置

`WHISPER_GUIDE_ENVIRONMENT`已从两份模板和Compose中移除：后端仍兼容读取并校验development/test/production，但没有业务消费者，不切换日志、调试、安全或识别行为。旧安装保留该键不会改变行为，production也不会自动提供认证或HTTPS。

| 变量 | 作用与优先级 |
|---|---|
| WHISPER_GUIDE_STT_PROVIDER_CONFIGS | 服务器注册的语音Provider JSON数组；仍有实际消费者，不是废弃字段。注册表不保存进模型方案，需重建/重启后端读取；每个条目受全局云端授权约束，原生/HTTP条目按自身协议参数处理 |
| WHISPER_GUIDE_CLOUD_ENDPOINT | 旧单Provider地址别名；新部署优先用CLOUD_STT_ENDPOINT。规范字段有值时优先，旧字段用于兼容 |
| WHISPER_GUIDE_CLOUD_APP_ID | 旧App ID别名；新部署使用CLOUD_STT_APP_ID |
| WHISPER_GUIDE_CLOUD_ACCESS_TOKEN | 旧语音凭据别名；新部署使用CLOUD_STT_ACCESS_TOKEN |

旧持久文件的cloud_*字段会规范化为cloud_stt_*；同一更新中规范字段显式提供（包括空字符串清除）优先，避免旧别名重新填回已清除的值。不要同时维护两套不同的地址/密钥。

STT_PROVIDER_CONFIGS中access_token_env读取的是后端进程环境。Docker根.env只用于Compose插值，随意写MY_PROVIDER_TOKEN并不会自动注入容器；需在自定义Compose中显式转发该键，或按标准CLOUD_STT字段/模型页面配置凭据。不要为图省事把整个私有.env放进镜像或公开发布包。

模型下载脚本另支持WHISPER_GUIDE_MODEL_URL、WHISPER_GUIDE_DECODER_URL，仅下载工具执行时使用，不是运行中识别源切换。VITE_API_BASE_URL与VITE_API_PROXY_TARGET也不是容器后端配置：前者修改后需重新构建前端，后者只控制Vite开发/预览服务器，修改后重启对应服务器，不改变Nginx容器。

### 修改后如何确认

1. 编辑正确运行目录的.env；仅变运行参数无需删除镜像或数据。已导出的同名Shell值优先，必要时在当前终端取消该项旧导出。
2. 结束会话，在该部署目录重新创建容器：docker compose --env-file .env up -d --no-build --pull never --force-recreate --wait --wait-timeout 180。仅docker compose restart不会重新注入环境。
3. 刷新浏览器，在工作台创建新会话确认默认值；恢复旧会话时以其快照参数为准。公开/api/app-config可核对标题、静音默认和上传限制，不含密钥。
4. 如果修改了模型方案环境值，先分辨是否由已有生效方案覆盖；按模型配置页面的保存/激活流程更新。
5. 如果部署的仍是旧发布包镜像，deploy.sh只load包内镜像并重建，不编译当前源码。需要重新构建/发布包含修复的新包，再部署并刷新页面。
