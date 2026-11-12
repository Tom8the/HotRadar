"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type PointerEvent } from "react";

type Lease = { access_key: string; width: number; height: number; has_city_page: boolean };
type VerifyResult = { status: string; item_count: number };

const messages: Record<string, string> = {
  origin_not_allowed: "请从服务器配置的 HTTPS 工作台地址打开本页。",
  invalid_operator_token: "管理令牌无效，请核对后重试。",
  import_not_configured: "服务器尚未配置会话加密密钥和管理令牌。",
  browser_unavailable: "服务器无法打开浏览器，请检查 Chromium 安装和平台网络。",
  browser_busy: "当前来源已有授权窗口，或服务器的授权窗口已满。请关闭已有窗口后重试。",
  browser_expired: "本次扫码窗口已过期，请关闭后重新打开。",
  browser_closed: "平台页面已关闭，请重新打开扫码窗口。",
  browser_interaction_failed: "平台页面暂时无法操作。请等待页面加载，或重新打开登录页。",
  login_pending: "平台尚未确认登录，请扫码并完成平台要求的验证。",
  session_required: "平台未接受当前会话，请在弹窗内确认登录状态。",
  expired_session: "会话已过期，请重新扫码。",
  upstream_rejected: "平台拒绝了服务器采集请求，请核对登录状态和服务器网络。",
  city_unverified: "返回结果未通过无锡身份校验，请在同城页选择无锡后重试。",
  verification_incomplete: "来源未返回足够的有效数据，请确认登录和来源设置后重试。",
  collection_busy: "此来源正在采集，请稍后重试。",
  session_storage_failed: "会话未写入，请检查服务器会话目录权限与密钥。",
  network_error: "服务器连接平台失败，请稍后重试。",
  verification_unavailable: "本次操作结果未能确认，请检查网络后重试。",
};

class LoginRequestError extends Error {
  constructor(readonly code: string) {
    super(messages[code] ?? messages.verification_unavailable);
  }
}

async function request(source: string, action: string, key: string, body = {}, signal?: AbortSignal) {
  const response = await fetch(`/api/platform-sessions?source=${source}&action=${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + key },
    body: JSON.stringify(body),
    cache: "no-store",
    keepalive: action === "close",
    signal,
  });
  if (!response.ok) {
    const { detail } = await response.json();
    throw new LoginRequestError(detail);
  }
  return response;
}

const editKeys = new Set([
  "Enter", "Backspace", "Delete", "ArrowLeft", "ArrowRight", "ArrowUp",
  "ArrowDown", "Home", "End",
]);

export default function BrowserLoginDialog({ source, name, note, onClose, onVerified }: {
  source: string; name: string; note: string; onClose: () => void; onVerified: () => void;
}) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const lease = useRef<Lease | null>(null);
  const disposed = useRef(false);
  const queue = useRef(Promise.resolve());
  const pointer = useRef<number | null>(null);
  const lastMove = useRef(0);
  const [active, setActive] = useState<Lease | null>(null);
  const [frame, setFrame] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [frameError, setFrameError] = useState("");
  const [result, setResult] = useState<VerifyResult | null>(null);

  useEffect(() => {
    disposed.current = false;
    const element = dialog.current;
    element?.showModal();
    function release() {
      if (!lease.current) return;
      void request(source, "close", lease.current.access_key).catch(() => {});
      lease.current = null;
    }
    window.addEventListener("pagehide", release);
    return () => {
      window.removeEventListener("pagehide", release);
      disposed.current = true;
      element?.close();
      release();
    };
  }, [source]);

  useEffect(() => {
    if (!active || busy || result) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll(current: Lease) {
      try {
        const response = await request(source, "frame", current.access_key, {}, controller.signal);
        const picture = await response.blob();
        if (controller.signal.aborted) return;
        setFrame(URL.createObjectURL(picture));
        setFrameError("");
      } catch (failure) {
        if (controller.signal.aborted) return;
        const message = failure instanceof Error ? failure.message : messages.browser_interaction_failed;
        if (failure instanceof LoginRequestError && ["browser_expired", "browser_closed"].includes(failure.code)) {
          void request(source, "close", current.access_key).catch(() => {});
          lease.current = null;
          setFrame("");
          setFrameError("");
          setActive(null);
          setError(message);
          return;
        }
        setFrameError(message);
      }
      timer = setTimeout(() => void poll(current), 500);
    }
    void poll(active);
    return () => { controller.abort(); clearTimeout(timer); };
  }, [active, busy, result, source]);

  useEffect(() => () => { if (frame) URL.revokeObjectURL(frame); }, [frame]);

  async function open(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const token = String(new FormData(form).get("operatorToken") ?? "").trim();
    if (!token) { setError(messages.invalid_operator_token); return; }
    setBusy("open");
    setError("");
    try {
      const response = await request(source, "open", token);
      const current: Lease = await response.json();
      if (disposed.current) {
        void request(source, "close", current.access_key).catch(() => {});
        return;
      }
      lease.current = current;
      form.reset();
      setActive(current);
    } catch (failure) {
      if (!disposed.current) setError(failure instanceof Error ? failure.message : messages.browser_unavailable);
    } finally {
      if (!disposed.current) setBusy("");
    }
  }

  function send(body: object) {
    const current = lease.current;
    if (!current || busy) return;
    queue.current = queue.current.then(async () => {
      if (disposed.current) return;
      await request(source, "input", current.access_key, body);
    }).catch(failure => {
      if (!disposed.current) setError(failure instanceof Error ? failure.message : messages.browser_interaction_failed);
    });
  }

  async function run(action: "login" | "city" | "verify") {
    const current = lease.current;
    if (!current || busy) return;
    setBusy(action);
    setError("");
    try {
      await queue.current;
      if (disposed.current) return;
      const response = await request(source, action, current.access_key);
      if (action === "verify") {
        const verified: VerifyResult = await response.json();
        lease.current = null;
        if (!disposed.current) {
          setResult(verified);
          setFrame("");
          onVerified();
        }
      } else if (!disposed.current) {
        setFrame("");
      }
    } catch (failure) {
      if (!disposed.current) setError(failure instanceof Error ? failure.message : messages.verification_unavailable);
    } finally {
      if (!disposed.current) setBusy("");
    }
  }

  function sendPointer(event: PointerEvent<HTMLImageElement>, phase: "down" | "move" | "up") {
    if (!active || busy) return;
    const target = event.currentTarget;
    if (phase === "down") {
      if (pointer.current !== null || event.button !== 0) return;
      event.preventDefault();
      target.focus();
      target.setPointerCapture(event.pointerId);
      pointer.current = event.pointerId;
      lastMove.current = 0;
    } else {
      if (pointer.current !== event.pointerId) return;
      if (phase === "move" && performance.now() - lastMove.current < 90) return;
      if (phase === "up") pointer.current = null;
    }
    lastMove.current = performance.now();
    const box = target.getBoundingClientRect();
    const x = Math.max(0, Math.min(active.width - 1, Math.floor((event.clientX - box.left) * active.width / box.width)));
    const y = Math.max(0, Math.min(active.height - 1, Math.floor((event.clientY - box.top) * active.height / box.height)));
    send({ kind: "pointer", phase, x, y });
  }

  function sendText(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const text = String(new FormData(form).get("code") ?? "");
    if (text) send({ kind: "text", text });
    form.reset();
  }

  return <dialog ref={dialog} className="hr-login-dialog" aria-labelledby={id} onCancel={event => {
    event.preventDefault();
    onClose();
  }}>
    <header className="hr-login-header">
      <h2 id={id}>{name} · 扫码授权</h2>
      <button className="hr-button" onClick={onClose}>{result ? "完成" : "关闭"}</button>
    </header>
    {error && <p className="hr-error-banner" role="alert">{error}</p>}
    {result ? <p className="hr-success-banner" role="status">
      会话已加密保存，采集已启用。本次{result.status === "partial" ? "部分采集成功，" : ""}留存 {result.item_count} 条。
    </p> : !active ? <form className="hr-authorization-form" onSubmit={event => void open(event)} autoComplete="off">
      <p>{note}</p>
      <label>管理令牌<input type="password" name="operatorToken" autoComplete="off" required disabled={!!busy} /></label>
      <button type="submit" className="hr-button hr-button-primary" disabled={!!busy}>{busy ? "正在打开平台页面…" : "打开扫码窗口"}</button>
    </form> : <>
      <p>在下方平台页面扫码。需要验证码时，可点击输入框后键入，或按住滑块拖动。</p>
      <div className="hr-login-controls">
        <button className="hr-button" disabled={!!busy} onClick={() => void run("login")}>重新打开登录页</button>
        {active.has_city_page && <button className="hr-button" disabled={!!busy} onClick={() => void run("city")}>打开同城页</button>}
        <button className="hr-button" disabled={!!busy || !frame} onClick={() => send({ kind: "scroll", delta: -420 })}>向上滚动</button>
        <button className="hr-button" disabled={!!busy || !frame} onClick={() => send({ kind: "scroll", delta: 420 })}>向下滚动</button>
      </div>
      {frameError && <p className="hr-error-banner" role="status">{frameError}</p>}
      <div className="hr-login-preview" style={{ aspectRatio: active.width / active.height }}>
        {frame ? (
          // The platform frame is a short-lived local blob supplied by the authorization session.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={frame} alt="可操作的平台登录页面" tabIndex={0} draggable={false}
            onPointerDown={event => sendPointer(event, "down")}
            onPointerMove={event => sendPointer(event, "move")}
            onPointerUp={event => sendPointer(event, "up")}
            onPointerCancel={event => sendPointer(event, "up")}
            onKeyDown={event => {
              if (event.nativeEvent.isComposing || event.altKey) return;
              const key = event.key;
              if (key.toLowerCase() === "a" && (event.ctrlKey || event.metaKey)) {
                event.preventDefault();
                send({ kind: "key", key: "ControlOrMeta+A" });
              } else if (!event.ctrlKey && !event.metaKey && (editKeys.has(key) || key.length === 1)) {
                event.preventDefault();
                send(editKeys.has(key) ? { kind: "key", key } : { kind: "text", text: key });
              }
            }}
          />
        ) : <p role="status">正在读取平台页面…</p>}
      </div>
      <div className="hr-login-controls">
        <form onSubmit={sendText} className="hr-login-code" autoComplete="off">
          <input name="code" type="text" aria-label="验证码" placeholder="点击页面输入框后填写验证码" maxLength={128} disabled={!!busy || !frame} required />
          <button className="hr-button" type="submit" disabled={!!busy || !frame}>输入到平台页面</button>
        </form>
        <button className="hr-button hr-button-primary" disabled={!!busy || !frame} onClick={() => void run("verify")}>
          {busy === "verify" ? "正在验证并采集…" : "已登录，验证并启用"}
        </button>
      </div>
    </>}
  </dialog>;
}
