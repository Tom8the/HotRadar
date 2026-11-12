"use client";
import { useEffect, useRef, useState } from "react";
import { ExternalLink, FileCheck2, RefreshCw } from "lucide-react";

type Content = {
  id: string; source_id: "douyin" | "weibo"; title: string; item_kind: string;
  original_url: string; author: string; author_url: string; published_at_display: string;
  published_precision: string; observed_at: string; discovery_label: string; discovery_url: string;
  method: string; evidence_file?: string; evidence_sha256?: string; raw_sha256: string;
  endpoint?: string; run_id?: string; title_is_excerpt?: boolean; request_method?: string;
  verification_note: string; version: string; play_count: null;
  metrics: Record<string, { label: string; value: number | null; definition: string; provider: string; unit: string }>;
};
type SourceState = { source_id: "douyin" | "weibo"; enabled: boolean; interval_seconds: number;
  verified_at: string | null; session_saved_locally: boolean; automatic_supported: boolean;
  last_run: { id: string; status: string; started_at: string; finished_at: string | null;
    item_count: number | null; rejected_count: number | null; error_code: string | null } | null };
type Catalog = { items: Content[]; selected_date: string; read_at: string; automatic_collection: boolean; sources: SourceState[] };
const platforms = { douyin: "抖音", weibo: "微博" };
function observed(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}

export default function ContentPool({ date, source, query, refreshKey }: { date: string; source: string; query: string; refreshKey: string }) {
  const [data, setData] = useState<Catalog | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [readRequest, setReadRequest] = useState(0);
  const sequence = useRef(0);
  useEffect(() => { setData(null); }, [date]);
  useEffect(() => {
    let disposed = false;
    const controller = new AbortController();
    const read = async () => {
      const current = ++sequence.current;
      setLoading(true);
      try {
        const response = await fetch(`/api/contents?date=${date}`, { cache: "no-store", signal: controller.signal });
        const result = await response.json();
        if (disposed || current !== sequence.current) return;
        if (!response.ok) throw new Error(result.error || "内容数据读取失败");
        setData(result); setError("");
      } catch (e) { if (!disposed && current === sequence.current) setError(e instanceof Error ? e.message : "内容读取失败"); }
      finally { if (!disposed && current === sequence.current) setLoading(false); }
    };
    void read();
    return () => { disposed = true; controller.abort(); };
  }, [date, refreshKey, readRequest]);
  const items = (data?.items ?? []).filter(item => (source === "all" || source === item.source_id) &&
    `${item.title} ${item.author} ${item.discovery_label}`.toLowerCase().includes(query.trim().toLowerCase()));
  return <section aria-label="真实内容核查池">
    <div className="hr-history-notice"><FileCheck2 /><div><strong>{data?.automatic_collection ? "已验证来源自动采集 · 历史样本保留" : "已核查内容留存 · 自动来源状态见下方"}</strong><p>仅收录具备真实链接、作者、发布时间和可用互动数的具体内容。当前是平台精选或热门候选，未定义爆款阈值；推荐结果不代表全平台榜单。每条指标以采样时刻为准，读取页面不会触发上游采集。</p></div></div>
    <div className="hr-section-row"><div><strong>具体内容 · {items.length} 条匹配</strong><p className="hr-muted">采样日期 {date} · 发布时间见各条原文 · 北京时间，按来源保留分钟或秒精度</p></div><button className="hr-button" disabled={loading} onClick={() => setReadRequest(value => value + 1)}><RefreshCw />{loading ? "正在读取" : "读取已保存内容"}</button></div>
    {error && <div className="hr-error-banner" role="alert">{error}{data && " 已保留此前真实样本，请核对采样时间。"}</div>}
    <div className="hr-content-access">{data?.sources.map(state => <p key={state.source_id}><strong>{platforms[state.source_id]}：</strong>{state.verified_at ? `${state.source_id === "douyin" ? "精选全部分类内容字段已验证" : "热门及详情字段已验证"} · ${state.enabled ? `每${state.interval_seconds / 60}分钟自动采集` : "自动采集已暂停"}。` : "会话内容接口尚未验证；自动采集未启用。"}
      {state.last_run && <> 最近任务：{state.last_run.status === "success" ? "成功" : state.last_run.status === "partial" ? "部分条目通过" : state.last_run.status === "empty" ? "来源返回空列表" : "失败"}，{observed(state.last_run.started_at)}；{state.last_run.item_count === null ? "无成功条目计数" : `留存${state.last_run.item_count}条`}{state.last_run.rejected_count ? `，跳过${state.last_run.rejected_count}条未通过采样核查的内容` : ""}{state.last_run.error_code ? `；${["session_required", "upstream_rejected"].includes(state.last_run.error_code) ? "平台未接受会话，请到采集授权页更新" : "接口或解析异常，历史仍保留"}` : ""}。</>}
      {state.session_saved_locally && <> 会话已加密保存。</>}</p>)}</div>
    <div className="hr-content-grid">
      {items.map(item => <article className="hr-content-card" key={item.id}>
        <div className="hr-content-body">
          <div className="hr-content-bottom"><strong>{platforms[item.source_id]} · {item.item_kind === "video" ? "视频" : "帖子"}</strong><span className={"hr-tag " + (item.method === "platform-session-api" ? "is-green" : "is-orange")}>{item.method === "platform-session-api" ? "会话自动采集" : "人工核查"}</span></div>
          <h3 className="hr-content-title" style={{ marginTop: 12 }}>{item.title.replace(/……版本过低.*$/, "…")}</h3>
          <a className="hr-content-author" href={item.author_url} target="_blank" rel="noreferrer">{item.author}<ExternalLink size={12} /></a>
          <p className="hr-muted">发布 {item.published_at_display}（{item.published_precision === "second" ? "来源秒级时间" : "页面原文，分钟精度"}）{(item.title_is_excerpt || item.title.includes("升级后可展示全部信息")) && " · 来源文字可能为摘录"}</p>
          <div className="hr-content-numbers">
            {Object.entries(item.metrics).map(([key, metric]) => <span key={key} title={metric.definition}>{metric.label}<strong>{metric.value === null ? "来源未提供" : metric.value.toLocaleString("zh-CN")}</strong></span>)}
          </div>
          <p className="hr-muted">播放量：来源未提供 · 采样 {observed(item.observed_at)}</p>
          <div className="hr-content-bottom"><a href={item.discovery_url} target="_blank" rel="noreferrer">{item.discovery_label}</a><a className="hr-button" href={item.original_url} target="_blank" rel="noreferrer">查看原文<ExternalLink /></a></div>
          <details className="hr-content-proof"><summary>核查依据与指标口径</summary>
            <p>{item.verification_note}</p>
            {Object.entries(item.metrics).map(([key, metric]) => <p key={key}>{metric.label}：{metric.definition}。单位：{metric.unit}；提供方：{platforms[item.source_id]}。</p>)}
            <p>原文：{item.original_url}<br />完整采样时间：{item.observed_at}<br />采样版本：{item.version}<br />记录 ID：{item.id}</p>
            {item.evidence_file && <p>本机截图：{item.evidence_file}<br />截图 SHA-256：{item.evidence_sha256}</p>}
            {item.endpoint && <p>来源接口：{item.endpoint}{item.request_method && <> · {item.request_method}</>}<br />采集任务：{item.run_id}</p>}
            <p>留存数据 SHA-256：{item.raw_sha256}</p>
          </details>
        </div>
      </article>)}
    </div>
    {!items.length && <div className="hr-empty"><FileCheck2 /><h2>{loading && !data ? "正在读取已核查内容" : error && !data ? "内容尚未读取" : "该采样日期或筛选下没有已核查内容"}</h2><p>话题热度不替代视频或帖子的互动指标，缺失数据不补造。</p></div>}
    {data && <p className="hr-muted">数据库读取：{observed(data.read_at)} · 内容指标以各条采样时刻为准</p>}
  </section>;
}
