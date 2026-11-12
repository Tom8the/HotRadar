"use client";
import { useEffect, useRef, useState } from "react";
import { curveSegments, pointValue, type TrendPoint, type TrendSeries, type TrendSource } from "../lib/trends";

export const trendColors = ["#149c7b", "#db7659", "#698ce2", "#b786c5", "#a7904d", "#428fa9", "#bc7195", "#799655"];
export function trendTime(value: string | null, dayOnly = false) {
  return value ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", ...(dayOnly ? {} : { hour: "2-digit", minute: "2-digit" }) }).format(new Date(value)) : "暂无观测";
}
export function numberLabel(value: number | null | undefined) {
  if (value === null || value === undefined) return "来源未提供";
  return Math.abs(value) >= 10000 ? (value / 10000).toFixed(1) + "万" : value.toLocaleString("zh-CN");
}

export default function TrendChart({ series, sources, mode, left, right, title }: {
  series: TrendSeries[]; sources: TrendSource[]; mode: "rank" | "metric"; left: number; right: number; title: string;
}) {
  const [selected, setSelected] = useState<{ point: TrendPoint; series: TrendSeries } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(860);
  useEffect(() => {
    const observer = new ResizeObserver(entries => { const size = entries[0]?.contentRect.width; if (size) setWidth(Math.max(200, Math.round(size))); });
    if (box.current) observer.observe(box.current);
    return () => observer.disconnect();
  }, []);
  const height = width < 500 ? 240 : 270, l = width < 400 ? 57 : 72, r = 15, t = 27, b = 48;
  const values = series.flatMap(s => s.points.map(p => pointValue(p, mode))).filter((v): v is number => v !== null);
  const high = values.reduce((n, v) => Math.max(n, v), mode === "rank" ? 50 : 1), low = mode === "rank" ? 1 : 0;
  const x = (value: string) => l + (Date.parse(value) - left) / Math.max(right - left, 1) * (width - l - r);
  const y = (value: number) => t + (mode === "rank" ? (value - low) : (high - value)) / (high - low || 1) * (height - t - b);
  const gaps = sources.flatMap(s => s.coverage.gaps.map(g => ({ ...g, source: s.name }))).filter(g => Date.parse(g.start) < right && Date.parse(g.end) > left);
  const plotted = series.flatMap(s => s.points.filter(p => pointValue(p, mode) !== null).map(p => ({ series: s, point: p })));
  const sameDay = trendTime(new Date(left).toISOString(), true) === trendTime(new Date(right).toISOString(), true);
  const axisTime = (at: number) => sameDay ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit" }).format(new Date(at)) : trendTime(new Date(at).toISOString(), true);
  return <div className="hr-real-chart" ref={box}>
    <div className="hr-chart-caption"><strong>{title}</strong><span>{mode === "rank" ? "源内榜位 · 越靠上越靠前" : "源内指标原值 · 非播放量"}</span></div>
    <svg viewBox={`0 0 ${width} ${height}`} role="img" aria-label={title + "；只绘制真实采样点，缺口不连线"}>
      <title>{title}</title><desc>横轴为实际采样时间。空白、缺口、未上榜及指标缺失不补零；缓存与部分解析点单独标示。</desc>
      {gaps.map((g, i) => <rect key={g.source + i} x={x(g.start) < l ? l : x(g.start)} y={t} width={Math.max(1, Math.min(width - r, x(g.end)) - Math.max(l, x(g.start)))} height={height - t - b} className={"hr-chart-gap is-" + g.kind}><title>{g.source} · {{ before_first: "首次成功留存前", missing: "采样缺口", paused: "采集暂停" }[g.kind]} · {trendTime(g.start)}—{trendTime(g.end)}</title></rect>)}
      {[0, .25, .5, .75, 1].map(f => { const v = mode === "rank" ? low + f * (high - low) : high * (1 - f); const atY = t + f * (height - t - b); return <g key={f}><line x1={l} x2={width - r} y1={atY} y2={atY} className="hr-chart-grid" /><text x={l - 12} y={atY + 4} textAnchor="end">{mode === "rank" ? Math.round(v) : numberLabel(v)}</text></g>; })}
      {(width < 420 ? [0, 1] : [0, .5, 1]).map(f => <text key={f} x={l + f * (width - l - r)} y={height - 17} textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"}>{axisTime(left + f * (right - left))}</text>)}
      {series.map((s, index) => <g key={s.item_id} style={{ color: trendColors[index % trendColors.length] }}>
        {curveSegments(s.points, mode).filter(segment => segment.length > 1).map((segment, i) => <polyline key={i} fill="none" stroke="currentColor" strokeWidth="2.3" strokeDasharray={index >= 2 ? "5 3" : undefined} points={segment.map(p => `${x(p.observed_at)},${y(pointValue(p, mode)!)}`).join(" ")} />)}
        {s.points.filter(p => pointValue(p, mode) !== null).filter((_, i, points) => points.length <= 240 || i % Math.ceil(points.length / 240) === 0 || i === points.length - 1).map(p => <circle key={p.evidence_id} cx={x(p.observed_at)} cy={y(pointValue(p, mode)!)} r={p.from_cache || p.snapshot_status === "partial" ? 4 : 3.3} className={p.from_cache || p.snapshot_status === "partial" ? "is-qualified" : ""} fill="currentColor" stroke="currentColor" onClick={() => setSelected({ point: p, series: s })}><title>{s.source_name} · {trendTime(p.observed_at)} · {mode === "rank" ? "榜位 " + p.rank : s.metric_label + " " + p.metric?.value} · {p.from_cache ? "缓存" : p.snapshot_status === "partial" ? "部分解析" : "完整采样"}</title></circle>)}
      </g>)}
      {values.length === 0 && <text x={(width + l) / 2} y={height / 2} textAnchor="middle">{mode === "rank" ? "窗口内没有来源提供的榜位" : "窗口内没有同口径指标值"}</text>}
    </svg>
    <div className="hr-chart-legend">{series.map((s, i) => <span key={s.item_id}><i style={{ background: trendColors[i % trendColors.length] }} />{s.source_name} · {s.title}</span>)}</div>
    <small>空白不代表零。浅色带为首次留存前 / 缺口 / 暂停；空心点为缓存或部分解析，不生成增速。{plotted.length > 240 ? "曲线保留全部点，点标记按密度减少；下方采样记录保留全部原值。" : ""}</small>
    {plotted.length > 0 && <label className="hr-point-picker">查看真实采样点<select aria-label={"查看采样点：" + title} value={selected?.point.evidence_id || ""} onChange={e => { const found = plotted.find(p => p.point.evidence_id === e.target.value); setSelected(found || null); }}><option value="">选择时间 / 来源（也可点击图中点）</option>{plotted.map(({ series: s, point: p }) => <option key={p.evidence_id} value={p.evidence_id!}>{s.source_name} · {trendTime(p.observed_at)} · {mode === "rank" ? "榜位 " + p.rank : numberLabel(p.metric?.value)}</option>)}</select></label>}
    {selected && plotted.some(p => p.point.evidence_id === selected.point.evidence_id) && <div className="hr-selected-point"><strong>{selected.series.source_name} · {trendTime(selected.point.observed_at)}</strong><span>{selected.point.title} · 榜位 {selected.point.rank ?? "来源未提供"} · {selected.series.metric_label} {selected.point.metric?.value?.toLocaleString("zh-CN") ?? "来源未提供"}</span><small>{selected.point.metric?.definition} · 快照 {selected.point.snapshot_id}</small><a href={selected.point.url!} target="_blank" rel="noopener noreferrer">打开该采样的原始来源</a></div>}
  </div>;
}
