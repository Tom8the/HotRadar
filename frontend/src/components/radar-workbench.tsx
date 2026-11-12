"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Radar, LayoutDashboard, Flame, ChartNoAxesCombined, Clapperboard, Bookmark,
  Search, SunMoon, Expand, Shrink, RefreshCw, RadioTower, ListFilter, Database,
  Clock, ArrowUpRight, Star, X, ExternalLink, CalendarDays, PlugZap, ArrowLeft,
  Settings2, GripVertical, ArrowUp, ArrowDown, Pause, Play,
} from "lucide-react";
import type { Board, DashboardData, HotItem, Snapshot, SnapshotSummary } from "../lib/types";
import { defaults, normalizePreferences, reorder, visibleBoards, selectedPlatformBoard, filterItems, type Preferences } from "../lib/workspace";
import WorkspaceControls from "./workspace-controls";
import SourceManager from "./source-manager";
import EventManager from "./event-manager";
import TrendManager from "./trend-manager";
import ContentPool from "./content-pool";

function today() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}
function clock(value: string | null) {
  return value ? new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
  }).format(new Date(value)) : "尚无成功采集";
}
function metric(value: number | null | undefined) {
  if (value === null || value === undefined) return "来源未提供";
  return value >= 10000 ? (value / 10000).toFixed(1) + " 万" : value.toLocaleString("zh-CN");
}
const labels: Record<string, string> = {
  fresh: "最新", partial: "部分解析", pending: "待采集", stale: "已过期",
  failed: "采集失败", collecting: "采集中",
  paused: "采集已暂停",
};
const nav = [
  { id: "overview", name: "热榜总览", Icon: LayoutDashboard },
  { id: "events", name: "热点事件", Icon: Flame },
  { id: "trends", name: "趋势观察", Icon: ChartNoAxesCombined },
  { id: "contents", name: "爆款内容", Icon: Clapperboard },
  { id: "following", name: "我的关注", Icon: Bookmark },
  { id: "sources", name: "来源管理", Icon: Settings2 },
];

export default function RadarWorkbench() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [date, setDate] = useState(today);
  const followsToday = useRef(true);
  const [view, setView] = useState("overview");
  const [eventModalOpen, setEventModalOpen] = useState(false);
  const [followingKind, setFollowingKind] = useState("boards");
  const [wall, setWall] = useState(false);
  const [preferences, setPreferences] = useState<Preferences>(defaults);
  const [preferencesReady, setPreferencesReady] = useState(false);
  const [preferenceError, setPreferenceError] = useState("");
  const { source, dark, favorites, query } = preferences;
  const contentSource = source === "baidu" ? "all" : source;
  const [expanded, setExpanded] = useState<string[]>([]);
  const [boardDetail, setBoardDetail] = useState<Board | null>(null);
  const [history, setHistory] = useState<SnapshotSummary[]>([]);
  const [detailSnapshot, setDetailSnapshot] = useState<Snapshot | null>(null);
  const [detailError, setDetailError] = useState("");
  const [selectedItem, setSelectedItem] = useState<{ item: HotItem; board: Board } | null>(null);
  const readSequence = useRef(0);
  const detailSequence = useRef(0);
  const [detailLoading, setDetailLoading] = useState(false);
  const [cardLoading, setCardLoading] = useState<string[]>([]);
  const [cardErrors, setCardErrors] = useState<Record<string, string>>({});
  const [dragging, setDragging] = useState("");
  const currentDate = useRef(date);
  const drawer = useRef<HTMLElement>(null);
  currentDate.current = date;
  function selectDate(value: string) { followsToday.current = value === today(); setDate(value); }
  useEffect(() => {
    if (!preferencesReady || !preferences.autoRefresh) return;
    const timer = setInterval(() => { if (followsToday.current) setDate(previous => previous === today() ? previous : today()); }, 30000);
    return () => clearInterval(timer);
  }, [preferencesReady, preferences.autoRefresh]);
  useEffect(() => {
    if (!selectedItem && !boardDetail) return;
    const previous = document.activeElement as HTMLElement | null;
    drawer.current?.querySelector<HTMLButtonElement>('button[aria-label="关闭详情"]')?.focus();
    return () => previous?.focus();
  }, [selectedItem, boardDetail]);

  useEffect(() => {
    try {
      const saved = JSON.parse(localStorage.getItem("hotradar-preferences-v2") || localStorage.getItem("hotradar-preferences-v1") || "{}");
      setPreferences(normalizePreferences(saved));
    } catch { setPreferenceError("本地偏好读取失败，正在使用默认设置。"); }
    setPreferencesReady(true);
  }, []);
  useEffect(() => {
    if (!preferencesReady) return;
    try { localStorage.setItem("hotradar-preferences-v2", JSON.stringify(preferences)); }
    catch { setPreferenceError("浏览器未能保存偏好；当前操作仍可使用，刷新后可能丢失。"); }
  }, [preferences, preferencesReady]);
  const updatePreferences = useCallback((change: Partial<Preferences>) => {
    setPreferences(previous => normalizePreferences({ ...previous, ...change }));
  }, []);
  function savePreferences(nextFavorites: string[], nextDark: boolean) {
    updatePreferences({ favorites: nextFavorites, dark: nextDark });
  }
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++readSequence.current;
    setLoading(true);
    try {
      const response = await fetch("/api/radar?date=" + date, { cache: "no-store", signal });
      const result = await response.json();
      if (sequence !== readSequence.current || signal?.aborted) return;
      if (!response.ok) throw new Error(result.error || "数据读取失败");
      setData(result);
      setError("");
    } catch (e) {
      if (signal?.aborted || sequence !== readSequence.current) return;
      setError(e instanceof Error ? e.message : "数据读取失败");
    } finally { if (sequence === readSequence.current) setLoading(false); }
  }, [date]);
  useEffect(() => {
    setData(null);
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => { ++readSequence.current; controller.abort(); };
  }, [refresh]);
  useEffect(() => {
    if (!preferencesReady || !preferences.autoRefresh) return;
    const controller = new AbortController();
    const timer = setInterval(() => void refresh(controller.signal), 30000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [refresh, preferences.autoRefresh, preferencesReady]);

  async function refreshCard(boardId: string) {
    const requestedDate = date;
    setCardLoading(previous => [...previous, boardId]);
    try {
      const response = await fetch(`/api/radar?board=${boardId}&view=1&date=${requestedDate}`, { cache: "no-store" });
      const result = await response.json(); if (!response.ok) throw new Error(result.error);
      if (currentDate.current !== requestedDate) return;
      setData(previous => !previous || previous.selected_date !== requestedDate || result.read_at < previous.read_at ? previous : {
        ...previous, boards: previous.boards.map(b => b.id === boardId ? result.board : b),
      });
      setCardErrors(previous => ({ ...previous, [boardId]: "" }));
    } catch { if (currentDate.current === requestedDate) setCardErrors(previous => ({ ...previous, [boardId]: "读取失败，保留当前快照。" })); }
    finally { setCardLoading(previous => previous.filter(id => id !== boardId)); }
  }

  function closeDetail() { ++detailSequence.current; setSelectedItem(null); setBoardDetail(null); setDetailLoading(false); }

  async function openBoard(board: Board) {
    const sequence = ++detailSequence.current;
    setDetailLoading(true);
    setSelectedItem(null); setBoardDetail(board); setDetailSnapshot(board.snapshot); setHistory([]); setDetailError("");
    try {
      const response = await fetch("/api/radar?board=" + board.id + "&date=" + date);
      const result = await response.json();
      if (sequence !== detailSequence.current) return;
      if (!response.ok) throw new Error(result.error);
      setHistory(result.snapshots);
    } catch { if (sequence === detailSequence.current) setDetailError("历史快照读取失败，请稍后重试。"); }
    finally { if (sequence === detailSequence.current) setDetailLoading(false); }
  }
  async function selectSnapshot(id: string) {
    if (!boardDetail) return;
    const sequence = ++detailSequence.current;
    setDetailLoading(true);
    try {
      const response = await fetch("/api/radar?board=" + boardDetail.id + "&snapshot=" + id);
      const result = await response.json();
      if (sequence !== detailSequence.current) return;
      if (!response.ok) throw new Error(result.error);
      setDetailSnapshot(result.snapshot); setDetailError("");
    } catch { if (sequence === detailSequence.current) setDetailError("该快照未能读取。"); }
    finally { if (sequence === detailSequence.current) setDetailLoading(false); }
  }
  function favorite(boardId: string) {
    const next = favorites.includes(boardId) ? favorites.filter(x => x !== boardId) : [...favorites, boardId];
    savePreferences(next, dark);
  }
  const current = nav.find(n => n.id === view)!;
  const boards = visibleBoards(data?.boards || [], preferences, view === "following");
  const healthy = data?.stats.healthy_sources;
  return (
    <div id="hotradar-app" data-theme={dark ? "dark" : "light"} data-view={view} data-screen={wall ? "wall" : "desk"} style={{ colorScheme: dark ? "dark" : "light" }}>
      <div className="hr-shell" inert={!!selectedItem || !!boardDetail || eventModalOpen}>
        <aside className="hr-sidebar">
          <div className="hr-logo"><span className="hr-logo-mark"><Radar /></span><div>HotRadar<small>热点雷达</small></div></div>
          <div className="hr-workspace"><span className="hr-avatar">H</span><div>MCN 运营团队<small>本地工作区</small></div></div>
          <div className="hr-nav-caption">发现与观察</div>
          <nav className="hr-navigation" aria-label="功能页面">{nav.map(({ id, name, Icon }) => (
            <button key={id} type="button" className={"hr-nav-item " + (view === id ? "is-active" : "")}
              onClick={() => setView(id)} aria-current={view === id ? "page" : undefined}><Icon /><span>{name}</span>
              {id === "contents" && <span className="hr-nav-count">核查</span>}
            </button>
          ))}</nav>
          <div className="hr-sidebar-bottom"><div className="hr-health-note"><span className="hr-dot" />真实采集 · M4<small>抖音 / 微博 / 百度，保留来源与快照</small></div>
            <div className="hr-member"><span className="hr-avatar">运</span><div>运营团队<small>HotRadar · 第一阶段</small></div></div></div>
        </aside>
        <main className="hr-main">
          <header className="hr-topbar">
            <div className="hr-crumb">工作台 <span>/</span><strong>{current.name}</strong></div>
            <div className="hr-top-actions">{(view === "overview" || view === "contents" || (view === "following" && followingKind === "boards")) && <label className="hr-search"><Search /><input aria-label={view === "contents" ? "搜索已核查内容" : "搜索当前榜单"} type="search" maxLength={300}
              placeholder={view === "contents" ? "搜索内容或作者" : "搜索当前热榜内容"} value={query} onChange={e => updatePreferences({ query: e.target.value })} /></label>}
              <button type="button" className="hr-icon-button" aria-label="切换深浅主题" onClick={() => savePreferences(favorites, !dark)}><SunMoon /></button>
              <span className="hr-demo-tag">M4 · 真实历史观察</span></div>
          </header>
          <section className="hr-page-heading"><div><div className="hr-overline">让热点更早被看见</div><h1>{current.name}</h1>
            <p>{view === "overview" ? "汇集抖音、微博与百度热榜，保留原始榜位、源内指标和采样时间。" : view === "events" ? "关联跨平台热点，核对来源证据，保留人工修正与摘要依据。" : view === "trends" ? "观察真实采样中的榜位与源内指标，明确历史覆盖和数据缺口。" : view === "following" ? "持续观察关注的平台榜单和热点事件。" : view === "sources" ? "查看实际采集任务，管理各来源采集周期与启停。" : "查看已验证来源的具体内容、作者、发布时间、互动数与采样依据。"}</p></div>
            <div className="hr-heading-actions"><button type="button" className="hr-button" onClick={() => { setWall(!wall); if (!wall) updatePreferences({ dark: true }); }}>{wall ? <Shrink /> : <Expand />}{wall ? "返回工作台" : "大屏模式"}</button>
              <button className="hr-button" onClick={() => updatePreferences({ autoRefresh: !preferences.autoRefresh })}>{preferences.autoRefresh ? <Pause /> : <Play />}{preferences.autoRefresh ? "暂停自动刷新" : "恢复自动刷新"}</button>
              <button type="button" className="hr-button hr-button-primary" onClick={() => void refresh()} disabled={loading}><RefreshCw className={loading ? "is-spinning" : ""} />{loading ? "读取中" : "刷新视图"}</button></div></section>
          <div className="hr-context-bar">{view !== "sources" && <div className="hr-period"><button type="button" className={date === today() ? "is-selected" : ""} onClick={() => selectDate(today())}>今日</button>
            <label className="hr-date"><CalendarDays /><input aria-label="选择快照日期" type="date" value={date} max={today()} onInput={e => { if (e.currentTarget.value && e.currentTarget.validity.valid) selectDate(e.currentTarget.value); }} onChange={e => { if (e.target.value && e.target.validity.valid) selectDate(e.target.value); }} /></label></div>}
            <span className="hr-sampling"><span className="hr-dot" />{data ? <time dateTime={data.read_at}>{"视图读取 " + clock(data.read_at) + " · 北京时间"}</time> : "等待数据服务"}</span></div>
          {!preferences.autoRefresh && <div className="hr-paused-note" role="status">页面自动读取已暂停，采集Worker继续运行；当前显示读取时的快照，可手动刷新。</div>}
          {preferenceError && <div role="alert" className="hr-error-banner">{preferenceError}</div>}
          {error && <div role="alert" className="hr-error-banner"><PlugZap /><div><strong>{error}</strong><small>{data ? "下方保留上次读到的快照，请核对采样时间。" : "未展示演示数据或伪造的空榜。"}</small></div><button className="hr-button" onClick={() => void refresh()}>重试读取</button></div>}
          {view === "overview" && <>
            <div className="hr-stats">{[
              ["有效榜单", <>{healthy ?? "—"}<span> / {data?.stats.enabled_sources ?? "—"}</span></>, "抖音 / 微博 / 百度，各榜单独立采集", RadioTower],
              ["当日新收录", data?.stats.new_items ?? "—", "当日首次被本机观测的条目", ListFilter],
              ["当日热榜条目", data?.stats.visible_items ?? "—", "选定日期最后快照的条目数", Flame],
              ["已存快照", data?.stats.snapshots ?? "—", "选定日期的持久化快照", Database],
            ].map(([label, value, note, Icon], i) => { const StatIcon = Icon as typeof RadioTower; return <div className="hr-stat" key={i}><div className="hr-stat-label">{label as string}<StatIcon /></div><div className="hr-stat-value">{value as React.ReactNode}</div><div className="hr-stat-foot">{note as string}</div></div>; })}</div>
            <div className="hr-section-row"><h2>平台热榜 <small>保留来源，分开看热度</small></h2><span className="hr-filter-tail">{preferences.autoRefresh ? "每30秒读取" : "自动读取暂停"} · 采集周期见来源管理</span></div>
          </>}
          {view === "following" && <div className="hr-category-row"><button className={"hr-chip " + (followingKind === "boards" ? "is-selected" : "")} onClick={() => setFollowingKind("boards")}>关注平台榜单</button><button className={"hr-chip " + (followingKind === "events" ? "is-selected" : "")} onClick={() => setFollowingKind("events")}>关注热点事件</button></div>}
          {(view === "events" || (view === "following" && followingKind === "events")) && <EventManager key={view} date={date} refreshKey={data?.read_at || ""} following={view === "following"} wall={wall} onModalChange={setEventModalOpen} />}
          {view === "trends" && <TrendManager date={date} refreshKey={data?.read_at || ""} wall={wall} />}
          {(view === "overview" || (view === "following" && followingKind === "boards")) && <>
            <div className="hr-category-row">{[["all", "全部平台"], ["douyin", "抖音"], ["weibo", "微博"], ["baidu", "百度"]].map(([id, name]) => <button key={id} className={"hr-chip " + (source === id ? "is-selected" : "")} onClick={() => updatePreferences({ source: id })}>{name}</button>)}
              {!wall && <button className="hr-link-button" onClick={() => updatePreferences({ order: [...defaults.order] })}>恢复默认卡片顺序</button>}</div>
            <WorkspaceControls preferences={preferences} update={updatePreferences} wall={wall} />
          </>}
          {(view === "overview" || (view === "following" && followingKind === "boards")) && <div className="hr-boards">
            {boards.map((card, boardIndex) => {
              const board = selectedPlatformBoard(card, data?.boards || [], preferences);
              const cardId = card.id;
              const items = filterItems(board.snapshot?.items || [], preferences, query);
              const showAll = expanded.includes(board.id);
              const visible = showAll ? items : items.slice(0, 15);
              return <article className={"hr-board " + (dragging === cardId ? "is-dragging" : "")} key={cardId} data-board-id={board.id}
                onDragOver={e => { if (!wall) e.preventDefault(); }} onDrop={e => { e.preventDefault(); if (!wall) updatePreferences({ order: reorder(preferences.order, e.dataTransfer.getData("text/plain"), cardId) }); setDragging(""); }}>
                <div className="hr-board-heading"><span className={"hr-platform-logo is-" + board.source_id}>{board.source_id === "baidu" ? "百" : board.source_id === "weibo" ? "微" : "抖"}</span><div><div className="hr-board-name">{board.source_name}</div><small>{board.name}</small></div>
                  <button type="button" className={"hr-icon-button " + (favorites.includes(cardId) ? "is-favorite" : "")} aria-label={(favorites.includes(cardId) ? "取消关注" : "关注") + board.source_name + "榜单"} onClick={() => favorite(cardId)}><Star /></button>
                  {!wall && <div className="hr-card-tools"><button className="hr-icon-button hr-drag-handle" aria-label={"拖动" + board.source_name + "卡片"} draggable onDragStart={e => { e.dataTransfer.setData("text/plain", cardId); e.dataTransfer.effectAllowed = "move"; setDragging(cardId); }} onDragEnd={() => setDragging("")}><GripVertical /></button>
                    <button className="hr-icon-button" aria-label={"前移" + board.source_name + "卡片"} disabled={boardIndex === 0} onClick={() => updatePreferences({ order: reorder(preferences.order, cardId, boards[boardIndex - 1].id) })}><ArrowUp /></button>
                    <button className="hr-icon-button" aria-label={"后移" + board.source_name + "卡片"} disabled={boardIndex === boards.length - 1} onClick={() => updatePreferences({ order: reorder(preferences.order, cardId, boards[boardIndex + 1].id) })}><ArrowDown /></button>
                    <button className="hr-icon-button" aria-label={"刷新" + board.source_name + "视图"} disabled={cardLoading.includes(board.id)} onClick={() => void refreshCard(board.id)}><RefreshCw className={cardLoading.includes(board.id) ? "is-spinning" : ""} /></button></div>}
                </div>
                {cardId === "douyin-hot" && <div className="hr-board-tabs" role="group" aria-label="抖音榜单切换">
                  {[["douyin-hot", "🔥 总榜单"], ["douyin-wuxi-app", "📍 无锡榜"], ["douyin-wuxi", "创作参考"]].map(([id, label]) => <button key={id} type="button" className={"hr-chip " + (preferences.douyinBoard === id ? "is-selected" : "")} aria-pressed={preferences.douyinBoard === id} onClick={() => { closeDetail(); updatePreferences({ douyinBoard: id as Preferences["douyinBoard"] }); }}>{label}</button>)}
                </div>}
                {cardId === "weibo-hot" && <div className="hr-board-tabs" role="group" aria-label="微博榜单切换">
                  {[["weibo-hot", "🔥 总榜单"], ["weibo-wuxi", "📍 无锡榜"]].map(([id, label]) => <button key={id} type="button" className={"hr-chip " + (preferences.weiboBoard === id ? "is-selected" : "")} aria-pressed={preferences.weiboBoard === id} onClick={() => { closeDetail(); updatePreferences({ weiboBoard: id as Preferences["weiboBoard"] }); }}>{label}</button>)}
                </div>}
                <div className="hr-board-caption"><span>{board.snapshot ? clock(board.snapshot.observed_at) + " 采样" : "当日暂无快照"}</span>
                  <span className={"hr-tag " + (date !== today() ? "" : board.state === "fresh" ? "is-green" : "is-orange")}>{date !== today() ? "历史记录" : labels[board.state]}{board.snapshot?.from_cache ? " · 上游缓存" : ""}</span></div>
                {board.id === "douyin-wuxi" && <div className="hr-board-warning" role="note">来源为抖音创作者中心，仅作创作参考。查看 App 同款城市榜请切换“📍 无锡榜”，两者历史独立保存。</div>}
                {board.latest_run?.error_code && <div className="hr-board-warning">最近采集：{board.latest_run.error_code === "session_required" ? "需要刷新本机" + (board.source_id === "weibo" ? "微博登录会话" : "抖音创作者中心会话") : board.latest_run.error_code === "city_unverified" ? "城市未确认为无锡，请在官方同城页重新选择无锡" : board.latest_run.error_code === "partial_parse" ? "部分条目未能解析" : "失败，保留历史快照"}</div>}
                {cardErrors[board.id] && <div className="hr-board-warning" role="alert">{cardErrors[board.id]}</div>}
                {board.snapshot && <div className="hr-result-count">匹配 {items.length} / 来源 {board.snapshot.item_count} 条 · {board.id === "douyin-wuxi" ? "按同城列表顺序" : "不改变源排名"}</div>}
                {!board.snapshot ? <div className="hr-empty"><Clock /><h2>该日期暂无采集记录</h2><p>{board.id.endsWith("-wuxi") ? "无锡榜从首次成功采样开始留存；未采集日期不补造。" : "请选取已有快照的日期；历史不会补造。"}</p><small>最近成功：{clock(board.last_success_at)}</small></div> :
                  board.snapshot.item_count === 0 ? <div className="hr-empty"><h2>来源返回空榜单</h2><p>此次请求成功，条目列表为空。</p></div> :
                  <ol className="hr-board-list">{visible.map(item => <li key={item.id}><span title={board.id === "douyin-wuxi" ? "同城列表序号；平台原始榜位见详情" : "来源榜位"} className={"hr-rank " + ((board.id === "douyin-wuxi" ? item.display_order <= 3 : item.rank !== null && item.rank <= 3) ? "is-hot" : "")}>{board.id === "douyin-wuxi" ? item.display_order : item.rank ?? (item.is_pinned ? "置顶" : "—")}</span>
                    <div className="hr-row-title"><a className="hr-title-button" href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a>
                      {item.flags.length > 0 && <span className="hr-row-flags">{item.flags.join(" · ")}</span>}</div>
                    <span className="hr-row-metric">{metric(item.metrics[board.metric_name]?.value)}{item.rank_change !== null && item.rank_change !== 0 && <small className={item.rank_change > 0 ? "hr-positive" : ""}>{item.rank_change > 0 ? "↑ " : "↓ "}{Math.abs(item.rank_change)}</small>}</span>
                    <button type="button" className="hr-row-details" aria-label={"查看详情：" + item.title} onClick={() => { closeDetail(); setSelectedItem({ item, board }); }}><ArrowUpRight /></button>
                  </li>)}{items.length === 0 && <li>没有匹配的已采集条目</li>}</ol>}
                <div className="hr-board-footer"><small>{board.id === "weibo-wuxi" && <>官方无锡同城热搜 · 非仅本地事件<br /></>}{board.id === "douyin-wuxi-app" && <>抖音原生无锡榜 · 官方热榜来源<br />历史始于实际首次采样<br /></>}{board.id === "douyin-wuxi" && <>创作者中心顺序 · 原始榜位保留在详情<br /></>}{board.metric_label} · 非播放量<br />箭头对比前次可比采样榜位{board.source_id === "weibo" && <><br />原文可能需要微博登录</>}</small><button type="button" className="hr-link-button" onClick={() => void openBoard(board)}>历史快照 <ArrowUpRight /></button></div>
                {items.length > 15 && <button className="hr-show-all" onClick={() => setExpanded(showAll ? expanded.filter(x => x !== board.id) : [...expanded, board.id])}>{showAll ? "收起榜单" : "展开全部 " + items.length + " 条"}</button>}
              </article>;
            })}
          </div>}
          {(view === "overview" || (view === "following" && followingKind === "boards")) && data && boards.length === 0 && <div className="hr-empty"><Bookmark /><h2>当前分组与平台筛选下没有榜单</h2><p>调整分组成员、平台筛选或关注清单；这不代表来源没有热点。</p><button className="hr-button" onClick={() => { setView("overview"); updatePreferences({ activeGroup: "all", source: "all" }); }}>查看全部平台</button></div>}
          {view === "sources" && <SourceManager onChange={() => void refresh()} autoRefresh={preferences.autoRefresh} />}
          {view === "contents" && <><div className="hr-category-row">{[["all", "全部平台"], ["douyin", "抖音"], ["weibo", "微博"]].map(([id, name]) => <button key={id} className={"hr-chip " + (contentSource === id ? "is-selected" : "")} onClick={() => updatePreferences({ source: id })}>{name}</button>)}</div><ContentPool date={date} source={contentSource} query={query} refreshKey={data?.read_at || ""} /></>}
          <div className="hr-insight"><Database /><div><strong>每一次榜单变化，都有原始快照可追溯</strong><p>{data?.first_observed_at ? "实际留存始于 " + clock(data.first_observed_at) + "。未采集日期不补值。" : "采集尚未成功时，页面保留明确的来源状态。"}</p></div></div>
          <footer className="hr-footer"><span>HotRadar · MCN 热点情报</span><span>抖音 / 微博 / 百度 · 来源指标不可跨平台相加</span></footer>
        </main>
      </div>
      {(selectedItem || boardDetail) && <div className="hr-detail-layer"><button className="hr-detail-backdrop" tabIndex={-1} aria-label="关闭详情背景" onClick={closeDetail} /><section ref={drawer} className="hr-drawer" role="dialog" aria-modal="true" aria-label={selectedItem ? "条目详情" : "历史快照"} onKeyDown={e => {
        if (e.key === "Escape") { e.stopPropagation(); closeDetail(); }
        if (e.key === "Tab") {
          const controls = drawer.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], select:not(:disabled)');
          if (!controls?.length) return;
          const first = controls[0], last = controls[controls.length - 1];
          if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
          else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
        }
      }}>
        <div className="hr-drawer-top"><span>{selectedItem ? "真实热榜条目" : "持久化历史快照"}</span><button className="hr-icon-button" aria-label="关闭详情" onClick={closeDetail}><X /></button></div>
        {selectedItem ? <><span className="hr-tag is-green">{selectedItem.board.source_name} · {selectedItem.board.name}</span><h2>{selectedItem.item.title}</h2><p className="hr-drawer-summary">排名 {selectedItem.item.rank ?? (selectedItem.item.is_pinned ? "置顶" : "来源未提供")} · {selectedItem.board.metric_label} {metric(selectedItem.item.metrics[selectedItem.board.metric_name]?.value)}</p>
          <div className="hr-callout">{selectedItem.item.metrics[selectedItem.board.metric_name]?.definition}。这是一条话题热榜记录，不是具体视频或已经核实的新闻摘要。</div>
          <div className="hr-evidence"><strong>{selectedItem.board.metric_label}原值</strong><small>{selectedItem.item.metrics[selectedItem.board.metric_name]?.value?.toLocaleString("zh-CN") ?? "来源未提供"}</small></div>
          <div className="hr-evidence"><strong>采样时间</strong><small>{clock(selectedItem.board.snapshot?.observed_at || null)}</small></div>
          <div className="hr-evidence"><strong>首次观测</strong><small>{clock(selectedItem.item.first_observed_at)}</small></div>
          <div className="hr-evidence"><strong>来源事件时间</strong><small>{selectedItem.item.source_event_at ? clock(selectedItem.item.source_event_at) : "来源未提供"}</small></div>
          <div className="hr-evidence"><strong>解析版本</strong><small>{selectedItem.board.snapshot?.parser_version}</small></div>
          <a className="hr-button hr-button-primary" href={selectedItem.item.url} target="_blank" rel="noopener noreferrer"><ExternalLink />打开原始来源</a></> :
          boardDetail && <><span className="hr-tag">{boardDetail.source_name}</span><h2>{boardDetail.name} · 历史快照</h2><p className="hr-drawer-summary">仅显示 {date} 实际成功留存的批次。选择时间可回放当时榜单。</p>
            {detailError && <div role="alert" className="hr-error-banner">{detailError}</div>}
            {detailLoading && <p role="status">正在读取历史快照…</p>}
            <select className="hr-snapshot-select" aria-label="选择历史快照" disabled={detailLoading} value={detailSnapshot?.id || ""} onChange={e => void selectSnapshot(e.target.value)}><option value="" disabled>请选择快照</option>{history.map(s => <option key={s.id} value={s.id}>{clock(s.observed_at)} · {s.item_count} 条</option>)}</select>
            {!detailLoading && !detailError && history.length === 0 && <div className="hr-callout">该日期暂无持久化快照。</div>}
            {detailSnapshot && <><small>{clock(detailSnapshot.observed_at)} · {detailSnapshot.item_count} 条 · {detailSnapshot.parser_version}</small><ol className="hr-history-list">{detailSnapshot.items.map(item => <li key={item.id}><span>{item.rank ?? (item.is_pinned ? "置顶" : "—")}</span><a href={item.url} target="_blank" rel="noopener noreferrer">{item.title}</a><small>{metric(item.metrics[boardDetail.metric_name]?.value)}</small></li>)}</ol></>}
          </>}
      </section></div>}
    </div>
  );
}
