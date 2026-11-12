# HotRadar 开源项目核查与借鉴决定

2026-10-14重新检索，并通过GitHub连接器检查仓库元数据、许可证及选定源码。下表为当前方案依据，历史对话中的泛化项目名不再作为选型结论。全部候选在核查时均未标记归档；最近推送时间只反映仓库活动，不证明接口持续可用。

## 候选比较

| 项目 | 最近推送日期 UTC | 本次许可证 | 可借鉴内容 | HotRadar决定 |
|---|---|---|---|---|
| [NewsNow](https://github.com/newsnext/newsnow) | 2026-10-13 | MIT | 来源注册、平台/子榜单拆分、卡片刷新与状态、拖拽、源适配器 | 首要参考；按模块适配，保留Next.js/FastAPI |
| [DailyHotApi](https://github.com/imsyy/DailyHotApi) | 2026-03-11 | MIT | 多平台解析、JSON/RSS响应、缓存、榜单参数 | 第二套解析参考与部分备用方案；不把通用hot字段直接入库 |
| [DailyHot](https://github.com/imsyy/DailyHot) | 2024-11-05 | MIT | 榜单卡片、更多、失败重试、更新时间、设置持久化 | 只借鉴交互，Vue组件不直接搬入Next.js |
| [TrendRadar](https://github.com/sansan0/TrendRadar) | 2026-10-13 | GPL-3.0 | 排名历史、首次/末次采集、来源状态、词频筛选与解释性评分 | 参考数据模型与方法，当前不复制其代码 |
| [RSSHub](https://github.com/DIYgod/RSSHub) | 2026-10-14 | AGPL-3.0 | RSS路由生态与来源扩展 | 可选外部Feed服务，非首期依赖；不嵌入其源码 |
| [MediaCrawler](https://github.com/NanmiCoder/MediaCrawler) | 2026-10-10 | 非商业学习使用许可证1.1 | 小红书/短视频内容数据采集结构 | 不列入MCN项目可直接复用名单；实际采用需解决商业授权 |
| [TopList](https://github.com/tophubs/TopList) | 2023-02-23 | Apache-2.0 | Go采集与聚合卡片的早期实现 | 次要结构参考，年代较早；不是已证实的tophub.today源码 |

许可证取自本次LICENSE与仓库元数据，尤其RSSHub主分支已是AGPL-3.0，不能沿用旧印象中的MIT。该表记录选型边界，不代替后续复制具体文件时的许可与版权标注核对。[RSSHub LICENSE](https://github.com/DIYgod/RSSHub/blob/a7faf8049727d0edb163ae6abe5126b7317d0f31/LICENSE)、[MediaCrawler LICENSE](https://github.com/NanmiCoder/MediaCrawler/blob/main/LICENSE)

## 首要参考 NewsNow

本次固定版本为`0560c993b226cd2fd07d8a79875d50551f52c584`，主分支最近提交为2026-10-13的v0.0.43发布。它使用React、Vite、TanStack与Nitro体系，并非Next.js；因此借鉴组件和接口组织，不整体替换HotRadar架构。[package.json](https://github.com/newsnext/newsnow/blob/0560c993b226cd2fd07d8a79875d50551f52c584/package.json)

- `server/utils/source.ts`与`shared/types.ts`：统一来源协议、RSS转换和类型边界。
- `server/sources/bilibili.ts`：明确区分搜索词、热门视频和排行榜，对HotRadar的事件/内容分离很有价值。
- `src/components/column/card.tsx`：单卡刷新、更新时间、获取失败、原文入口和按榜单类型显示。
- 抖音连接器仅输出话题ID、标题和/hot/链接；微博连接器含硬编码Cookie，不能直接沿用。详见[数据接入方案](DataSources.md)。

README宣布下一代NewsNext并暂不接受贡献，但当前仓库仍有近期发布。因此可将固定版本作为参考，不依赖其未来路线或作者公共实例保证HotRadar生产运行。[README](https://github.com/newsnext/newsnow#contributing)

## 第二参考 DailyHotApi 与 DailyHot

DailyHotApi固定版本`36c77e3bd891c11642d314cfb229bf31646704de`。它的`src/routes/`按平台组织，`src/utils/cache.ts`提供内存/Redis缓存；适合参考适配器拆分与缓存边界。它没有替HotRadar解决持续七天快照、跨平台事件与MCN内容发现。

`src/routes/bilibili.ts`的新旧接口映射与缺失值处理需要重新定义；`src/routes/douyin.ts`是话题热榜，不等于具体爆款视频。[B站源码](https://github.com/imsyy/DailyHotApi/blob/36c77e3bd891c11642d314cfb229bf31646704de/src/routes/bilibili.ts)、[抖音源码](https://github.com/imsyy/DailyHotApi/blob/36c77e3bd891c11642d314cfb229bf31646704de/src/routes/douyin.ts)

DailyHot固定版本`8b91ad9d35f7dad3b0c2a0b671001a9de9873115`，最近推送为2024年。其`src/components/HotList.vue`可参考卡片的加载、重试、展开与更新时间处理；结合现有Next.js重新实现，不增加Vue运行时。[卡片组件](https://github.com/imsyy/DailyHot/blob/8b91ad9d35f7dad3b0c2a0b671001a9de9873115/src/components/HotList.vue)

## 历史分析参考 TrendRadar

固定版本`ceabb027fe9bbdafec59dbbfa12cb46c2ba256ce`，2026-10-13更新至v6.11.0。源码确认：

- `trendradar/storage/schema.sql`含`news_items`、`rank_history`、`crawl_records`与`crawl_source_status`，可参考观测历史及来源状态分离。
- `trendradar/core/analyzer.py`按排名、出现频次和高排名占比计算权重；这是关注度规则，不是已经验证的未来爆款预测。
- `trendradar/crawler/fetcher.py`默认调用NewsNow API；部署TrendRadar并不等于拥有独立平台采集器。

HotRadar独立设计PostgreSQL表和计算逻辑，借鉴首次/末次观测、排名时间线与可解释评分；推送机器人、MCP及报告分发不在首期范围。[历史表](https://github.com/sansan0/TrendRadar/blob/ceabb027fe9bbdafec59dbbfa12cb46c2ba256ce/trendradar/storage/schema.sql)、[数据入口](https://github.com/sansan0/TrendRadar/blob/ceabb027fe9bbdafec59dbbfa12cb46c2ba256ce/trendradar/crawler/fetcher.py)

## 与旧项目的复用

本机`D:\Coding\Ai-news`保持原样。本次检查后的复用优先级为：

| 文件或能力 | 决定 |
|---|---|
| ingestion/rss.py与registry.py | 可提取RSS条件请求、解析与注册设计，改用HotRadar协议 |
| articles/normalization.py | 可借鉴URL规范化；查询参数保留需按平台逐个验证 |
| jobs/scheduling.py | 借鉴按采集间隔分桶的幂等任务方式 |
| events/clustering.py | 只借鉴关系与可追溯思路；中文整段token匹配不足以直接完成热点聚类 |
| events/scoring.py | 不沿用AI类别、口播价值及AI受众权重；按MCN热点重新设计 |
| 写稿、研究、视频制作流程 | 首期不迁入 |

## 核查证据

仓库状态、固定提交与已读文件清单保存在`sources/research-2026-10-14.json`；真实来源抽样保存在`sources/source-probes-2026-10-14.json`。本次没有运行候选应用，也没有逐平台验证它们在HotRadar部署环境下的全部接口。

旧“Open Aggregator”“AI News Aggregator”“Trend Finder”“Social Media Trend Tracker”没有从历史引用中取得准确仓库，名字存在歧义，暂不入选；Socioboard的账号管理方向不属于首期需求。这不构成“GitHub没有完整匹配项目”的绝对结论。

## 环境初始化参考

基础入口按官方文档建立：[FastAPI First Steps](https://fastapi.tiangolo.com/tutorial/first-steps/) 与 [Next.js Installation](https://nextjs.org/docs/app/getting-started/installation)。实际安装版本以 uv.lock 与 frontend/package-lock.json 为准。
