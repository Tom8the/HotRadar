"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bookmark, ExternalLink, Flame, GitMerge, RefreshCw, Scissors, Search, Star, Undo2, X } from "lucide-react";
import { filterEvents, type EventCatalog, type EventDetail, type Evidence } from "../lib/events";

function stamp(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
function EvidenceCard({ evidence: e }: { evidence: Evidence }) {
  const metric = e.metrics[e.metric_name];
  return <article className="hr-event-evidence" id={"evidence-" + e.id}>
    <div className="hr-evidence-top"><span className="hr-tag">{e.source_name}</span><span>{stamp(e.observed_at)} 采样</span></div>
    <a href={e.url} target="_blank" rel="noopener noreferrer">{e.title}<ExternalLink /></a>
    <p>最后出现榜位 {e.rank ?? "来源未提供"} · {e.metric_label} {metric?.value?.toLocaleString("zh-CN") ?? "来源未提供"}</p>
    <small>{e.on_latest_board === undefined ? "当前关系中的来源条目" : e.on_latest_board ? "仍在选定日期末次榜单" : "已不在选定日期末次榜单"}{e.from_cache ? " · 上游缓存" : ""}{e.snapshot_status === "partial" ? " · 部分解析" : ""}</small>
    <small>{metric?.definition} · 非播放量</small>
    <small>关联依据：{e.basis.reason} · {e.basis.review}</small>
    <details><summary>追溯采样与指标</summary><small>来源首次观测：{stamp(e.first_observed_at)}<br />来源事件时间：{e.source_event_at ? stamp(e.source_event_at) : "来源未提供"}<br />快照：{e.snapshot_id}<br />证据：{e.id}<br />指标提供方：{metric?.provider ?? "来源未提供"} · {metric?.unit ?? "来源未提供"}</small></details>
  </article>;
}

export default function EventManager({ date, refreshKey, following = false, wall = false, onModalChange }: {
  date: string; refreshKey: string; following?: boolean; wall?: boolean; onModalChange: (open: boolean) => void;
}) {
  const [data, setData] = useState<EventCatalog | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState(following ? "followed" : "all");
  const [tag, setTag] = useState("");
  const [limit, setLimit] = useState(20);
  const [detail, setDetail] = useState<EventDetail | null>(null);
  const [opened, setOpened] = useState("");
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [mergeId, setMergeId] = useState("");
  const [mergeQuery, setMergeQuery] = useState("");
  const [splitIds, setSplitIds] = useState<string[]>([]);
  const [reason, setReason] = useState("");
  const [tags, setTags] = useState("");
  const read = useRef(0), detailRead = useRef(0);
  const drawer = useRef<HTMLElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  const refresh = useCallback(async () => {
    const seq = ++read.current;
    setLoading(true);
    try {
      const response = await fetch(`/api/events?date=${date}`, { cache: "no-store" });
      const result = await response.json();
      if (seq !== read.current) return;
      if (!response.ok) throw new Error(result.error);
      setData(result); setError("");
    } catch (e) { if (seq === read.current) setError(e instanceof Error ? e.message : "事件读取失败"); }
    finally { if (seq === read.current) setLoading(false); }
  }, [date]);
  useEffect(() => { setData(null); setOpened(""); setDetail(null); void refresh(); return () => { ++read.current; ++detailRead.current; }; }, [refresh]);
  useEffect(() => { if (refreshKey) void refresh(); }, [refreshKey, refresh]);
  useEffect(() => { setLimit(20); }, [query, scope, tag, date]);
  useEffect(() => { onModalChange(!!opened); return () => onModalChange(false); }, [opened, onModalChange]);
  useEffect(() => {
    if (opened) drawer.current?.querySelector<HTMLButtonElement>('[aria-label="关闭事件详情"]')?.focus();
  }, [opened]);

  function close() { onModalChange(false); ++detailRead.current; setOpened(""); setDetail(null); setDetailLoading(false); requestAnimationFrame(() => opener.current?.focus()); }
  async function open(id: string, reset = true, trigger?: HTMLElement) {
    const seq = ++detailRead.current;
    if (reset) { opener.current = trigger ?? document.activeElement as HTMLElement; setDetail(null); setReason(""); setMergeId(""); setMergeQuery(""); setSplitIds([]); }
    setOpened(id); setDetailLoading(true); setDetailError("");
    try {
      const response = await fetch(`/api/events?date=${date}&id=${id}`, { cache: "no-store" });
      const result = await response.json();
      if (seq !== detailRead.current) return;
      if (!response.ok) throw new Error(result.error);
      setDetail(result); setTags(result.event.tags.join("，"));
    } catch (e) { if (seq === detailRead.current) setDetailError(e instanceof Error ? e.message : "详情读取失败"); }
    finally { if (seq === detailRead.current) setDetailLoading(false); }
  }
  async function mutate(action: string, payload: unknown) {
    if (busy) return;
    const requestDetail = detailRead.current;
    setBusy(true); setDetailError(""); setNotice("");
    try {
      const response = await fetch("/api/events", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action, payload }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error);
      setNotice("已保存到本地项目数据库，原始快照保留。");
      setSplitIds([]); setMergeId(""); setReason("");
      await refresh();
      if (opened && requestDetail === detailRead.current) await open(opened, false);
    } catch (e) { setDetailError(e instanceof Error ? e.message : "修正保存失败"); }
    finally { setBusy(false); }
  }
  const visible = filterEvents(data?.events || [], query, following ? "followed" : scope, tag);
  const allTags = [...new Set(data?.events.flatMap(e => e.tags) || [])];
  const mergeTarget = data?.events.find(e => e.id === mergeId);
  const mergeOptions = (data?.events || []).filter(e => e.id !== detail?.event.id &&
    (e.id === mergeId || !mergeQuery.trim() || [e.title, ...e.evidence.map(s => s.title)].some(t => t.includes(mergeQuery.trim()))))
    .sort((a, b) => Number(b.id === mergeId) - Number(a.id === mergeId)).slice(0, 30);
  const event = detail?.event;
  const versions = event ? { [event.id]: event.revision, ...(mergeTarget ? { [mergeTarget.id]: mergeTarget.revision } : {}) } : {};
  const canEdit = !busy && !detailLoading;
  return <>
    <section className="hr-event-manager">
      <div className="hr-section-row"><h2><Flame />{following ? "关注的事件" : "事件观察池"}<small>跨平台优先 · 最近观测排序</small></h2><button className="hr-button" disabled={loading} onClick={() => void refresh()}><RefreshCw />读取事件</button></div>
      <div className="hr-event-stats"><span><strong>{data?.events.length ?? "—"}</strong> 当日观测事件</span><span><strong>{data?.events.filter(e => e.platforms.length > 1).length ?? "—"}</strong> 跨平台关联</span><span><strong>{data?.events.filter(e => e.followed).length ?? "—"}</strong> 已关注</span></div>
      <p className="hr-management-note">{data?.coverage_note || "仅展示实际采集的抖音、微博和百度话题。"} 自动关联仍需核对；相似度不代表事实置信度。</p>
      {data && <small className="hr-event-read">事件读取 {stamp(data.read_at)} · {data.algorithm} · 广告不进入自动事件池</small>}
      <div className="hr-event-sources">{data?.sources.map(s => <span className={"hr-tag " + (["fresh", "partial"].includes(s.state) ? "is-green" : "is-orange")} key={s.id}>{s.name} · {{ fresh: "最新", partial: "部分解析", pending: "待采集", stale: "已过期", failed: "采集失败", collecting: "采集中", paused: "采集暂停" }[s.state] || s.state} · 最近成功 {s.last_success_at ? stamp(s.last_success_at) : "尚无"}</span>)}</div>
      {!!data?.pending_snapshots && <div className="hr-paused-note">还有 {data.pending_snapshots} 批快照等待Worker处理，当前聚合覆盖尚未完整。</div>}
      {error && <div className="hr-error-banner" role="alert">{error} {data ? "保留上次读到的事件，请核对时间。" : "未展示示例事件。"}</div>}
      {notice && <div className="hr-success-banner" role="status">{notice}</div>}
      <div className="hr-event-filters"><label className="hr-search"><Search /><input aria-label="搜索事件与来源标题" type="search" value={query} maxLength={300} placeholder="搜索事件或来源标题" onChange={e => setQuery(e.target.value)} /></label>
        {!following && <select aria-label="事件范围" value={scope} onChange={e => setScope(e.target.value)}><option value="all">全部事件</option><option value="cross">跨平台事件</option><option value="manual">人工修正事件</option><option value="followed">已关注事件</option></select>}
        <select aria-label="事件标签筛选" value={tag} onChange={e => setTag(e.target.value)}><option value="">全部标签</option>{allTags.map(t => <option key={t}>{t}</option>)}</select><small>匹配 {visible.length} 条</small></div>
      <div className="hr-event-grid">{visible.slice(0, limit).map(e => <article className="hr-event-card" key={e.id}>
        <div className="hr-event-card-top"><span className={"hr-tag " + (e.platforms.length > 1 ? "is-green" : "")}>{e.platforms.length > 1 ? "跨平台 · " + e.platforms.length + "个平台" : "单平台观察"}</span><span className="hr-event-status">{e.manual_locked ? "人工关系保护" : e.total_members > 1 ? "自动关联 · 待核对" : "来源条目"}{e.followed && <Star aria-label="已关注" />}</span></div>
        <h3><button className="hr-title-button" onClick={x => void open(e.id, true, x.currentTarget)}>{e.title}</button></h3>
        <div className="hr-event-summary">{e.summary.lines.slice(0, 2).map(line => <p key={line.evidence_id}>{line.text}</p>)}</div>
        <div className="hr-keyword-tags">{e.tags.map(t => <span className="hr-keyword" key={t}>{t}</span>)}</div>
        <div className="hr-event-card-bottom"><span>{e.evidence.length} 条来源 · 最近 {stamp(e.last_observed_at)}</span><button className="hr-link-button" aria-label={"查看事件：" + e.title} onClick={x => void open(e.id, true, x.currentTarget)}>来源与修正</button></div>
      </article>)}</div>
      {data && visible.length === 0 && <div className="hr-empty"><Bookmark /><h2>{following ? "该日期暂无匹配的关注事件" : "该日期暂无匹配的观测事件"}</h2><p>调整筛选或选择已有采集日期；未观测不等于热度为零。</p></div>}
      {visible.length > limit && <button className="hr-show-all" onClick={() => setLimit(n => n + 20)}>再显示20条 · 共 {visible.length} 条</button>}
    </section>
    {opened && typeof document !== "undefined" && document.getElementById("hotradar-app") && createPortal(<div className="hr-detail-layer"><button className="hr-detail-backdrop" tabIndex={-1} aria-label="关闭事件背景" onClick={close} /><section ref={drawer} className="hr-drawer hr-event-drawer" role="dialog" aria-modal="true" aria-label="热点事件详情" onKeyDown={e => {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
      if (e.key === "Tab") {
        const controls = drawer.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), summary');
        if (!controls?.length) return;
        const first = controls[0], last = controls[controls.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    }}><div className="hr-drawer-top"><span>热点事件 · 来源证据与关系修正</span><button className="hr-icon-button" aria-label="关闭事件详情" onClick={close}><X /></button></div>
      {detailError && <div className="hr-error-banner" role="alert">{detailError}<button className="hr-button" disabled={busy} onClick={() => void open(opened, false)}>重新读取</button></div>}
      {detailLoading && <p role="status">正在读取来源证据…</p>}
      {event && <><span className="hr-tag is-green">{event.platforms.length} 个独立平台 · 版本 {event.revision}</span><h2>{event.title}</h2>
        <p className="hr-drawer-summary">选定日期首次观测 {stamp(event.first_observed_at)} · 最近 {stamp(event.last_observed_at)} · {event.observation_count} 条实际采样记录</p>
        <h3>来源摘要</h3><div className="hr-event-brief"><p>{event.summary.lead}</p>{event.summary.lines.map(line => <p key={line.evidence_id}><a href={"#evidence-" + line.evidence_id}>{line.text}</a></p>)}</div>
        <div className="hr-callout">{event.summary.boundary} 来源热度分开显示，不计算跨平台总热度。</div>
        <details className="hr-summary-provenance"><summary>摘要依据与版本</summary><small>{event.summary.provider} · {event.summary.version}<br />模型：无，来源标题摘录<br />证据输入 {event.summary.input_ids.length} 条 · {event.summary.record_id ? "已持久化摘要版本" : "由已存证据生成的只读视图"}<br />签名：{event.summary.signature}</small></details>
        <h3>选定日期的来源证据</h3>{event.evidence.map(e => <EvidenceCard key={e.id} evidence={e} />)}
        {!wall && <><div className="hr-event-edit"><h3>关注与标签</h3><label>事件标签（逗号分隔）<input aria-label="事件标签" value={tags} maxLength={320} onChange={e => setTags(e.target.value)} /></label><div className="hr-drawer-actions"><button className="hr-button" disabled={!canEdit} onClick={() => void mutate("metadata", { event_id: event.id, revision: event.revision, followed: !event.followed, tags: event.tags })}><Star />{event.followed ? "取消事件关注" : "关注事件"}</button><button className="hr-button" disabled={!canEdit} onClick={() => void mutate("metadata", { event_id: event.id, revision: event.revision, followed: event.followed, tags: tags.split(/[,，]/).map(t => t.trim()).filter(Boolean) })}>保存标签</button></div></div>
          <div className="hr-event-edit"><h3>人工合并 / 拆分</h3><p className="hr-management-note">操作影响当前事件关系及历史视图，原始热榜保留。人工关系受保护；新线索仍可另行判断。</p>
            <label>修正原因<textarea aria-label="修正原因" value={reason} maxLength={300} placeholder="说明为何属于同一事件，或为何应分开" onChange={e => setReason(e.target.value)} /></label>
            <label>查找待合并事件<input aria-label="查找待合并事件" value={mergeQuery} maxLength={300} placeholder="输入另一事件的关键词" onChange={e => setMergeQuery(e.target.value)} /></label>
            <label>合并到当前事件<select aria-label="选择合并事件" value={mergeId} onChange={e => setMergeId(e.target.value)}><option value="">选择另一个事件（最多显示30条，可搜索）</option>{mergeOptions.map(e => <option key={e.id} value={e.id}>{e.title}</option>)}</select></label>
            {detail.candidates.length > 0 && <div className="hr-event-candidates"><small>文字相似线索 · 仅供人工核对</small>{detail.candidates.map(c => <button className={"hr-chip " + (mergeId === c.id ? "is-selected" : "")} key={c.id} onClick={() => setMergeId(c.id)} title={c.reason}>{c.title} · {(c.score * 100).toFixed(0)}%文字相似</button>)}</div>}
            {mergeTarget && <div className="hr-merge-preview"><strong>将保留当前事件标题，接入以下来源：</strong>{mergeTarget.evidence.map(e => <p key={e.id}>{e.source_name}：{e.title}</p>)}</div>}
            <button className="hr-button" disabled={!canEdit || !mergeTarget || reason.trim().length < 2} onClick={() => void mutate("merge", { target_id: event.id, other_ids: [mergeId], revisions: versions, reason })}><GitMerge />合并所选事件</button>
            <div className="hr-split-list"><strong>选择拆出的来源条目（包含其他日期）</strong>{detail.all_evidence.map(e => <label key={e.item_id}><input type="checkbox" checked={splitIds.includes(e.item_id)} onChange={x => setSplitIds(ids => x.target.checked ? [...ids, e.item_id] : ids.filter(id => id !== e.item_id))} />{e.source_name} · {e.title}</label>)}</div>
            <button className="hr-button" disabled={!canEdit || !splitIds.length || splitIds.length >= event.total_members || reason.trim().length < 2} onClick={() => void mutate("split", { event_id: event.id, item_ids: splitIds, revisions: { [event.id]: event.revision }, reason })}><Scissors />将 {splitIds.length} 条拆为独立事件</button>
          </div></>}
        <h3>修正记录</h3><div className="hr-event-audit">{detail.changes.length === 0 && <p>暂无人工修正记录。</p>}{detail.changes.map(c => <article key={c.id}><strong>{{ merge: "人工合并", split: "人工拆分", restore: "恢复关系", metadata: "关注与标签" }[c.kind] || c.kind}</strong><small>{stamp(c.changed_at)} · {c.reason}</small><p>{Object.values(c.before).reduce((n, s) => n + Object.keys(s.members).length, 0)} 条来源 · {Object.values(c.after).map(s => `${s.title}（${Object.keys(s.members).length}条）`).join("；")}</p>{["merge", "split"].includes(c.kind) && <><small>{c.reverted_by ? "已恢复，保留操作记录" : c.can_restore ? "当前版本允许恢复到操作前关系" : "已有后续修改，请基于当前来源手动修正"}</small>{!wall && <button className="hr-button" disabled={!canEdit || !c.can_restore || reason.trim().length < 2} onClick={() => void mutate("restore", { change_id: c.id, reason })}><Undo2 />恢复此操作前关系</button>}</>}</article>)}</div>
      </>}
    </section></div>, document.getElementById("hotradar-app")!)}
  </>;
}
