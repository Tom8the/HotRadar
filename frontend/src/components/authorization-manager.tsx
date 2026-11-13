"use client";

import { ArrowLeft, KeyRound, QrCode, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { notifyPlatformSessionUpdated, watchPlatformSessionUpdates } from "../lib/platform-session-updates";
import BrowserLoginDialog from "./browser-login-dialog";

const groups = [
  {
    platform: "抖音",
    sources: [
      { id: "douyin-hot", name: "抖音总榜", note: "默认匿名采集。匿名请求失败时，可使用已验证的登录会话。" },
      { id: "douyin-creator-hot", name: "创作参考 · 全国创作热点", note: "登录抖音创作者中心，验证全国创作热点榜。" },
      { id: "douyin-content", name: "抖音精选内容", note: "在抖音精选登录并打开全部分类，同一浏览器会话的创作者中心选择无锡。" },
    ],
  },
  {
    platform: "微博",
    sources: [
      {
        id: "weibo",
        name: "微博热门与无锡同城",
        note: "登录微博网页版。打开同城页并选择无锡后验证，同一次登录会同时启用热门内容和无锡同城榜。",
      },
    ],
  },
];

type Session = {
  state: "saved" | "missing" | "expired" | "unavailable";
  saved_at: string | null;
};
type AuthorizationStatus = { storage_configured: boolean; browser_available: boolean; sources: Record<string, Session> };

const states: Record<Session["state"], string> = {
  saved: "授权会话已保存", missing: "待扫码授权", expired: "会话已过期", unavailable: "会话无法读取",
};

function weiboSummary(sources: Record<string, Session> | undefined) {
  const content = sources?.["weibo-content"];
  const city = sources?.["weibo-wuxi"];
  const savedAt = [content?.saved_at, city?.saved_at].filter((value): value is string => !!value).sort().at(-1) ?? null;
  const savedCount = [content, city].filter(item => item?.state === "saved").length;
  if (savedCount === 2) return { label: states.saved, tone: "is-green", savedAt, complete: true };
  if (savedCount === 1) return { label: "仅部分来源已授权", tone: "is-orange", savedAt, complete: false };
  if ([content, city].some(item => item?.state === "unavailable")) return { label: states.unavailable, tone: "is-orange", savedAt: null, complete: false };
  if ([content, city].some(item => item?.state === "expired")) return { label: states.expired, tone: "is-orange", savedAt, complete: false };
  return { label: states.missing, tone: "is-orange", savedAt: null, complete: false };
}

function savedTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "short",
  }).format(new Date(value));
}

export default function AuthorizationManager() {
  const [status, setStatus] = useState<AuthorizationStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusError, setStatusError] = useState("");
  const [selected, setSelected] = useState<{ id: string; name: string; note: string } | null>(null);
  const [success, setSuccess] = useState("");
  const readSequence = useRef(0);
  const load = useCallback(async (signal?: AbortSignal) => {
    const sequence = ++readSequence.current;
    setLoading(true);
    try {
      const response = await fetch("/api/platform-sessions", { cache: "no-store", signal });
      if (!response.ok) throw new Error("Unavailable");
      const data = await response.json();
      if (!signal?.aborted && sequence === readSequence.current) {
        setStatus(data);
        setStatusError("");
      }
    } catch {
      if (!signal?.aborted && sequence === readSequence.current) setStatusError("会话状态读取失败，请检查 HotRadar 服务后重试。");
    } finally {
      if (!signal?.aborted && sequence === readSequence.current) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    const unsubscribe = watchPlatformSessionUpdates(() => void load(controller.signal));
    return () => { unsubscribe(); controller.abort(); };
  }, [load]);

  return <main id="hotradar-app" className="hr-authorization" data-theme="light">
    <header className="hr-authorization-header">
      <a className="hr-button" href="/"><ArrowLeft />返回工作台</a>
      <button className="hr-button" disabled={loading} onClick={() => void load()}><RefreshCw />读取会话状态</button>
    </header>
    <section className="hr-page-heading">
      <div><h1><KeyRound />采集授权</h1>
        <p>在弹窗内打开平台页面并扫码，完成登录验证后启用对应来源。</p>
      </div>
    </section>
    {statusError && <div className="hr-error-banner" role="alert">{statusError}</div>}
    {success && <div className="hr-success-banner" role="status">{success}</div>}
    {status && !status.browser_available && <p className="hr-authorization-notice" role="status">
      {status.storage_configured ? "服务器尚未安装网页扫码组件。" : "服务器尚未配置会话加密密钥。"}
    </p>}
    {groups.map(group => <section className="hr-authorization-group" key={group.platform}>
      <h2>{group.platform}</h2>
      {group.sources.map(source => {
        const sources = status?.sources;
        const session = source.id === "weibo" ? undefined : sources?.[source.id];
        const combined = source.id === "weibo" && sources ? weiboSummary(sources) : null;
        const savedAt = combined?.savedAt ?? session?.saved_at;
        const complete = combined ? combined.complete : session?.state === "saved";
        const label = combined?.label ?? (session ? (source.id === "douyin-hot" && session.state === "missing" ? "匿名采集" : states[session.state]) : "");
        const tone = combined?.tone ?? (session?.state === "saved" ? "is-green" : "is-orange");
        return <article className="hr-source-card hr-authorization-card" id={source.id} key={source.id}>
          <div className="hr-section-row"><h3>{source.name}</h3>
            {(session || combined) && <span className={"hr-tag " + tone}>{label}</span>}
          </div>
          <p>{source.note}</p>
          {source.id === "weibo" && sources && <small>热门内容：{states[sources["weibo-content"]?.state ?? "missing"]}；无锡同城：{states[sources["weibo-wuxi"]?.state ?? "missing"]}</small>}
          {savedAt && <small>保存于 {savedTime(savedAt)}（北京时间）</small>}
          <div className="hr-authorization-actions">
            <button className="hr-button hr-button-primary" aria-haspopup="dialog" disabled={!status?.browser_available} onClick={() => {
              setSuccess("");
              setSelected(source);
            }}>
              <QrCode />{complete ? "重新扫码授权" : "扫码授权"}
            </button>
          </div>
        </article>;
      })}
    </section>)}
    <section className="hr-authorization-group">
      <h2>公开来源</h2>
      <article className="hr-source-card hr-authorization-card">
        <p>微博热搜、百度实时热搜无需登录。</p>
        <p>抖音原生无锡榜无需登录。服务器收到其他地区的榜单时，采集会显示城市校验失败并保留历史。</p>
      </article>
    </section>
    {selected && <BrowserLoginDialog source={selected.id} name={selected.name} note={selected.note}
      onClose={() => setSelected(null)} onVerified={(session, count, summary) => {
        setStatus(previous => {
          if (!previous) return previous;
          const sources = selected.id === "weibo"
            ? { ...previous.sources, "weibo-content": session, "weibo-wuxi": session }
            : { ...previous.sources, [selected.id]: session };
          return { ...previous, sources };
        });
        setSuccess(summary ?? `${selected.name}授权成功，已启用采集。本次留存 ${count} 条。`);
        notifyPlatformSessionUpdated();
        void load();
      }} />}
  </main>;
}
