"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, ChartNoAxesCombined, Clock, Pause, Play, RefreshCw, Search } from "lucide-react";
import TrendChart, { numberLabel, trendTime } from "./trend-chart";
import { filterTrendChoices, rangeFor, shiftDay, type EventTrend, type TrendChoice, type TrendOverview, type TrendSource } from "../lib/trends";

const states: Record<string, string> = { fresh: "最新", partial: "部分解析", pending: "待采集", stale: "已过期", failed: "采集失败", collecting: "采集中", paused: "采集暂停" };
const pointStates: Record<string, string> = { present: "已观测", not_on_board: "未在非广告采集榜单内", failed: "采集失败", collecting: "任务未完成", unknown_partial: "部分解析，是否上榜未知", unobserved: "窗口内暂无采样" };

function CoverageCard({ source: s }: { source: TrendSource }) {
  const [allGaps, setAllGaps] = useState(false);
  return <article className="hr-trend-source">
    <div className="hr-section-row"><h3>{s.name}<small>{s.coverage.sampled_days}/{s.coverage.requested_days} 个采样日</small></h3><span className={"hr-tag " + (s.stale || !["fresh", "partial"].includes(s.state) ? "is-orange" : "is-green")}>{states[s.state] || s.state}{s.state !== "stale" && s.stale ? " · 旧快照过期" : ""}</span></div>
    <p>窗口内 {s.coverage.snapshots} 批快照 · 首次留存 {trendTime(s.coverage.first_ever_observed_at)}<br />窗口实际 {trendTime(s.coverage.first_observed_at)}—{trendTime(s.coverage.last_observed_at)}</p>
    <div className="hr-days-grid" style={{ gridTemplateColumns: `repeat(${Math.min(7, s.coverage.days.length)}, minmax(0,1fr))` }}>{s.coverage.days.map(d => <div key={d.date} className={"hr-day-cell " + (d.state === "observed" ? "is-observed" : "is-unobserved")} title={(d.first_observed_at ? trendTime(d.first_observed_at) + "—" + trendTime(d.last_observed_at) : "该日期没有成功留存") + ` · 失败任务 ${d.failed_runs} · 部分解析 ${d.partial_snapshots} · 缓存 ${d.cached_snapshots}`}><time dateTime={d.date}>{d.date.slice(5).replace("-", "/")}</time><strong>{d.snapshots === null ? "未采集" : d.snapshots + "批"}</strong><small>{d.open_day ? "当日未结束" : d.state === "observed" ? "部分时段" : "无曲线"}</small></div>)}</div>
    <div className="hr-coverage-quality"><span>失败任务 {s.coverage.failed_runs}</span><span>部分解析 {s.coverage.partial_snapshots}</span><span>缓存 {s.coverage.cached_snapshots}</span><span>采样中断/暂停 {s.coverage.gaps.filter(g => g.kind !== "before_first").length}</span></div>
    <small>最近成功 {trendTime(s.last_success_at)} · 当前周期 {s.interval_seconds / 60}分钟{!s.enabled ? " · 当前已暂停" : ""}</small>
    <details><summary>覆盖口径与缺口（{s.coverage.gaps.length}处）</summary><p>{s.coverage.definition}</p>{s.coverage.gaps.slice(0, allGaps ? undefined : 10).map((g, i) => <p key={i}>{{ before_first: "首次成功留存前", missing: "采样缺口", paused: "历史采集暂停" }[g.kind]}：{trendTime(g.start)}—{trendTime(g.end)}</p>)}{s.coverage.gaps.length > 10 && <button className="hr-link-button" onClick={() => setAllGaps(v => !v)}>{allGaps ? "收起缺口" : "显示全部缺口"}</button>}<small>旧快照中有 {s.coverage.legacy_cadence_snapshots} 批未记录周期，按当时默认900秒识别。日格有采样不表示全天连续。</small></details>
  </article>;
}

export default function TrendManager({ date, refreshKey, wall }: { date: string; refreshKey: string; wall: boolean }) {
  const [mode, setMode] = useState("seven");
  const [customStart, setCustomStart] = useState(shiftDay(date, -6));
  const [customEnd, setCustomEnd] = useState(date);
  const [data, setData] = useState<TrendOverview | null>(null);
  const [detail, setDetail] = useState<EventTrend | null>(null);
  const [selected, setSelected] = useState("");
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("all");
  const [error, setError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [zoom, setZoom] = useState(true);
  const [checked, setChecked] = useState<string[]>([]);
  const [ledgerItem, setLedgerItem] = useState("");
  const [ledgerLimit, setLedgerLimit] = useState(30);
  const [rotating, setRotating] = useState(false);
  const read = useRef(0), detailRead = useRef(0), selectedRef = useRef("");
  const lastRange = useRef("");
  const choicesRef = useRef<TrendChoice[]>([]);
  const range = rangeFor(mode, date, customStart, customEnd);
  const key = range.start + "/" + range.end;

  const readEvent = useCallback(async (id: string, preserve = false) => {
    const seq = ++detailRead.current;
    selectedRef.current = id; setSelected(id); setDetailLoading(true); setDetailError("");
    if (!preserve) { setDetail(null); setLedgerLimit(30); }
    try {
      const response = await fetch(`/api/trends?id=${id}&start=${range.start}&end=${range.end}`, { cache: "no-store" });
      const result = await response.json();
      if (seq !== detailRead.current) return;
      if (!response.ok) throw new Error(result.error);
      setDetail(result);
      const ids = result.series.map((s: { item_id: string }) => s.item_id) as string[];
      setChecked(previous => preserve && previous.some(id => ids.includes(id)) ? previous.filter(id => ids.includes(id)) : ids.slice(0, 8));
      setLedgerItem(previous => preserve && ids.includes(previous) ? previous : ids[0] || "");
    } catch (e) { if (seq === detailRead.current) setDetailError(e instanceof Error ? e.message : "曲线读取失败"); }
    finally { if (seq === detailRead.current) setDetailLoading(false); }
  }, [range.start, range.end]);

  const refresh = useCallback(async () => {
    const seq = ++read.current;
    setLoading(true);
    try {
      const response = await fetch(`/api/trends?start=${range.start}&end=${range.end}`, { cache: "no-store" });
      const result = await response.json();
      if (seq !== read.current) return;
      if (!response.ok) throw new Error(result.error);
      setData(result); setError("");
      const id = result.events.some((e: { id: string }) => e.id === selectedRef.current) ? selectedRef.current : result.events[0]?.id;
      if (id) await readEvent(id, id === selectedRef.current);
      else { ++detailRead.current; setDetail(null); setSelected(""); selectedRef.current = ""; setDetailLoading(false); }
    } catch (e) { if (seq === read.current) setError(e instanceof Error ? e.message : "历史读取失败"); }
    finally { if (seq === read.current) setLoading(false); }
  }, [range.start, range.end, readEvent]);

  useEffect(() => {
    if (lastRange.current !== key) { setData(null); setDetail(null); setSelected(""); selectedRef.current = ""; lastRange.current = key; }
    void refresh();
    return () => { ++read.current; ++detailRead.current; };
  }, [key, refresh, refreshKey]);
  useEffect(() => { if (!wall) setRotating(false); }, [wall]);
  const choices = filterTrendChoices(data?.events || [], query, source);
  choicesRef.current = choices;
  useEffect(() => {
    if (!loading && choices.length && !choices.some(e => e.id === selectedRef.current)) void readEvent(choices[0].id);
  }, [query, source, loading, choices, readEvent]);
  const nextEvent = useCallback((step: number) => {
    const list = choicesRef.current;
    if (!list.length) return;
    const index = list.findIndex(e => e.id === selectedRef.current);
    const next = list[(Math.max(0, index) + step + list.length) % list.length];
    void readEvent(next.id);
  }, [readEvent]);
  useEffect(() => {
    if (!wall || !rotating || error || detailError || loading || detailLoading || choices.length < 2) return;
    const timer = setInterval(() => nextEvent(1), 15000);
    return () => clearInterval(timer);
  }, [wall, rotating, error, detailError, loading, detailLoading, choices.length, nextEvent]);
  const allSeries = detail?.series || [];
  const visible = allSeries.filter(s => checked.includes(s.item_id) && (source === "all" || source === s.platform));
  const timeline = visible.flatMap(s => s.points.map(p => Date.parse(p.observed_at)));
  const windowLeft = Date.parse(detail?.window_start || data?.window_start || new Date().toISOString());
  const windowRight = Date.parse(detail?.window_end || data?.window_end || new Date().toISOString());
  const actualLeft = timeline.reduce((a, b) => Math.min(a, b), Infinity), actualRight = timeline.reduce((a, b) => Math.max(a, b), -Infinity);
  const left = zoom && timeline.length ? actualLeft : windowLeft;
  const right = zoom && timeline.length ? actualRight === actualLeft ? actualRight + 60000 : actualRight : windowRight;
  const ledger = allSeries.find(s => s.item_id === ledgerItem);
  const index = choices.findIndex(e => e.id === selected);
  return <section className="hr-trend-manager">
    <div className="hr-trend-range"><div className="hr-category-row">{[["day", "当日"], ["previous", "前一日"], ["seven", "近7天"], ["custom", "自定义"]].map(([id, name]) => <button className={"hr-chip " + (mode === id ? "is-selected" : "")} key={id} onClick={() => setMode(id)}>{name}</button>)}</div><small>以顶部选定日期为基准 · 北京时间</small><button className="hr-button" disabled={loading} onClick={() => void refresh()}><RefreshCw />读取历史</button></div>
    {mode === "custom" && <div className="hr-custom-range"><label>开始日期<input aria-label="趋势开始日期" type="date" max={date} value={customStart} onInput={e => { if (e.currentTarget.value) setCustomStart(e.currentTarget.value); }} onChange={e => { if (e.target.value) setCustomStart(e.target.value); }} /></label><label>结束日期<input aria-label="趋势结束日期" type="date" max={date} value={customEnd} onInput={e => { if (e.currentTarget.value) setCustomEnd(e.currentTarget.value); }} onChange={e => { if (e.target.value) setCustomEnd(e.target.value); }} /></label><small>最多31个自然日；未采集的日期保留空白</small></div>}
    {error && <div className="hr-error-banner" role="alert">{error}{data && <small>保留 {data.start_date}—{data.end_date} 上次读取；请核对时间。</small>}</div>}
    {data && <>
      <div className="hr-history-notice"><Clock /><div><strong>{data.sampled_days < data.requested_days ? `历史不足${data.requested_days}天 · 仅${data.sampled_days}个日期有真实采样` : "所选日期有采样，仍需核对各日覆盖"}</strong><p>{data.start_date}—{data.end_date} · {data.coverage_note}</p><small>七天连续稳定性尚未验收；有采样的日期不等于完整自然日覆盖。</small></div></div>
      <div className="hr-trend-source-grid">{data.sources.map(s => <CoverageCard key={s.id} source={s} />)}</div>
      <div className="hr-trend-read"><time dateTime={data.read_at}>历史读取 {trendTime(data.read_at)}</time><span>{data.relationship_note}</span></div>
      <div className="hr-trend-pickers"><label className="hr-search"><Search /><input type="search" aria-label="搜索趋势事件" value={query} maxLength={300} placeholder="搜索已采集的事件" onChange={e => setQuery(e.target.value)} /></label><select aria-label="趋势平台筛选" value={source} onChange={e => setSource(e.target.value)}><option value="all">全部平台</option><option value="douyin">抖音</option><option value="weibo">微博</option><option value="baidu">百度</option></select><label className="hr-trend-event-picker">观察事件<select aria-label="选择趋势事件" value={choices.some(e => e.id === selected) ? selected : ""} onChange={e => void readEvent(e.target.value)}><option value="" disabled>请选择匹配事件</option>{choices.map(e => <option key={e.id} value={e.id}>{e.platforms.length > 1 ? "跨平台 · " : ""}{e.title}</option>)}</select></label><small>{choices.length} 个匹配事件</small></div>
      {choices.length === 0 && <div className="hr-empty"><ChartNoAxesCombined /><h2>该窗口或筛选下暂无观测事件</h2><p>覆盖情况仍按真实快照显示，未添加示例曲线。</p></div>}
      {wall && <div className="hr-trend-rotation"><button className="hr-button" disabled={choices.length < 2 || detailLoading} aria-label="上一趋势事件" onClick={() => nextEvent(-1)}><ArrowLeft /></button><button className="hr-button" disabled={choices.length < 2 || detailLoading} aria-label="下一趋势事件" onClick={() => nextEvent(1)}><ArrowRight /></button><button className="hr-button" disabled={!rotating && choices.length < 2} onClick={() => setRotating(v => !v)}>{rotating ? <Pause /> : <Play />}{rotating ? "暂停事件轮播" : "开始事件轮播"}</button><span>{rotating && choices.length >= 2 && !loading && !detailLoading && !error && !detailError ? "轮播中" : rotating ? "轮播等待数据" : "轮播暂停"} · {index >= 0 ? index + 1 : "—"}/{choices.length} · 每15秒 · 与自动读取独立</span></div>}
      {detailError && <div className="hr-error-banner" role="alert">{detailError} · {detail ? "保留上次读取的曲线" : "未显示示例数据"}</div>}
      {detailLoading && <p className="hr-management-note" role="status">正在读取真实历史曲线…</p>}
      {detail && choices.some(e => e.id === detail.event.id) && <div className="hr-trend-content">
        <div className="hr-section-row"><h2>{detail.event.title}<small>{detail.event.manual_locked ? "人工关系保护" : "关联仍待核对"} · 版本{detail.event.revision}</small></h2><button className="hr-button" onClick={() => setZoom(v => !v)}>{zoom ? "查看完整日期窗口" : "缩放到实际采样"}</button></div>
        <p className="hr-management-note">曲线{zoom ? "缩放至实际采样时段" : "展示完整所选日期窗口"}：{trendTime(new Date(left).toISOString())}—{trendTime(new Date(right).toISOString())}。未采集日期见上方覆盖日历，空白不插值。</p>
        <div className="hr-trend-series-checks">{allSeries.map(s => <label key={s.item_id}><input type="checkbox" checked={checked.includes(s.item_id)} onChange={e => setChecked(ids => e.target.checked ? [...ids, s.item_id].slice(-8) : ids.filter(id => id !== s.item_id))} />{s.source_name} · {s.title}</label>)}<small>显示 {visible.length}/{allSeries.length} 条来源话题 · 最多同时8条，源内指标各自绘图</small></div>
        {visible.length ? <>
          <TrendChart key={detail.event.id + "rank"} title="源内榜位变化" series={visible} sources={detail.sources.filter(s => visible.some(v => v.board_id === s.id))} mode="rank" left={left} right={right} />
          <div className="hr-trend-metric-grid">{visible.map(s => <article className="hr-trend-metric-card" key={s.item_id}><h3>{s.source_name} · {s.metric_label}<small>{s.title}</small></h3><div className="hr-real-series-stats"><span>已观测最佳榜位<strong>{s.stats.best_rank ?? "来源未提供"}</strong></span><span>末次出现榜位<strong>{s.stats.last_rank ?? "来源未提供"}</strong></span><span>话题观测记录<strong>{s.stats.observations}</strong></span></div><small>末次状态：{pointStates[s.stats.latest_status]} · 首次 {trendTime(s.stats.first_observed_at)} · 最近 {trendTime(s.stats.last_observed_at)}<br />首末观测跨度 {s.stats.observed_span_seconds === null ? "暂无" : Math.round(s.stats.observed_span_seconds / 60) + "分钟"}，不是完整或连续在榜时长。</small>
            <TrendChart title={s.source_name + " · " + s.metric_label + "原值"} series={[s]} sources={detail.sources.filter(source => source.id === s.board_id)} mode="metric" left={left} right={right} />
            <div className="hr-trend-comparison"><strong>末两次可比采样</strong><p>榜位变化 {s.comparison.rank_delta === null ? "不可比较" : (s.comparison.rank_delta > 0 ? "↑" : s.comparison.rank_delta < 0 ? "↓" : "持平 ") + Math.abs(s.comparison.rank_delta)} · 指标变化 {s.comparison.metric_delta === null ? "不可比较" : (s.comparison.metric_delta >= 0 ? "+" : "") + numberLabel(s.comparison.metric_delta)}{s.comparison.metric_percent !== null ? "（" + (s.comparison.metric_percent >= 0 ? "+" : "") + s.comparison.metric_percent + "%）" : ""}</p><small>{s.comparison.window_seconds !== null ? "比较窗口 " + (s.comparison.window_seconds / 60).toFixed(1) + "分钟 · " : ""}{s.comparison.reason}{s.comparison.metric_delta !== null && s.comparison.metric_percent === null ? " 基期为零或百分比不可计算，不显示增速。" : ""}</small><small>{s.metric_definition?.definition || "该来源未提供指标口径"}。不同口径点不混画；指标缺失不填零。</small></div>
          </article>)}</div>
        </> : <div className="hr-empty"><p>请选择要观察的来源话题。</p></div>}
        <details className="hr-trend-ledger"><summary>真实采样记录与来源追溯</summary><label>来源话题<select aria-label="选择采样记录来源" value={ledgerItem} onChange={e => { setLedgerItem(e.target.value); setLedgerLimit(30); }}>{allSeries.map(s => <option key={s.item_id} value={s.item_id}>{s.source_name} · {s.title}</option>)}</select></label><div className="hr-trend-table-wrap"><table><thead><tr><th>采样时间</th><th>状态</th><th>榜位</th><th>源内指标</th><th>快照 / 来源</th></tr></thead><tbody>{ledger?.points.slice().reverse().slice(0, ledgerLimit).map(p => <tr key={p.run_id + p.observed_at}><td>{trendTime(p.observed_at)}</td><td>{pointStates[p.status]}{p.from_cache ? " · 缓存" : p.snapshot_status === "partial" ? " · 部分解析" : ""}</td><td>{p.rank ?? "—"}</td><td>{p.metric?.value?.toLocaleString("zh-CN") ?? (p.status === "present" ? "来源未提供" : "不填值")}</td><td>{p.url && <a href={p.url} target="_blank" rel="noopener noreferrer">{p.title}</a>}<small>快照 {p.snapshot_id || "无成功快照"}<br />任务 {p.run_id}<br />证据 {p.evidence_id || "无条目"}<br />{p.metric?.definition || "未取到该条目指标"} · {p.parser_version}</small></td></tr>)}</tbody></table></div>{ledger && ledger.points.length > ledgerLimit && <button className="hr-button" onClick={() => setLedgerLimit(n => n + 50)}>再显示50条 · 共{ledger.points.length}条</button>}</details>
        <p className="hr-management-note">曲线读取 {trendTime(detail.read_at)} · {detail.relationship_note} · 不提供爆款预测或跨平台总热度。</p>
      </div>}
    </>}
    {!data && !error && <div className="hr-empty"><Clock /><p>正在读取实际历史…</p></div>}
  </section>;
}
