"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { RefreshCw, Pause, Play, RadioTower } from "lucide-react";
import type { ManagedSource } from "../lib/types";

function time(value: string | null) { return value ? new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "尚无记录"; }
const states: Record<string, string> = { paused: "采集已暂停", fresh: "最近采集正常", stale: "数据已过期", failed: "最近采集失败", pending: "尚未采集", partial: "部分条目可用", collecting: "正在采集" };
const statuses: Record<string, string> = { success: "成功", partial: "部分解析", empty: "成功空榜", failed: "失败", running: "采集中", queued: "等待" };

export default function SourceManager({ onChange, autoRefresh }: { onChange: () => void; autoRefresh: boolean }) {
  const [sources, setSources] = useState<ManagedSource[]>([]);
  const [readAt, setReadAt] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const readSequence = useRef(0);
  const load = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++readSequence.current;
    try {
      const r = await fetch("/api/sources", { cache: "no-store", signal }); const data = await r.json();
      if (!r.ok) throw new Error(data.error);
      if (!signal?.aborted && sequence === readSequence.current) { setSources(data.sources); setReadAt(data.read_at); setError(""); }
    } catch(e) { if (!signal?.aborted && sequence === readSequence.current) setError(e instanceof Error ? e.message : "读取失败"); }
  }, []);
  useEffect(() => { const c = new AbortController(); void load(c.signal); return () => c.abort(); }, [load]);
  useEffect(() => { if (!autoRefresh) return; const c = new AbortController(); const t = setInterval(() => void load(c.signal), 30000); return () => { c.abort(); clearInterval(t); }; }, [load, autoRefresh]);
  async function change(board: ManagedSource, settings: { enabled?: boolean; interval_seconds?: number }) {
    setBusy(board.id); setNotice("");
    try {
      const r = await fetch("/api/sources", { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ board_id: board.id, settings }) });
      const data = await r.json(); if (!r.ok) throw new Error(data.error);
      setNotice(board.source_name + "配置已保存，历史快照保留。" + (data.enabled ? "Worker会按新周期检查。" : "在途请求可能仍会完成。"));
      await load(); onChange();
    } catch(e) { const message = e instanceof Error ? e.message : "保存失败"; await load(); setError(message); }
    finally { setBusy(""); }
  }
  return <section className="hr-source-management" aria-label="来源管理">
    <div className="hr-section-row"><h2><RadioTower />采集来源 <small>抖音 / 微博 / 百度</small></h2><button className="hr-button" onClick={() => void load()}><RefreshCw />读取来源状态</button></div>
    <p className="hr-management-note">最近24小时任务统计，实际留存始于各来源的首次采样；不代表已通过24小时连续运行验收。暂停采集与暂停页面刷新独立。</p>
    {notice && <div className="hr-success-banner" role="status">{notice}</div>}
    {error && <div className="hr-error-banner" role="alert">{error}</div>}
    {!readAt && !error && <p>正在读取来源记录…</p>}
    <div className="hr-source-cards">{sources.map(board => <article className="hr-source-card" key={board.id}>
      <div className="hr-section-row"><h3>{board.source_name}<small>{board.name}</small></h3><span className={"hr-tag " + (board.state === "fresh" ? "is-green" : "is-orange")}>{states[board.state]}</span></div>
      <div className="hr-source-settings"><label>采集周期<select aria-label={board.source_name + board.name + "采集周期"} value={board.interval_seconds} disabled={!!busy} onChange={e => void change(board, { interval_seconds: Number(e.target.value) })}>{[900, 1800, 3600].map(n => <option key={n} value={n}>{n / 60}分钟</option>)}</select></label><button className="hr-button" disabled={!!busy} onClick={() => void change(board, { enabled: !board.enabled })}>{board.enabled ? <Pause /> : <Play />}{board.enabled ? "暂停" : "恢复"}{board.source_name}{board.id === "douyin-wuxi" ? "无锡创作热点" : ["weibo-wuxi", "douyin-wuxi-app"].includes(board.id) ? "无锡榜" : ""}采集</button></div>
      <dl className="hr-source-facts"><div><dt>最近成功</dt><dd>{time(board.last_success_at)}</dd></div><div><dt>首次留存</dt><dd>{time(board.first_observed_at)}</dd></div><div><dt>数据类型</dt><dd>话题 · {board.metric_label}</dd></div><div><dt>最近任务尝试</dt><dd>{board.latest_run?.attempt_count ?? "—"} 次</dd></div></dl>
      <div className="hr-run-counts">任务 {board.window.total} · 成功 {board.window.success} · 部分 {board.window.partial} · 失败 {board.window.failed} · 未完成 {board.window.unfinished}</div>
      <details><summary>{board.source_name}最近任务</summary><ul className="hr-run-list">{board.recent_runs.map(run => <li key={run.id}><span>{time(run.created_at)}</span><strong>{statuses[run.status] ?? run.status}</strong><span>{run.item_count === null ? "条数未知" : run.item_count + " 条"}</span>{run.error_code && <small>{run.error_code}</small>}</li>)}</ul>{!board.recent_runs.length && <p>统计窗口暂无任务。</p>}</details>
      <details><summary>{board.source_name}配置变更记录</summary>{board.changes.map(change => <p key={change.id}>{time(change.changed_at)} · {change.after.enabled ? "启用" : "暂停"} · {change.after.interval_seconds / 60}分钟</p>)}{!board.changes.length && <p>暂无配置变更。</p>}</details>
      <small>提供方 {board.provider} · {board.source_id === "baidu" ? "百度官方实时热搜网页 · 无需登录" : board.source_id === "weibo" ? (board.id === "weibo-wuxi" ? "本机微博登录会话 · 官方城市需设为无锡" : "原文可能需微博登录") : board.id === "douyin-wuxi" ? "本机创作者中心会话 · 非抖音 App 无锡榜" : board.id === "douyin-wuxi-app" ? "官方热榜落地页 · 无需登录" : "匿名临时会话"}</small>
    </article>)}</div><small>状态读取：{time(readAt)}（北京时间）</small>
  </section>;
}
