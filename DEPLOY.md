# 新服务器部署与版本发布指南

本项目使用 Git 标签关联源码、前后端镜像和离线包。当前正式发布支持稳定版本 `vX.Y.Z` 和 `linux/amd64`；预发布后缀、ARM 架构会明确拒绝。历史 v0.1.0/v0.1.1 不重新打标或覆盖。

## 1. 版本对应关系

以 v0.1.2 为例：

| 对象 | 对应值 |
|---|---|
| Git 标签 / GitHub Release | v0.1.2 |
| 前后端声明版本 | 0.1.2 |
| 镜像 | whisper-guide-backend:0.1.2、whisper-guide-frontend:0.1.2 |
| 离线包 | whisper-guide-release-0.1.2-linux-amd64.tar |
| 包校验文件 | 同名.tar.sha256 |
| 来源证明 | release.json记录完整Git SHA、平台、镜像ID与OCI版本/提交标签 |

只有从标签对应的干净工作区构建才允许发布；清单和uv.lock版本必须一致。发布包内Compose固定两个镜像版本，无build配置，不受旧.env中版本变量影响。源码根Compose默认使用dev标签，供本地构建。

## 2. 维护者：准备并触发自动发布

在包含本流程的主分支代码中，从干净工作区执行。构建机使用Python 3.12和uv；prepare只改声明版本和锁文件，不提交、不创建标签：

```bash
python3 scripts/release.py prepare 0.1.2
git diff
git add apps/frontend/package.json apps/backend-core/pyproject.toml apps/backend-core/uv.lock
git commit -m "chore: prepare v0.1.2"
git push origin main
git tag -a v0.1.2 -m "Release v0.1.2"
git push origin v0.1.2
```

分支不叫main时替换为实际分支。prepare只调整项目自身版本，不升级或重新解析第三方依赖；同步uv.lock中的虚拟项目版本后运行uv lock --locked --offline验证，失败回滚这三个文件。需先安装uv，不能强行跳过锁校验发布。pnpm-lock.yaml不记录根应用版本，prepare不修改它，依赖变更仍由pnpm维护。

推送标签触发.github/workflows/release.yml：

1. 校验稳定版本格式、标签指向当前提交、前后端及uv.lock版本一致、工作区干净。
2. 调用可复用ci.yml，检查同一SHA的后端、前端和浏览器流程。
3. 检查成功后下载并验证ASR模型，构建带版本/SHA标签的双镜像。
4. 打包固定Compose、镜像、清单和校验文件。
5. 独立发布任务获得contents:write，确认远程标签未移动，创建Release草稿、上传tar和.sha256，再发布。

在GitHub仓库Actions查看release流程。仓库策略须允许checkout、setup-python、setup-node、setup-uv、upload-artifact、download-artifact及写Release；无需配置个人访问令牌，工作流使用GITHUB_TOKEN。此流程不推送GHCR，也不SSH到服务器部署。发布默认不修改GitHub的Latest选择。

检查/打包失败不会创建公开Release；已有Release永不覆盖。上传失败可能留下草稿，排查后在Actions手动运行release并选择同一个既有标签前，先审查草稿和附件：当前流程拒绝重写任何已有Release，必要时只删除失败草稿，不移动已发布标签。手动运行支持tag输入；运行于主分支工作流时也会检出并验证该标签，不会拿主分支HEAD冒充旧版本。

GitHub自动提供的Source code压缩包只是源码，不含离线镜像，请下载版本化tar和对应.sha256。

## 3. 本机构建同样的发布包

安装Docker/Compose、Bash、gzip、Python 3.12；使用已打标签的干净源码，先按完整源码仓库docs/development.md准备ASR模型：

```bash
python3 scripts/release.py metadata --tag v0.1.2
./deploy/package.sh --tag v0.1.2 --platform linux/amd64
```

输出位于release/0.1.2/linux-amd64/：tar和同名.sha256。不清空整个release目录、不prune镜像、不覆盖已有版本镜像或完成的发布包。若本地已有同版本镜像，脚本会拒绝构建；优先从GitHub下载已发布产物，或在独立构建机处理失败构建留下的标签，不改写已经交付的版本。模型/依赖/基础镜像下载需要网络，目标部署机导入镜像不需要这些开发工具。

归档根目录包含：

```text
VERSION
release.json
SHA256SUMS
whisper-guide-image.tar.gz
docker-compose.yml
.env.example
deploy.sh
verify_release.py
DEPLOY.md
scripts/backup.py
```

私有.env、SQLite、备份、模型凭据和录音不会放进发布包。内部镜像gzip只存在于外层tar，完成后不额外保留第二份大副本。校验和能发现损坏，不代替对GitHub仓库与发布来源的信任。

## 4. 新服务器：下载、校验与解包

服务器需要Docker Engine、Compose插件、Bash、gzip、tar、sha256sum；支持up --wait --wait-timeout。当前仅linux/amd64，服务器常见uname输出x86_64。确保Docker权限和磁盘空间（镜像导入需额外解压空间）：

```bash
docker info
docker compose version
uname -m
df -h .
```

从同一GitHub Release下载tar及.sha256到一个临时下载目录，不把用户.env或data放在下载目录。校验、查看路径后解到新部署目录：

```bash
sha256sum --strict -c whisper-guide-release-0.1.2-linux-amd64.tar.sha256
tar -tf whisper-guide-release-0.1.2-linux-amd64.tar
mkdir -p ~/whisper-guide
tar -xf whisper-guide-release-0.1.2-linux-amd64.tar -C ~/whisper-guide
cd ~/whisper-guide
cp .env.example .env
chmod 600 .env
# 用编辑器检查端口、CORS、标题和网络设置，勿把密钥提交到Git。
./deploy.sh
```

部署脚本必须从解包目录运行，不能直接用源码目录deploy/deploy.sh部署dev镜像。它先核对内部文件白名单和SHA256SUMS，再load双镜像，比较VERSION、Git SHA、镜像ID、OCI标签、架构与固定Compose。验证器只挂载版本/清单/Compose/校验器四个只读文件，不挂载.env或data，使用无网络的只读临时容器。验证失败不启动服务，也不创建/覆盖用户配置和数据库。

通过后保留已有.env，缺失时才复制模板；初始化data/sqlite与data/backups并要求容器UID10001可写，然后使用--no-build --pull never --wait启动。需要root/sudo设置目录权限；rootless/userns-remap环境请管理员核对映射，不直接套用UID。Compose固定服务名backend/frontend和项目名whisper-guide。

## 5. 运行配置、网络和检查

端口默认8080，通过WHISPER_GUIDE_HTTP_PORT配置；当前对外监听，公开访问前需防火墙、HTTPS与认证保护。麦克风需要localhost或HTTPS。系统没有用户认证与讲稿权限隔离，仅部署到可信访问范围。显示标题WHISPER_GUIDE_APP_TITLE默认whisper-guide。

本地STT模型已内置，不代表完全离线：edge-tts合成需要服务器联网；云端STT/文本LLM也需访问各自HTTPS/WSS地址。语音API Key与文本模型密钥独立，不在日志/命令行中打印。配置含模型凭据，按私密信息保护。

```bash
docker compose ps
docker compose logs --tail=100 backend frontend
curl -fsS http://localhost:8080/api/health
cat VERSION
```

健康检查只证明服务能力，不证明真实麦克风准确率或扬声器可听。设备验收见完整源码仓库docs/user-guide.md；随包部署手册之外的链接在完整源码仓库中阅读。日志每服务最多5个、每个10MB，SQLite和备份需自行监控余量。

## 6. 升级与回滚：始终保留当前数据路径

升级前结束演讲会话（服务端内存进度不持久化），备份现有.env、data/sqlite、data/backups及当前版本的发布文件/镜像ID。讲稿与私有方案都在data/sqlite目录，不能仅复制数据库而漏掉隐藏模型配置。运行数据库请用SQLite备份工具或先停止服务再复制，不用在线裸拷贝。

下载新版本到独立临时目录并校验，解包检查后，**只把上述发布文件白名单复制到原部署目录**（包括scripts/backup.py），不要复制.env或覆盖data，也不要从空的新目录直接启动同名项目造成空库。原目录运行新deploy.sh完成版本校验和重建。备份中保存旧VERSION、Compose、release.json、SHA256SUMS、脚本和对应双镜像，以便回滚。

回滚同样在原部署目录：结束会话并备份当前数据，恢复上次发布文件并运行旧版本包的deploy.sh。版本校验不能证明数据库格式向后兼容；若新版本迁移了数据库，应结合对应版本备份和迁移说明处理，不能盲目用旧程序读新结构。不要删除Docker卷或使用docker system prune -a --volumes来升级。

历史v0.1.0/v0.1.1包不含新manifest：继续使用其随包原脚本，或升级为完整的新版本包。新deploy.sh会拒绝缺失VERSION/manifest/校验和的旧包，不悄悄跳过校验。

## 7. 备份与迁移

根scripts/backup.py使用SQLite在线备份API；可用容器内Python运行，不需要宿主Python。完整操作见源码仓库docs/maintenance.md。备份应包含.env和私有模型方案文件（0600）并限制访问，不能上传公共Release。

跨服务器迁移时仅部署相同架构镜像，另行迁移data/sqlite、需要保留的data/backups与.env。新服务器启动前核对UID写权限、CORS、HTTPS入口与外部服务网络。保持一个Uvicorn worker，会话不跨进程共享。
