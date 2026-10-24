# 新服务器部署指南（已安装 Docker）

适用日期：2026-10-24。面向 Linux 服务器，使用两份已构建镜像运行 芯创客无障碍语音提示助手。本文可随交付目录或离线发布包单独阅读。

目标服务器需要 Docker、Compose 插件、Bash 及常见系统工具（gzip、tar、sha256sum）；远程传输/访问示例使用 SSH/SCP，HTTP 检查示例使用 curl。导入镜像运行时不需要安装 Python、uv、Node.js、pnpm，也不需要下载 STT 模型。`scripts/backup.py` 使用容器内 Python 执行。

**“离线包”指镜像可以离线传输，不代表语音提示完全断网可用。** 本地 STT 模型内置，`edge-tts` 合成仍需服务器联网；启用云端语音或文本模型后，还需访问对应 HTTPS/WSS 服务。

## 1. 先确认需要迁移哪些内容

| 内容 | 交付方式 | 是否含私密信息 |
|---|---|---|
| 后端与前端镜像 | 一个 `whisper-guide-image.tar.gz`，内含两个镜像 | 不含现有用户数据和密钥 |
| 启动配置、部署脚本、本文、备份脚本 | 与镜像包放在同一交付目录 | `.env.example` 只含模板 |
| 已有讲稿与模型方案 | 可选：另行迁移 `data/sqlite` 整个目录 | 私有模型方案含 API Key/Token |
| 原运行环境配置 | 可选：另行迁移 `.env` | 可能含密钥 |
| 旧备份 | 按需另行迁移 `data/backups` | 按私密数据保护 |
| 实时会话进度 | 不迁移；新服务器重新准备会话 | 仅在旧进程内存中 |

全新使用无需复制旧 `data`，首次启动会建立数据库。保留旧讲稿和模型方案则先阅读第 8 节；发布镜像不会自动包含它们。不要把私有迁移文件放进公共发布包。

## 2. 在新服务器检查 Docker 和架构

用之后实际部署的同一用户登录服务器：

```bash
docker --version
docker info
docker compose version
uname -m
df -h .
docker system df
```

`docker info` 必须能正常输出服务端信息。只有 `docker --version` 成功不足以证明 daemon 可用。若出现 permission denied，需要由服务器管理员授予该用户 Docker 使用权限，或在该服务器统一使用有权限的账户；不要修改 socket 为全员可写。

需要 `docker compose` 插件，且支持 `up --wait --wait-timeout`。若缺失，先按服务器操作系统的 Docker 安装方式补齐插件。脚本还需要 root 或 sudo 权限，把应用数据目录交给容器 UID `10001`。本文权限命令针对常规 rootful Docker 且未启用 userns-remap；rootless 或自定义用户映射环境需先由管理员确认宿主 UID 映射，不直接套用 `10001`。

当前已验证的镜像平台为 **`linux/amd64`**，对应服务器通常显示 `x86_64`。若为 `aarch64/arm64`，需先准备并验证该架构的镜像，不能直接假定现有镜像可运行。

当前镜像统计：后端 `780,356,360` 字节、前端 `93,538,369` 字节（2026-10-24 状态修复构建读数，后续以实际 inspect 为准）；STT 核心模型已经包含在后端。检查部署目录和 Docker 数据目录所在磁盘的余量，导入还需要解压及数据层空间，不能仅按 gzip 文件大小估算。日志配置为每个服务最多 5 个、每个 10 MB；数据库及备份会随使用增长。

## 3. 从当前机器准备交付物

选择 A 或 B 一条路线即可。本项目根目录指包含 `docker-compose.yml` 的源码目录，例如当前 WSL 的 `/home/timeleafing/projects/whisper-guide`。

### A. 已有验证通过的镜像：直接导出（本次推荐）

这条路线不会重新构建或停止正在运行的容器。镜像标签必须是你准备交付的版本；源码提交不会自动更新镜像。

在构建机项目根目录执行：

```bash
set -euo pipefail
# 不输出运行配置或密钥。
docker image inspect whisper-guide-backend:0.1.0 whisper-guide-frontend:0.1.0 >/dev/null

# 每次使用独立目录，避免混入旧交付物或运行数据。
DELIVERY_DIR="release/delivery-$(date +%Y%m%d-%H%M%S)"
test ! -e "$DELIVERY_DIR"
mkdir -p "$DELIVERY_DIR/scripts"
cp docker-compose.yml .env.example DEPLOY.md "$DELIVERY_DIR/"
cp deploy/deploy.sh "$DELIVERY_DIR/"
cp scripts/backup.py "$DELIVERY_DIR/scripts/"
chmod +x "$DELIVERY_DIR/deploy.sh"

# pipefail 及遇错停止，避免把失败的 docker save 当作完整交付。
docker save whisper-guide-backend:0.1.0 whisper-guide-frontend:0.1.0 \
  | gzip -c > "$DELIVERY_DIR/whisper-guide-image.tar.gz"
(cd "$DELIVERY_DIR" && sha256sum whisper-guide-image.tar.gz > SHA256SUMS)
ls -lh "$DELIVERY_DIR" "$DELIVERY_DIR/scripts"
```

交付目录如下：

```text
delivery-时间戳/
├── whisper-guide-image.tar.gz
├── docker-compose.yml
├── .env.example
├── deploy.sh
├── DEPLOY.md
├── SHA256SUMS
└── scripts/
    └── backup.py
```

直接传输该目录，不再生成第二份大型外层归档。将下面的 `deploy@server.example.com` 替换为你的服务器 SSH 用户和地址，在同一个构建机终端执行：

```bash
ssh deploy@server.example.com 'mkdir -p whisper-guide'
scp -r "$DELIVERY_DIR/." deploy@server.example.com:whisper-guide/
```

随后在服务器执行：

```bash
set -euo pipefail
cd ~/whisper-guide
sha256sum -c SHA256SUMS
```

校验必须显示 `OK`，然后继续第 4 节。此时 `~/whisper-guide` 就是部署目录。

### B. 需要重新构建并生成单一传输 tar

构建机需要完整源码、四份默认 INT8 模型文件和可访问基础镜像/锁定依赖下载源的网络。执行：

```bash
set -euo pipefail
./deploy/package.sh
sha256sum whisper-guide-release.tar > release/whisper-guide-release.tar.sha256
ssh deploy@server.example.com 'mkdir -p whisper-guide'
scp whisper-guide-release.tar release/whisper-guide-release.tar.sha256 \
  deploy@server.example.com:whisper-guide/
```

`package.sh` 会先重新构建前后端，再导出双镜像 gzip，放入外层 `whisper-guide-release.tar`。它会清空构建机原 `release/` 与同名外层 tar，`release/` 只能作为生成目录，不能存放唯一数据或运行中的数据库。

**脚本完成后，构建机 `release/` 中的大镜像 gzip 已被删除，完整镜像在根目录的外层 tar 内。应传输这个 tar，不能直接复制脚本执行后的 `release/` 目录。** 归档过程会短暂同时存在内外文件，完成后立即删除内部副本；空间紧张时使用 A，直接传输交付目录。

本次后端镜像曾使用同锁生产依赖离线核对复用构建成功；全新空缓存下载仍受网络影响。不要把现有镜像成功等同于目标机器能联网重新构建。只有部署需求时选 A 或直接使用已有发布包。

在服务器解包：

```bash
set -euo pipefail
cd ~/whisper-guide
sha256sum -c whisper-guide-release.tar.sha256
# 首次安装使用空目录解包；升级时按第 7 节处理。
tar -xf whisper-guide-release.tar
cd release
```

归档内也包含前述镜像 gzip、Compose、模板、脚本和本文；此时 `~/whisper-guide/release` 是部署目录。后续所有命令均在包含 `docker-compose.yml` 的部署目录执行。

## 4. 初始化配置并启动

确认当前位置后执行：

```bash
set -euo pipefail
test -f docker-compose.yml && test -f whisper-guide-image.tar.gz
test -f .env || cp .env.example .env
chmod 600 .env

# 如需修改端口等初始参数，用编辑器编辑 .env。
# 默认保留 localhost:8080；云端参数也可启动后在模型配置页填写。
mkdir -p data/sqlite data/backups
sudo chown -R 10001:10001 data/sqlite data/backups
sudo chmod -R u+rwX data/sqlite data/backups

docker compose config --quiet
chmod +x deploy.sh
./deploy.sh ./whisper-guide-image.tar.gz

# 等待前后端实际健康；目标服务器不进行构建或镜像拉取。
docker compose up -d --no-build --pull never --wait --wait-timeout 180
docker compose ps
```

若使用 root 账户，可省略命令中的 sudo。若需要保留旧数据，先按第 8 节恢复到这些目录，再启动。

部署脚本会检查 daemon、初始化缺失 `.env`、修复数据目录所有权、导入两个镜像并启动。现有脚本本身没有完整等待两个服务健康，故保留最后的 `--wait` 与后续接口检查。脚本末尾打印端口来自 shell 环境，修改过 `.env` 端口时，以 `docker compose ps` 的端口映射为准。

建议保留的容器路径：

| 设置 | 值与含义 |
|---|---|
| `WHISPER_GUIDE_HTTP_PORT` | 宿主机端口，默认 `8080`，仍仅绑定 `127.0.0.1` |
| `WHISPER_GUIDE_DATA_DIR` | `/data`，对应宿主 `./data/sqlite` |
| `WHISPER_GUIDE_BACKUP_DIR` | `/backups`，对应宿主 `./data/backups` |
| `WHISPER_GUIDE_SPEECH_MODEL_DIR` | 保留模板中的容器内 `/models/...` 路径 |
| `WHISPER_GUIDE_DEFAULT_SILENCE_TIMEOUT_MS` | 默认 `3000`，整数且不小于 `1000` |
| `WHISPER_GUIDE_STT_PROVIDER` | 首次默认 `local`；不代表覆盖已有模型方案 |
| `WHISPER_GUIDE_CLOUD_OPT_IN` | 首次默认 `false`；使用云端音频需显式授权 |

不要将宿主机路径填入容器的模型/数据库路径，也不要增加后端 worker 或后端副本。活跃会话仅由单个进程维护；重启会清空会话，讲稿和方案依靠挂载持久化。

## 5. 从自己的电脑打开前端

### 推荐：SSH 隧道

Compose 默认发布 `127.0.0.1:8080:8080`，服务器公网 IP 的 8080 无法直接访问。在你自己的电脑终端执行并保持窗口打开：

```bash
ssh -N -L 8080:127.0.0.1:8080 deploy@server.example.com
```

然后用自己电脑的浏览器打开：

```text
http://localhost:8080
http://localhost:8080/studio
http://localhost:8080/settings/models
```

这样不需要开放服务器 8080，且浏览器通过 localhost 采集麦克风。若服务器实际发布端口不是 8080，修改隧道中右侧的 `127.0.0.1:端口`；若电脑本地 8080 已占用，可使用 `-L 18080:127.0.0.1:8080`，浏览器改为 `http://localhost:18080`。

### 需要通过公网域名使用

使用现有反向代理把 HTTPS 域名转发到服务器 `127.0.0.1:8080`，配置认证和访问控制，并转发 `/api/` 与 WebSocket Upgrade，保留较长的 WebSocket 读取超时。`WHISPER_GUIDE_CORS_ORIGINS` 按实际来源设置 JSON 数组，例如 `["https://guide.example.com"]`。

本项目目前没有应用级用户认证和讲稿隔离；模型管理接口可修改后端密钥配置。不要仅把端口改为 `0.0.0.0` 就公开服务。普通远程 HTTP IP 页面也可能无法申请麦克风；使用 localhost 隧道或 HTTPS。

## 6. 验证服务，再配置模型

在服务器部署目录执行，`APP_PORT` 与 `.env` 中发布端口一致：

```bash
APP_PORT=8080
curl -fsS "http://127.0.0.1:$APP_PORT/" >/dev/null
curl -fsS "http://127.0.0.1:$APP_PORT/api/health"
curl -fsS "http://127.0.0.1:$APP_PORT/api/speech/status"
docker compose ps
docker compose logs --tail=100 backend frontend
```

服务器没有 curl 时，可用容器内检查健康接口（外部页面和代理仍应从浏览器检查）：

```bash
docker compose exec -T backend /app/backend-core/.venv/bin/python -c \
  "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=5).read().decode())"
```

预期健康接口含 `"status":"ok"`，语音接口中 `stt`、`vad`、`tts` 都为 `ready`。`ready` 是依赖/配置能力检查，不等于云端账号已完成真实识别、TTS 网络已成功或浏览器扬声器正常。

浏览器验收：

1. 上传一份测试讲稿，确认列表及正文可读取。
2. 开始演讲并授权麦克风，朗读讲稿，检查原始识别、已对齐文本与游标。
3. 保持麦克风连接，静音等待到期应提示下一片段；主动“手动提示”确认真实合成和耳机播放。
4. 验证暂停、继续、结束。结束测试会话后再激活其他模型方案。

默认本地 STT 不需要云端 Key。需要双云配置时，在 `/settings/models` 新建方案：

| 环节 | 填写内容 |
|---|---|
| 豆包流式语音 STT | 云端 STT、协议 `volcengine-ws`、`wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async`、已开通语音资源、**语音服务 API Key**、云端音频授权；`api-key` 鉴权不填 App ID |
| 方舟或其他兼容文本 LLM | 单独设置语义模式、Base URL、实际模型 ID、**文本服务 API Key**；可先获取模型列表，再测试调用权限 |

这些端点是当前项目预设，部署时应核对服务商控制台实际开通情况。语音 Key 与方舟文本 Key 不会自动复用；旧 `app-token` 鉴权才使用 App ID 与 Access Token。先分别测试，再保存，结束所有未终止会话（包括暂停会话）后设为生效。

模型方案与生效快照保存在 `data/sqlite/.model-configs.json`，权限 `0600`；它优先于 `.env` 的首次默认值。改 `.env` 不会覆盖已激活方案，复制方案不会复制密钥。语音静音探测只证明协议可达，仍需真实朗读验收。

识别状态说明：源码已修复 VAD 丢失有效 partial、覆盖上次真实可靠结论，以及“待确认/真失配”界面误归类。应用修复需结束演讲后构建并更新前后端镜像；已有旧交付包不会自动包含修复。更新后，待确认时旧文字/分数标为“上次结果/上次匹配”，有效转写为“来”等且无法匹配稿件时仍触发 AC-14；只有声音却没有有效转写时仍保留 pending，不凭过滤或超时强制恢复。可继续朗读附近正文或手动定位；“手动提示”不代表已恢复可靠性。本次不是新增降噪或声学阈值调整，现场杂音与设备效果仍需分别验证。

## 7. 日常停止、升级与回滚

停止服务但保留目录：

```bash
docker compose down
```

升级前结束演讲并执行第 8 节备份。新交付先放进独立接收目录。完成下述回滚标签和 Compose 备份后，才替换镜像包、Compose、模板、脚本和文档；保留原部署目录的 `.env` 与 `data/`，不要用整目录覆盖或删除它们。

当前使用固定版本标签 `0.1.0`。替换任何新文件或导入镜像前，先为实际运行的镜像加唯一回滚标签，并保留旧 Compose（标签不复制镜像数据）：

```bash
set -euo pipefail
ROLLBACK_STAMP=$(date +%Y%m%d-%H%M%S)
BACKEND_CONTAINER=$(docker compose ps -a -q backend)
FRONTEND_CONTAINER=$(docker compose ps -a -q frontend)
test -n "$BACKEND_CONTAINER"
test -n "$FRONTEND_CONTAINER"
BACKEND_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$BACKEND_CONTAINER")
FRONTEND_IMAGE_ID=$(docker inspect --format '{{.Image}}' "$FRONTEND_CONTAINER")
docker tag "$BACKEND_IMAGE_ID" "whisper-guide-backend:rollback-$ROLLBACK_STAMP"
docker tag "$FRONTEND_IMAGE_ID" "whisper-guide-frontend:rollback-$ROLLBACK_STAMP"
cp -p docker-compose.yml "docker-compose.before-$ROLLBACK_STAMP.yml"
printf '记录回滚标签时间戳：%s\n' "$ROLLBACK_STAMP"

# 上述备份完成后，再替换新的公共部署文件与镜像包；保留 .env/data。
set -o pipefail
gzip -dc whisper-guide-image.tar.gz | docker load
docker compose up -d --no-build --pull never --force-recreate --wait --wait-timeout 180
```

若升级失败，选择先前记录的时间戳，并恢复匹配的 Compose 配置：

```bash
set -euo pipefail
ROLLBACK_STAMP=20261024-120000  # 替换为实际保存的时间戳
docker tag "whisper-guide-backend:rollback-$ROLLBACK_STAMP" whisper-guide-backend:0.1.0
docker tag "whisper-guide-frontend:rollback-$ROLLBACK_STAMP" whisper-guide-frontend:0.1.0
cp -p "docker-compose.before-$ROLLBACK_STAMP.yml" docker-compose.yml
docker compose up -d --no-build --pull never --force-recreate --wait --wait-timeout 180
```

镜像回滚不会自动回滚数据库或模型方案；有数据格式变化的版本需另核对备份兼容性。确认新版本稳定后，再按明确标签清理旧镜像及旧传输包。保留备份，不使用全局删除卷的清理命令。

## 8. 备份与迁移已有数据

### 只备份讲稿 SQLite（服务运行时可执行）

在部署目录使用容器内 Python，无需宿主机安装 Python：

```bash
BACKUP_NAME="whisper-guide-$(date +%Y%m%d-%H%M%S).db"
docker compose exec -T backend /app/backend-core/.venv/bin/python - \
  /data/whisper-guide.db "/backups/$BACKUP_NAME" < scripts/backup.py
```

脚本使用 SQLite backup API，验证 `quick_check=ok`，拒绝覆盖同名备份。文件在宿主 `data/backups` 下，容器 UID `10001` 拥有；通过有权限的账户管理备份。SQLite 备份不包含模型配置、密钥或 `.env`。

### 完整迁移讲稿和模型方案

先结束会话，在旧服务器部署目录执行下列 Bash 子 shell；任何一步失败即中止：

```bash
(
  set -euo pipefail
  umask 077
  PRIVATE_ARCHIVE="whisper-guide-private-$(date +%Y%m%d-%H%M%S).tar.gz"
  test ! -e "$PRIVATE_ARCHIVE"
  : > "$PRIVATE_ARCHIVE"  # 先以 0600 创建，tar 写入期间也不公开凭据。
  docker compose stop
  sudo tar -czf "$PRIVATE_ARCHIVE" .env data/sqlite
  sudo chown "$(id -u):$(id -g)" "$PRIVATE_ARCHIVE"
  chmod 600 "$PRIVATE_ARCHIVE"
  docker compose start
  printf '私有迁移包：%s\n' "$PRIVATE_ARCHIVE"
)
```

若 tar 失败，保留原数据并排查后再恢复服务。整个 `data/sqlite` 会包含隐藏的 `.model-configs.json`、旧兼容文件及可能的 SQLite 附属文件；不要使用 `data/sqlite/*` 遗漏隐藏文件。需要历史备份时另行迁移 `data/backups`。

将私有归档通过受控 SSH/SCP 传到目标部署目录，保持 `0600`；不要将其发布、提交到 Git 或贴出内容。目标目录必须与第 4 节使用的部署目录相同。

在新服务器恢复前先停止服务、移开原目录，防止旧 WAL/SHM 混入。下面的 Bash 子 shell 在失败时中止并保持服务停止，使用明确的备份副本，不删除原数据：

```bash
(
  set -euo pipefail
  PRIVATE_ARCHIVE=whisper-guide-private-20261024-120000.tar.gz  # 替换为实际文件
  RESTORE_STAMP=$(date +%Y%m%d-%H%M%S)
  test ! -e ".env.before-$RESTORE_STAMP"
  test ! -e "data/sqlite.before-$RESTORE_STAMP"
  test -f "$PRIVATE_ARCHIVE"
  sudo tar -tzf "$PRIVATE_ARCHIVE" >/dev/null
  docker compose stop
  [ ! -f .env ] || cp -p .env ".env.before-$RESTORE_STAMP"
  [ ! -d data/sqlite ] || sudo mv data/sqlite "data/sqlite.before-$RESTORE_STAMP"
  sudo tar -xzf "$PRIVATE_ARCHIVE" .env data/sqlite
  sudo chown "$(id -u):$(id -g)" .env
  chmod 600 .env
  sudo mkdir -p data/sqlite data/backups
  sudo chown -R 10001:10001 data/sqlite data/backups
  sudo chmod -R u+rwX data/sqlite data/backups
  docker compose config --quiet
  docker compose up -d --no-build --pull never --force-recreate --wait --wait-timeout 180
)
```

恢复前应先确认私有归档来自可信旧服务器、可正常列出且含 `.env` 与 `data/sqlite`；如中途失败，保持服务停止并从 `before-时间戳` 目录恢复，不带着不完整数据启动。迁移后复查 `.env` 中端口、来源、容器路径以及新服务器的云端访问权限。

检查原讲稿列表、当前生效方案及分别测试 STT/LLM；仅看到密钥掩码不代表新服务器已有调用权限。新环境的实时会话需要重新准备。

## 9. 常见问题

| 现象 | 操作 |
|---|---|
| `docker info` 失败 | 检查 daemon 与当前账号权限；不要只看 CLI 版本 |
| `docker compose` 不存在 | 安装或启用与服务器 Docker 配套的 Compose 插件 |
| `exec format error` | 核对 `linux/amd64` 与服务器 CPU 架构 |
| 找不到 `Dockerfile` / 尝试联网构建 | 先加载两份镜像，启动加 `--no-build --pull never`；部署机不需要源码 |
| 镜像 gzip 不存在 | A 路线传整个交付目录；B 路线传根目录 tar 并在目标解包，不能传构建机已删 gzip 的 release |
| SQLite 打不开、backend unhealthy、保存配置 503 | 检查 `data/sqlite`、`data/backups` 对 UID `10001` 的权限与磁盘余量 |
| 服务器 IP:8080 打不开 | 默认仅 localhost；按第 5 节建立 SSH 隧道 |
| 页面有了但无麦克风 | localhost/HTTPS、浏览器授权、麦克风设备 |
| 已进入播报但没有声音 | edge-tts 网络、浏览器播放权限、耳机输出、最近播放错误 |
| STT unavailable | 检查容器模型路径；内置模型部署无需在服务器补装大型 SDK |
| 匹配提示反复变化或不播报 | 先确认镜像已包含第 6 节状态修复，再区分 pending/真实 mismatch；旧分数是历史信息，持续背景声音仍需现场排查 |
| 无法激活模型 | 结束 READY/LISTENING/PROMPTING/PAUSED 会话；包括断线恢复入口的未结束会话 |
| 重启后讲稿或方案丢失 | 确认一直从同一部署目录启动、原 `data` 挂载完整；仅实时会话应因重启丢失 |

本文对应源码中默认双容器、单后端 worker 配置。发布只包含公共配置与应用镜像；本轮状态修复已完成源码回归与镜像构建，尚未替换原运行容器；发布包需另行导出，切换后重新准备会话验收。
