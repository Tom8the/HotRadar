"use client";

import { ArrowLeft, KeyRound, QrCode, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import BrowserLoginDialog from "./browser-login-dialog";

const groups = [
  {
    platform: "抖音",
    sources: [
      { id: "douyin-hot", name: "抖音总榜", note: "默认匿名采集。匿名请求失败时，可使用已验证的登录会话。" },
      { id: "douyin-wuxi", name: "无锡创作热点", note: "在抖音创作者中心登录，并在同城榜中选择无锡。" },
      { id: "douyin-content", name: "抖音精选内容", note: "在抖音精选登录并打开全部分类，同一浏览器会话的创作者中心选择无锡。" },
    ],
  },
  {
    platform: "微博",
    sources: [
      { id: "weibo-wuxi", name: "微博同城无锡榜", note: "在移动微博登录，并在同城热搜中选择无锡。" },
      { id: "weibo-content", name: "微博热门内容", note: "在微博网页版登录并打开热门推荐，同一浏览器会话的移动微博选择无锡。" },
    ],
  },
];

type Session = {
  state: "saved" | "missing" | "expired" | "unavailable";
  saved_at: string | null;
};
type AuthorizationStatus = { import_available: boolean; browser_available: boolean; sources: Record<string, Session> };

const states: Record<Session["state"], string> = {
  saved: "已保存会话", missing: "待扫码授权", expired: "会话已过期", unavailable: "会话无法读取",
};

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
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const response = await fetch("/api/platform-sessions", { cache: "no-store", signal });
      if (!response.ok) throw new Error("Unavailable");
      const data = await response.json();
      if (!signal?.aborted) {
        setStatus(data);
        setStatusError("");
      }
    } catch {
      if (!signal?.aborted) setStatusError("会话状态读取失败，请检查 HotRadar 服务后重试。");
    } finally {
      if (!signal?.aborted) setLoading(false);
    }
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
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
    {status && !status.browser_available && <p className="hr-authorization-notice" role="status">
      {status.import_available ? "服务器尚未安装网页扫码组件。" : "服务器尚未配置会话加密密钥和管理令牌。"}
    </p>}
    {groups.map(group => <section className="hr-authorization-group" key={group.platform}>
      <h2>{group.platform}</h2>
      {group.sources.map(source => {
        const session = status?.sources[source.id];
        return <article className="hr-source-card hr-authorization-card" key={source.id}>
          <div className="hr-section-row"><h3>{source.name}</h3>
            {session && <span className={"hr-tag " + (session.state === "saved" ? "is-green" : "is-orange")}>{source.id === "douyin-hot" && session.state === "missing" ? "匿名采集" : states[session.state]}</span>}
          </div>
          <p>{source.note}</p>
          {session?.saved_at && <small>保存于 {savedTime(session.saved_at)}（北京时间）</small>}
          <div className="hr-authorization-actions">
            <button className="hr-button hr-button-primary" aria-haspopup="dialog" disabled={!status?.browser_available} onClick={() => setSelected(source)}>
              <QrCode />{session?.state === "saved" ? "重新扫码授权" : "扫码授权"}
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
      onClose={() => setSelected(null)} onVerified={() => void load()} />}
  </main>;
}
