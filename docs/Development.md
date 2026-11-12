# HotRadar 本地开发环境

## 采集授权与服务器迁移

1. 在 API 部署目录执行 `uv sync --locked --extra browser-auth` 和 `uv run --locked python scripts/configure-platform-sessions.py`。生成的 `HOTRADAR_SESSION_KEY` 用于加密会话，`HOTRADAR_SESSION_ADMIN_TOKEN` 用于打开扫码窗口。也可由部署环境注入这两项。API 和 Worker 使用同一个加密密钥和持久会话目录，默认 `data/sessions`，可用 `HOTRADAR_SESSION_DIR` 指定。
2. Linux API 主机执行 `uv run --locked --extra browser-auth playwright install-deps chromium` 安装系统依赖，再用 API 服务账户执行 `uv run --locked --extra browser-auth playwright install chromium`。API 以可运行 Chromium 沙箱的非 root 用户启动，授权期间相关请求需落在同一个 API 进程。单机使用一个 Uvicorn Worker 即可。Windows 依次尝试已安装的 Edge、Chrome 和 Playwright Chromium，也可通过 `HOTRADAR_BROWSER_CHANNEL` 指定浏览器。
3. 对外部署时为 Next.js 设置 `HOTRADAR_PUBLIC_ORIGIN=https://实际工作台域名`（无尾斜杠），`HOTRADAR_API_URL` 指向内网或回环 API。通过 HTTPS 反代工作台，API 端口仅对工作台和受信环境开放。代理转发 `Origin` 和 `Authorization`，验证请求超时至少留 90 秒，登录请求体和画面应排除在访问日志之外。回环访问可省略 `HOTRADAR_PUBLIC_ORIGIN`。
4. 重启 API、Worker 和工作台。从“来源管理”或“爆款内容”进入“采集授权”，选择来源，填入管理令牌并打开扫码窗口。用对应平台 App 扫码。如果平台要求验证码，在弹窗中的输入框直接键入，或按住滑块拖动。微博同城在同一窗口打开同城页并选择无锡。
5. 完成登录后点“已登录，验证并启用”。服务器验证并留存首批真实数据后加密保存会话。更新验证失败时保留此前保存的会话和数据。关闭窗口、验证成功或十分钟到期后释放临时浏览器。

Windows 原有 `*.dpapi` 仍供原 Windows 用户读取。Linux 需要重新登录并导入一次。之后换机可迁移 `*.fernet` 密文和原加密密钥，分别通过私有渠道转移；平台可能要求新服务器重新登录。管理令牌可在迁移时重新生成。已有历史仍由 PostgreSQL 保存，迁移历史需单独迁移数据库。

抖音总榜优先匿名，匿名失败或空榜时可用已验证会话。微博热搜、百度实时热搜继续匿名。抖音原生无锡榜独立匿名采集，如果部署机反复出现 `city_unverified`，可给 API/Worker 配置受信的 `HOTRADAR_DOUYIN_WUXI_PROXY` 无锡出口，仅作用于原生无锡榜请求。配置后仍需通过城市名、城市编号、榜单标签和逐条榜位上下文校验。可运行 `uv run --locked python scripts/verify-douyin-app-wuxi.py` 做真实验证。

## 百度实时热搜（2026-10-29）

执行 `uv run --locked hotradar-seed` 注册 `baidu-hot`，新建时默认启用15分钟；重复注册保留既有周期及暂停设置。更新后重启后端、Worker并构建/重启前端；桌面入口在未运行时可自动完成启动。无需新增依赖、凭据或数据库迁移，百度不会创建具体内容任务。页面来源管理支持15/30/60分钟和独立启停；历史自首次实际采样开始。只读核查执行 `uv run --locked python scripts/verify-baidu-board.py`，输出 `data/baidu-probe/verification.json`。参见验收（本地留档 `BaiduBoardAcceptance.md`）。

## 微博无锡榜（2026-10-15）

已有正常本机会话、且官方同城页已选择无锡时，使用 `uv run --locked python scripts/verify-weibo-wuxi-board.py` 只验证访问；加 `--activate` 才启用并通过正式分发另取真实批次入库。结果写入忽略的 `data/weibo-wuxi-probe/verification.json`。新环境seed默认禁用此榜；必须先重启到包含无锡分发的新Worker，再验证启用，避免旧版本按平台总榜采集。既有运行配置不会被seed重置。

登录过期时，在“采集授权”弹窗扫码登录，打开同城页选择无锡后验证。旧 Windows 环境仍支持 DPAPI。界面来源管理可独立调整周期或暂停，暂停保留历史；微博总榜和具体内容任务独立。实际数据、页面与测试证据见验收（本地留档 `WeiboWuxiBoardAcceptance.md`）。

Python3.12、uv、Node.js24/npm、Git和Docker。后端与前端使用项目独立依赖，不复用Ai-news运行目录。

## 初始化

```powershell
# 在克隆后的 HotRadar 仓库根目录执行
.\scripts\setup.ps1
.\scripts\init-project.ps1
```

setup安装锁定的Python开发依赖和前端npm依赖。init生成缺失的本机.env、启动专属PostgreSQL、应用迁移、注册来源并执行到期采集。可重复执行，保留既有密码与数据。.env.example无密钥。

PostgreSQL镜像postgres:16-alpine，容器hotradar-postgres-1，卷hotradar_hotradar_postgres，只绑定127.0.0.1:18814，不占用其他项目的5432/5433。不要使用docker compose down -v清除已留存数据。

## 启停

### 桌面启动

桌面双击“启动热点雷达”快捷方式，自动启动本项目数据库、后端、网页和采集进程，确认网页与数据库代理就绪后打开默认浏览器 http://127.0.0.1:18811/ 。启动窗口隐藏。重复点击时复用与当前项目文件相符的进程，文件或前端依赖更新后重启启动器管理的对应进程。电脑需保持开机，关闭浏览器不会停止采集。

入口由`./scripts/create-desktop-shortcut.ps1`生成，实际执行`./scripts/start-desktop.ps1`。首次缺少依赖或网页构建时会安装/构建；数据库仍使用原持久卷，现有来源设置和本机会话保留。启动失败会显示提示，日志位于`.runlogs/desktop/`，不纳入Git。

停止此启动器创建的后端、网页及采集进程（保留数据库和历史）：

```powershell
# 在 HotRadar 仓库根目录执行
.\scripts\start-desktop.ps1 -Action Stop -NoBrowser
```

手动启动的既有进程仍由原窗口停止。未创建Windows服务、计划任务或开机自启动。2026-10-15已验证首次启动、重复启动复用、隐藏进程、停止后再次通过桌面入口启动及真实内容页面读取。

### 开发窗口

三个窗口分别执行dev-backend.ps1、dev-frontend.ps1、dev-worker.ps1。默认端口18810、18811；话题榜默认900秒采集，两平台已验证具体内容默认1800秒，页面30秒读库。HotRadar页面不直接请求平台。

关闭窗口/Ctrl+C停止进程；Worker停止后不再采样，页面最终标记陈旧。docker compose stop postgres停止数据库并保留卷；docker compose up -d --wait postgres恢复。

后端支持-Port；变更后在frontend/.env.local设置HOTRADAR_API_URL。默认仅127.0.0.1，不开放局域网/外网。未创建Windows系统服务、计划任务或自启动项。

## 验证

```powershell
uv run --locked --extra dev ruff check backend migrations tests
uv run --locked --extra dev pytest -q
uv run --locked --extra dev alembic check
Set-Location frontend
npm.cmd run typecheck
npm.cmd test
npm.cmd run build
```

测试需本项目数据库可用；每次创建并清理唯一测试schema，隔离真实快照，不输出密码。健康页检查真实数据库连接，不能单凭健康页证明采集及页面正确。

实际数据、浏览器与重启证据见M1验收（本地留档 `M1-Acceptance.md`）。历史核查脚本依赖本地留存的验收记录与原始档案。

M2已新增来源配置API和审计迁移；升级仍执行init-project.ps1，既有启停和周期不被seed覆盖。浏览器本地偏好与采集配置互相独立，页面来源管理可启停两平台并选择15/30/60分钟。无需新增依赖，npm与uv锁文件保持原依赖版本。

只读核查当前工作区真实快照与M1基线：`uv run --locked --extra dev python scripts/verify-live-data.py`，输出到忽略的data/M2-live-verification.json；新建空库不具备旧基线，不能用该核查证明历史迁移。此脚本不触发采集，不输出Cookie或数据库密码。

## M3事件与摘要

升级仍执行init-project.ps1；迁移20261014_0003新增事件、成员、审计、摘要版本及处理标记五表，不覆盖原始采集记录。Worker每轮将待处理快照聚合并保存来源摘要；页面只读库，人工操作通过本机同源代理写入。

已有快照的独立回填命令（不访问上游）：

```powershell
uv run --locked --extra dev python -m hotradar.events
uv run --locked --extra dev python -m hotradar.events --refresh-summaries
```

前一条只处理未标记快照；后一条还按当前摘要版本回填各实际日期，内容签名相同不重复保存，旧摘要保留。正常Worker处理新快照不需要手动命令。

本项目已有M1/M2基线时可执行`uv run --locked --extra dev python scripts/verify-m3-data.py`，只读核对原始响应、旧快照、事件成员、摘要输入、指标、审计及归档哈希，输出data/M3-live-verification.json。新建空库应使用独立测试和自己的真实数据验收，不能继承此项目旧基线。

本次人工操作前另存PostgreSQL备份到data/backups/M3-before-corrections.dump，该文件仅在本机且被Git忽略。运行方式和依赖未改变，仍无Windows服务或自启动；测试、截图及结果见M3验收（本地留档 `M3-Acceptance.md`）。

## M4趋势与大屏

M4复用现有数据表，无新增依赖、锁文件变更或迁移。更新代码后重启后端与前端即可，Worker可继续留存；新环境仍通过init-project.ps1初始化。进入“趋势观察”，默认近七天，不足七天明确标注。完整日期窗口与实际采样缩放共用原始点，不导入模拟历史。

## 具体内容来源核查后的运行

具体内容迁移20261014_0004及会话配置/任务迁移20261014_0005，需要先运行`uv run alembic upgrade head`，再重启后端、Worker并构建/重启前端。当前微博热门与抖音精选全部分类均已验证并启用独立30分钟任务。新增抖音适配器不需要新迁移或依赖。没有新增系统服务或页面Cookie输入。新环境内容池为空且内容来源默认关闭，需自己的正常会话验证后启用。

已有浏览器人工核查样本保存在data/content-source-probes/browser-samples.json。导入使用`uv run python scripts/import-verified-content.py data/content-source-probes/browser-samples.json`；先验证完整字段和本机截图哈希，再事务写入，不执行外部请求。重复导入同一证据不增加记录。

本地归档 `docs/content-sources/browser-samples.json` 是历史验收样本副本；仅用于明确的证据回放，不能当成新的实时采集。截图需仍存在且哈希匹配。没有凭据或真实证据时不要填入示例对象。

验证：`uv run python scripts/verify-content-data.py`转发至verify-session-data.py，核查两条原证据及截图哈希、所有已留存会话观察、数据库与后端/前端代理，并验证读前后数量不变。当前工作区基线要求两来源已验证且启用；新环境需使用自己的真实来源验收。`uv run pytest tests/test_douyin_content.py tests/test_content_sessions.py tests/test_contents.py tests/test_api.py -q`在独立schema测试。来源匿名探测可执行`uv run python scripts/probe-content-sources.py`，不会自动覆盖docs中旧核查证据或导入内容。

页面“爆款内容”以采样日期查看已保存的精选/热门候选，支持平台及内容/作者搜索。页面刷新只读本机数据库；播放量缺失、源分钟/秒精度、描述摘录、采样时刻、原文、任务和核查依据均明确展示。最新会话实测见抖音SDK验收（本地留档 `DouyinSdkAcceptance.md`）。

## 正常会话具体内容启停

### 抖音无锡话题榜（2026-10-15）

热榜总览的抖音卡片可切换总榜单/无锡榜。`douyin-wuxi`复用现有表但有独立周期、任务、原始业务证据和历史快照，默认15分钟；来源管理可单独暂停/恢复。本机已验证并启用，新环境seed只注册且默认关闭，取得创作者中心正常会话后执行：

```powershell
uv run --locked python scripts/verify-wuxi-board.py --activate
```

不传`--activate`仅验证真实访问。每次采集先核查官方city_code_list确实包含无锡320200，再读取同城榜；不会回退总榜或其他城市。会话失效时任务失败且页面明确提示，保留历史；通过正常页面刷新本机会话，不将Cookie粘贴到聊天或Git。内容池原任务与微博不变。

本轮不把同城榜自动加入事件/趋势的既有成员池。验证详情见无锡榜验收（本地留档 `WuxiBoardAcceptance.md`），本机业务核查记录在忽略的`data/wuxi-probe/`。没有新增依赖、迁移或系统服务。

Windows 兼容流程可通过本机私有进程输入交给 `scripts/save-platform-session.py`，密文位于 `data/sessions/weibo.dpapi` 与 `douyin.dpapi`，绑定原 Windows 用户。新服务器使用[采集授权与服务器迁移](#采集授权与服务器迁移)。

已有有效本机会话时分别验证并启用：

```powershell
# 在 HotRadar 仓库根目录执行
uv run --locked python scripts/verify-content-session.py --source weibo --activate
uv run --locked python scripts/verify-content-session.py --source douyin --activate
```

微博真实请求热门推荐和详情，抖音使用正常SDK确认的精选全部分类POST端点；仅完整字段及持久化成功后启用。缺省--source仍为weibo，默认生成新的幂等任务键；可传--key复核，同一已完成键不会再次请求，跨平台重用同一键被拒绝。Worker必须保持运行，关闭窗口停止后续采样，不保证电脑关机后继续执行。

独立暂停具体内容（话题榜不受影响）：

```powershell
uv run --locked python scripts/pause-content-source.py weibo
uv run --locked python scripts/pause-content-source.py douyin
```

每条命令仅暂停对应具体内容来源；verification.controls保留记录，历史观察保留。可通过“采集授权”重新验证恢复，也可运行该平台验证命令。会话失效或上游拒绝自动暂停该来源；失败任务不将缺失数量写为0，不输出原响应凭据。抖音精选接口使用有效会话的实际 UIFID，后台 HTTP 采集可独立于登录浏览器运行。

GET /api/v1/trends?start=YYYY-MM-DD&end=YYYY-MM-DD读取覆盖及事件；GET /api/v1/trends/events/{UUID}使用同样日期参数读取来源序列。Next.js /api/trends为只读代理。界面支持当日、前一日、近七天和最多31日自定义，榜位叠加但源内指标分开；大屏轮播默认关闭，可主动开始或暂停，退出大屏停止轮播。

已有本项目历史基线时执行`uv run --locked --extra dev python scripts/verify-m4-data.py`，只读核查全部当前事件趋势与原始响应、旧快照、修正审计和归档。运行导出data/M4-live-verification.json，正式时点记录及截图见M4验收（本地留档 `M4-Acceptance.md`）。核查可能需数十秒，输出实际已检查事件数，不触发采集或更改关系。

43个后端测试（42项全量及一个补充午夜测试，错误处理复验）、15个前端测试及类型/构建/Ruff/Alembic检查已通过。24小时连续稳定性和七天连续覆盖尚未通过；阶段功能交付与自然日运行验收分开，不以单次接口成功、日格存在或数据库行数代替。
