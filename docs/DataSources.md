# HotRadar 首期数据接入方案

「创作参考」使用抖音创作者中心全国创作热点榜 `douyin-creator-hot`。官方榜单请求使用 `hot_search_type=1`，省略城市参数。榜位和热度分别来自 `rank` 和 `hot_score`，榜单更新时间与采样时间分开保存。全国榜历史从首次真实采集开始。

2026-10-29更新：已按用户要求接入百度实时热搜。正式适配器读取 `https://top.baidu.com/board?tab=realtime&platform=pc` 网页的 `s-data` 数据，仅接受 `realtime` 热搜卡片；首次生产采样51条含置顶1条。`word`为源内标识与标题，普通 `index + 1` 为榜位，置顶保留标记且无普通榜位，`hotScore`映射为百度提供的热搜指数原值，缺失为null。`updateTime`为榜单更新时间，不是各话题发布时间；采样时间另存。原文保留官方搜索链接，原始业务响应和解析版本 `baidu-hot-v1` 可追溯。无需Cookie；默认15分钟，可独立启停并保留历史。进入热榜、事件和趋势，不进入具体内容池。详见验收（本地留档 `BaiduBoardAcceptance.md`）。本条取代下方百度暂缓的历史状态。

2026-10-19 16:13更新：原生无锡榜现已通过官方 `so-landing.douyin.com/aweme/v1/hot/search/list/` 接入，board_type=1、board_sub_type=320200、aid=581610、version_code=32.3.0。正式批次50条，独立 `douyin-wuxi-app`，已启用15分钟采集；校验城市、榜单标签和逐行城市榜参数，并与全国结果对照。无需Cookie。指标为hot_value同城热度，非播放量；榜单active_time和本机采样时间分别保存。详见验收（本地留档 `DouyinAppWuxiAcceptance.md`）。本条取代下一段关于App榜待验证的旧状态，原创作者中心来源仍独立保留。

2026-10-19纠正：`douyin-wuxi` 为创作者中心同城创作热点，当前产品名称已纠正，非抖音App原生无锡榜。App榜接入仍待验证；网页响应即使出现无锡城市名，若条目仍为全国榜也必须排除。详见来源核查（本地留档 `DouyinAppWuxiSourceAudit.md`）。

2026-10-15补充：微博无锡榜已通过正常登录会话接入 `m.weibo.cn/api/container/getIndex` 的 `filter_type=region` 容器。官方城市配置无锡代码800863202，响应标签与每条话题的城市证据均须一致。保留源内榜位、同城热度、实际原文和采样；不以全国标题过滤替代，也不将通用total元数据当作实际采集条数。Cookie仅本机DPAPI加密，分别按微博自有.com/.cn域作用域发送。真实批次及边界见微博无锡榜验收（本地留档 `WeiboWuxiBoardAcceptance.md`）。

2026-10-14 核查。第一阶段采用按来源隔离的 Python 适配器，优先平台公开端点与公开 RSS。开源仓库提供解析线索，接口是否持续可用以 HotRadar 的实际运行记录为准。

## 来源优先级

| 来源与榜单 | 实际内容类型 | 当前核查结果 | 首期安排 |
|---|---|---|---|
| 百度实时热搜 | 搜索话题 | 2026-10-29生产适配器真实51条，含置顶1条 | 当前启用；默认15分钟，长期稳定性待观察 |
| B站热门视频 | 具体视频 | 历史单次抽样20条；没有生产适配器 | 暂缓 |
| 微博热搜 | 搜索话题 | M1真实入库与页面验证，匿名端点；两轮51/52条 | 当前启用；连续稳定性观察中 |
| 抖音热榜 | 热点话题 | M1匿名临时会话链路、入库与页面验证，两轮各50条 | 当前启用；不承诺视频统计 |
| 抖音无锡同城榜 | 本地热点话题 | 2026-10-15创作者中心正常会话实测，城市配置无锡320200；首批2条，与正常页面一致 | 默认15分钟，独立快照与来源设置；非具体视频 |
| 知乎热榜 | 问题/讨论 | 有开源线索，未生产验证 | 暂缓 |
| 公开RSS | 新闻/文章 | Ai-news有历史参考代码，未接入HotRadar | 暂缓 |
| 小红书、快手、视频号的具体内容 | 笔记/短视频 | 当前未取得可验收的授权接口与指标 | 不计入首期已接入范围；接口落实后增加 |

样本结果见 `sources/source-probes-2026-10-14.json`。上述HTTP200只证明当次请求，不能代替持续可用性、业务覆盖或平台数据授权验证。

用户在UI评审后原先指定只接抖音、微博，2026-10-29确认增加百度；其他平台继续暂缓。历史探测文件保留为参考，不用于当前实现证明。M1证据见M1-verification.json。

## M1实际入口与字段

- 微博：`https://weibo.com/ajax/side/hotSearch`，匿名读取，校验ok=1和data.realtime列表。word为标题/源内ID，rank为0起始源排名，num保留为search_heat（热搜指数，非播放量）；广告保持标记而不分配热搜排名。word_scheme用于搜索链接，onboard_time如有则为来源事件时间。
- 抖音：先请求平台login_guiding_strategy匿名端点，再用同一内存HTTP客户端请求`https://www.douyin.com/aweme/v1/web/hot/search/list/`。校验status_code=0和data.word_list列表，sentence_id为话题ID，position为源排名，hot_value为topic_heat（话题热度，非播放量），event_time是来源事件时间，不能冒充视频发布时间。
- 两平台当前不提供具体视频、作者、播放、点赞等内容指标。published_at保持null；上游未提供榜单更新时间则upstream_updated_at=null。解析版本weibo-hot-v1/douyin-hot-v1。
- Cookie只由抖音平台给匿名客户端临时发放，在请求客户端销毁时丢弃。没有复制仓库Cookie或读取用户浏览器会话。HTTP头和凭据不写入业务响应；Age>0记录from_cache。
- 非JSON、HTTP错误、业务码或结构错误标失败；合法空列表才记empty，部分坏行记partial。默认900秒、每榜单并发1、单任务最多三次重试。

上述是网页端点的本次可用性结果，没有持续稳定性承诺；24小时观察尚未完成。免费自采当前不需要购买官方聚合API。

## 历史候选抽样入口（百度已按上方说明正式接入）

- B站：`https://api.bilibili.com/x/web-interface/popular`。这是平台域名下的网页端接口，本次可匿名读取，不称为有稳定性承诺的开放API。保存`stat.view/like/reply/favorite/share/coin`等原值，而非NewsNow已经格式化的展示字符串。
- 百度：`https://top.baidu.com/board?tab=realtime`。解析网页内`<!--s-data:...-->`，保留`hotScore`、榜单序号与置顶标记。`hotScore`标为“百度热搜指数”，不标为播放量。

API URL、解析版本与样本字段写入来源注册表；解析字段缺失或结构改变时报告异常。兼容改动先以新样本验证，再更新适配器版本。

## 开源连接器的适配边界

NewsNow提供统一来源协议，但部分平台只映射标题、ID和链接。例如抖音连接器没有将输入的`hot_value`带到输出。HotRadar需要从原始响应保留可解释的指标，不能将聚合API没有提供的值臆造回来。[抖音连接器](https://github.com/newsnext/newsnow/blob/0560c993b226cd2fd07d8a79875d50551f52c584/server/sources/douyin.ts)

DailyHotApi使用通用`hot`字段，B站新旧接口映射含义不完全一致，并存在缺失值退化为0的写法。HotRadar适配时应拆成明确指标、保留null和来源版本；不能直接继承通用热度作为跨平台量纲。[B站连接器](https://github.com/imsyy/DailyHotApi/blob/36c77e3bd891c11642d314cfb229bf31646704de/src/routes/bilibili.ts)

NewsNow当前微博连接器包含硬编码Cookie。本项目不复制或使用该值；使用可验证的匿名入口或用户自己的合法授权，凭据只保存在本机配置。[微博连接器](https://github.com/newsnext/newsnow/blob/0560c993b226cd2fd07d8a79875d50551f52c584/server/sources/weibo.ts)

## 今日热榜官方数据服务

站点明确指向[榜眼数据](https://www.tophubdata.com/)作为官方API开放平台。[公开文档](https://www.tophubdata.com/documentation)列出榜单目录、最新详情、历史数据集、搜索、榜中榜、快照和热点日历等接口。

采用可选`TopHubProvider`隔离接入，不把官网页面抓取作为生产系统主数据源。API需通过Authorization传访问密钥；快照列表与详细内容计费不同，文档对详细快照给出按快照计费说明。本次只读公开文档，未注册、未调用带密钥接口、未消费额度。

历史数据集接口返回指定历史日期上榜条目集合，文档明确不返回当天数据；它不同于逐次完整榜单快照，不能直接还原分钟级排名曲线。历史补齐应使用有时间戳的快照，并保存数据提供方、粒度和导入范围。

页面示例的`items.extra`被定义为任意附加内容；即使有视频条目，也不能据此承诺抖音播放、点赞和作者增长等字段。启用前核实具体节点、字段样本、历史覆盖、更新频率、额度及商业使用授权。

若希望从上线第一天就看到此前七天历史，需取得可导入的历史快照；否则自然留存七天。免费自采为初始方案，官方服务为后续可选择的覆盖补充，不预设采购。

## 统一数据协议

每条至少记录：`source_id`、`board_id`、`item_kind`、`external_id`、`original_url`、`canonical_url`、`title`、`source_rank`、`observed_at`、`upstream_updated_at`、`published_at`、`metrics`、`raw_payload`、`parser_version`。

- `item_kind`区分topic、question、article、video、note；URL结构辅助识别但不能唯一决定类型。
- `source_rank`是来源实际给出的排名，置顶/广告单独标记，页面顺序与源排名分离。
- `metrics`逐项含名称、数值、单位、提供方和口径；缺失为null，源中真实0保留为0。
- `observed_at`表示本机采样时间；`published_at`是内容发布时间。榜单收录时间、热点发生时间和首次观测时间不可互相冒充。
- 原始响应只保存业务数据；请求凭据、Cookie、Authorization不进入快照或日志。

## 单源验收

每个来源需留下脱敏真实样本、字段映射、失败样本、重试策略、限频与授权说明，经过数据库与页面验证。建议连续观察24小时，再标记为稳定候选；只在README列出不算接入完成。

同一平台通过两个聚合提供方返回的相同榜单属于一份证据，不算两个平台。缓存副本不构成独立观测，来源失败也不意味着热点消失。

## M3来源与摘要边界

M3没有新增外部来源、付费API或平台账户。事件和摘要只由已保存的抖音话题榜、微博热搜及本机采样记录生成；摘要摘录标题并概括观测覆盖，没有取得新闻正文，也没有调用LLM。每条引用保留SnapshotItem ID、原始链接、源内榜位、指标口径及采样时间；数字不跨平台相加。

展示“最后出现榜位”和是否仍在选定日期的末次榜单，避免把退出榜单的旧记录当成当前排名。重复或缓存采样可以是多条留存记录，但不能当成多次独立传播。来源暂停、失败、过期及部分解析在事件页仍可见；历史页面采用当前人工修正关系。

具体爆款内容只在取得真实视频/图文对象、作者、发布时间、链接与可用互动字段后另行接入。目前两平台已接入的是topic，具体内容池继续留空。当前来源、24小时稳定性和七天覆盖不能由本次事件功能验收代替。

上述为M3验收时的边界。2026-10-14随后完成具体内容来源核查（本地留档 `ContentSourceAudit.md`）：正常浏览器页面分别核对一条抖音视频和一条微博帖子，独立留存为manual-browser-review并进入只读内容池。其后微博正常访客会话及抖音页面SDK实际精选POST接口均验证通过，新增两平台自动内容采集。原人工观察保留，播放量及增长仍缺失，候选不代表全网爆款榜。

## 正常会话具体内容（本轮更新）

微博使用本人本机正常网页获得的访客Cookie，请求 https://weibo.com/ajax/feed/hottimeline 的statuses及 https://weibo.com/ajax/statuses/show 的真实帖子详情。启用前需两端返回ok=1，完整内容ID、作者、发布时间及可用互动字段，并校对两端身份/时间。四次真实采集留存35条自动观察；默认30分钟由原前台Worker执行，页面只读。推荐会变化且可能个性化，不代表全网榜；广告与不完整条目跳过并计数。

字段映射：user.id/idstr及screen_name为作者；user ID与mblogid组成原文链接；created_at保存秒级UTC和源时区，页面显示北京时间；attitudes_count/comments_count/reposts_count分别为点赞/评论/转发整数原值。缺失为null，真实0保留；text_raw优先，长文可能为摘录，不生成播放或增长。业务原始字段经过白名单筛选，保留采样、任务和数据SHA-256。

抖音前一轮详情GET返回403的记录保持历史时点。本轮直接观察正常页面SDK：精选实际接口为POST https://www.douyin.com/aweme/v2/web/module/feed/，module_id=3003101、tag_id为空为全部分类、count=20。GET对照404不能代表POST失败；携本机Cookie/实际UIFID按正确方法请求，HTTP200、status_code=0且aweme_list字段完整，无签名回放/伪造。

抖音映射：aweme_id形成真实视频链接，author.nickname/sec_uid为作者/主页，create_time为秒级Unix时间，statistics.digg_count/comment_count/collect_count/share_count为点赞/评论/收藏/分享原值。业务白名单保存以上字段、类型及广告标记；非视频、广告及不完整字段跳过。SDK轻量响应可能只提供点赞，其他值保持缺失；本机完整POST实测可取得四项互动。来源描述可能被截断或含版本提示，明确标注摘录。精选推荐可能个性化并含旧内容，不代表全平台爆款排行。

三批自动采集留存57条抖音观察，默认30分钟；微博继续运行。原文作者/发布时间与内容池已核对，幂等、独立停启及真实字段回放通过。最新证据见SDK验收（本地留档 `DouyinSdkAcceptance.md`），播放量、增长与爆款判定尚未验收。

网页扫码完成后，会话以独立来源的 Fernet 密文保存，API 和 Worker 读取同一私有目录。旧 Windows 安装保留对当前用户 DPAPI 文件的兼容。仅保存对应平台域 Cookie，采集请求使用 HTTPS 且不跟随重定向。凭据不进入业务响应或日志，内容会话失效或被上游拒绝时自动暂停并保留历史。操作方式见[采集授权与服务器迁移](Development.md#采集授权与服务器迁移)。

## M4趋势数据

M4没有新增外部请求来源。所有曲线点来自已有快照，状态来自实际任务、快照覆盖和配置记录；新留存由原抖音/微博Worker继续执行。排名与源内热度原值、原文和观测时间保留。无快照日期不生成曲线，成功榜单未见条目也不生成热度0。

观测日数只是有采样的日期数，空榜快照也可证明当日发生过成功采样，但不证明该日有事件或全天覆盖。缓存标记沿用现有HTTP Age提示，未标缓存不保证上游不存在其他缓存机制；记录数不等于独立传播次数。不同周期、失败、缺口、部分解析、口径改变或基期0不能伪造增速。

当前只取得今天14:49后的历史，页面如实显示1/7个日期有采样，后续自然留存后动态变化。没有导入旧项目数据库、第三方历史集或模拟七天数据；七天连续覆盖和24小时稳定性仍需实际运行验收。
