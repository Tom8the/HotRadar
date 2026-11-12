"use client";

import { ArrowLeft, ExternalLink, KeyRound, RefreshCw } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";

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
  login_url: string;
  state: "saved" | "missing" | "expired" | "unavailable";
  saved_at: string | null;
};
type AuthorizationStatus = { import_available: boolean; sources: Record<string, Session> };

const states: Record<Session["state"], string> = {
  saved: "已保存会话", missing: "待导入会话", expired: "会话已过期", unavailable: "会话无法读取",
};
const errors: Record<string, string> = {
  origin_not_allowed: "请从服务器配置的 HTTPS 工作台地址打开本页。",
  invalid_operator_token: "管理令牌无效，请核对后重试。",
  invalid_session: "会话文件格式或平台不匹配，请用所选来源重新导出。",
  expired_session: "文件内的会话已过期，请重新登录后导出。",
  session_too_large: "会话文件超过 256 KB，请仅导出所选平台的 Cookie。",
  session_required: "平台未接受会话，请在官方页面重新登录后导出。",
  upstream_rejected: "平台拒绝了服务器请求，请核对官方登录会话与服务器网络。",
  city_unverified: "返回结果未通过无锡身份校验，请在官方同城页选中无锡并重新导出。",
  verification_incomplete: "来源未返回足够的有效数据，请核对所选来源和官方页面后重试。",
  collection_busy: "此来源正在采集，请稍后重试。",
  session_storage_failed: "会话未写入，请检查服务器会话目录权限与密钥。",
  import_not_configured: "服务器尚未配置会话加密密钥和管理令牌。",
  network_error: "服务器连接平台失败，请检查服务器网络后重试。",
  verification_unavailable: "本次验证结果未能确认，请读取会话和来源状态。",
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
  const [selected, setSelected] = useState("");
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ text: string; ok: boolean } | null>(null);
  const load = useCallback(async (signal?: AbortSignal) => {
    setLoading(true);
    try {
      const response = await fetch("/api/platform-sessions", { cache: "no-store", signal });
      if (!response.ok) throw new Error("Unavailable");
      setStatus(await response.json());
      setStatusError("");
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

  async function submit(event: FormEvent<HTMLFormElement>, source: string) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    const file = fields.get("session");
    const token = String(fields.get("operatorToken") ?? "").trim();
    if (!(file instanceof File) || !file.size || file.size > 256 * 1024) {
      setFeedback({ text: file instanceof File && file.size ? errors.session_too_large : errors.invalid_session, ok: false });
      return;
    }
    setBusy(true);
    setFeedback(null);
    try {
      const response = await fetch("/api/platform-sessions?source=" + source, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + token },
        body: file,
        cache: "no-store",
      });
      const result = await response.json();
      if (!response.ok) {
        setFeedback({ text: errors[result.detail] ?? "来源未通过验证，请核对平台会话和采集状态。", ok: false });
        return;
      }
      form.reset();
      setFeedback({
        text: `会话已加密保存，采集已启用。本次${result.status === "partial" ? "部分采集成功，" : ""}留存 ${result.item_count} 条。`,
        ok: true,
      });
      await load();
    } catch {
      setFeedback({ text: errors.verification_unavailable, ok: false });
    } finally {
      setBusy(false);
    }
  }

  return <main id="hotradar-app" className="hr-authorization" data-theme="light">
    <header className="hr-authorization-header">
      <a className="hr-button" href="/"><ArrowLeft />返回工作台</a>
      <button className="hr-button" disabled={loading || busy} onClick={() => void load()}><RefreshCw />读取会话状态</button>
    </header>
    <section className="hr-page-heading">
      <div><h1><KeyRound />采集授权</h1>
        <p>在官方页面登录，导入对应来源的会话。服务器验证通过并留存首批数据后启用该来源。</p>
      </div>
    </section>
    {statusError && <div className="hr-error-banner" role="alert">{statusError}</div>}
    {status && !status.import_available && <p className="hr-authorization-notice" role="status">{errors.import_not_configured}</p>}
    {groups.map(group => <section className="hr-authorization-group" key={group.platform}>
      <h2>{group.platform}</h2>
      {group.sources.map(source => {
        const session = status?.sources[source.id];
        const open = selected === source.id;
        return <article className="hr-source-card hr-authorization-card" key={source.id}>
          <div className="hr-section-row"><h3>{source.name}</h3>
            {session && <span className={"hr-tag " + (session.state === "saved" ? "is-green" : "is-orange")}>{source.id === "douyin-hot" && session.state === "missing" ? "匿名采集" : states[session.state]}</span>}
          </div>
          <p>{source.note}</p>
          {session?.saved_at && <small>保存于 {savedTime(session.saved_at)}（北京时间）</small>}
          <div className="hr-authorization-actions">
            {session && <a className="hr-button" href={session.login_url} target="_blank" rel="noopener noreferrer"><ExternalLink />打开官方登录页</a>}
            <button className="hr-button hr-button-primary" aria-expanded={open} disabled={busy || !status?.import_available} onClick={() => {
              setSelected(open ? "" : source.id); setFeedback(null);
            }}>{open ? "收起" : session?.state === "saved" ? "更新会话" : "导入会话"}</button>
          </div>
          {open && <form className="hr-authorization-form" onSubmit={event => void submit(event, source.id)} autoComplete="off">
            <details><summary>如何导出会话文件</summary>
              <p>在本机 HotRadar 项目目录运行下方命令，完成弹出浏览器里的官方登录，按终端提示保存后选择生成的 JSON 文件。</p>
              <code>uv run --locked --extra session-export python scripts/export-platform-session.py {source.id}</code>
            </details>
            <label>会话文件<input type="file" name="session" accept=".json,application/json" required disabled={busy} /></label>
            <label>管理令牌<input type="password" name="operatorToken" autoComplete="off" required disabled={busy} /></label>
            <button type="submit" className="hr-button hr-button-primary" disabled={busy}>{busy ? "正在验证并采集…" : "验证并启用采集"}</button>
            {feedback && <div className={feedback.ok ? "hr-success-banner" : "hr-error-banner"} role={feedback.ok ? "status" : "alert"}>{feedback.text}</div>}
          </form>}
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
  </main>;
}
