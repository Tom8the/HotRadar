"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type MouseEvent, type PointerEvent } from "react";

type Lease = { access_key: string; width: number; height: number; has_city_page: boolean };
type SavedSession = { state: "saved"; saved_at: string };
type VerifyResult = SavedSession & {
  status: string; item_count: number; content_item_count?: number; city_item_count?: number;
};
type Point = { x: number; y: number };
type Gesture = {
  id: number; clientX: number; clientY: number;
  start: Point; last: Point; dragging: boolean; lastMove: number;
};

const messages: Record<string, string> = {
  origin_not_allowed: "请从服务器配置的 HTTPS 工作台地址打开本页。",
  storage_not_configured: "服务器尚未配置会话加密密钥。",
  invalid_browser_key: "扫码窗口已失效，请重新打开。",
  browser_unavailable: "服务器无法打开浏览器，请检查 Chromium 安装和平台网络。",
  browser_busy: "当前来源已有授权窗口，或服务器的授权窗口已满。请关闭已有窗口后重试。",
  browser_expired: "本次扫码窗口已过期，请关闭后重新打开。",
  browser_closed: "平台页面已关闭，请重新打开扫码窗口。",
  browser_interaction_failed: "平台页面暂时无法操作。请等待页面加载，或重新打开登录页。",
  text_target_missing: "请先点击平台页面中的验证码输入框，再填写验证码。",
  text_input_rejected: "验证码没有写入平台输入框，或验证按钮仍不可用。请点击输入框后再试。",
  login_pending: "平台尚未确认登录，请扫码并完成平台要求的验证。",
  session_required: "平台未接受当前会话，请在弹窗内确认登录状态。",
  session_unavailable: "检测到平台登录，但无法读取可用于采集的会话。请重新打开授权窗口后再试。",
  collector_session_rejected: "已读取抖音登录会话，但抖音采集接口仍拒绝当前服务器的请求。",
  source_page_unavailable: "已读取抖音登录会话，但重新加载来源页面失败。请检查平台页面是否能打开，再重试验证。",
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

async function request(source: string, action: string, key?: string, body?: object, signal?: AbortSignal) {
  const headers = new Headers();
  if (key) headers.set("Authorization", "Bearer " + key);
  if (body) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/platform-sessions?source=${source}&action=${action}`, {
    method: "POST",
    headers,
    body: body ? JSON.stringify(body) : undefined,
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
  source: string; name: string; note: string; onClose: () => void;
  onVerified: (session: SavedSession, itemCount: number, summary?: string) => void;
}) {
  const id = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const lease = useRef<Lease | null>(null);
  const disposed = useRef(false);
  const pendingOpen = useRef<Promise<Lease> | null>(null);
  const queue = useRef(Promise.resolve());
  const pointer = useRef<Gesture | null>(null);
  const handledClick = useRef(false);
  const [active, setActive] = useState<Lease | null>(null);
  const [frame, setFrame] = useState("");
  const [busy, setBusy] = useState("open");
  const [error, setError] = useState("");
  const [frameError, setFrameError] = useState("");
  const [result, setResult] = useState<VerifyResult | null>(null);

  useEffect(() => {
    disposed.current = false;
    const element = dialog.current;
    element?.showModal();
    function release() {
      disposed.current = true;
      const current = lease.current;
      lease.current = null;
      if (current) void request(source, "close", current.access_key).catch(() => {});
    }
    void open();
    window.addEventListener("pagehide", release);
    return () => {
      window.removeEventListener("pagehide", release);
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
        const response = await request(source, "frame", current.access_key, undefined, controller.signal);
        const picture = await response.blob();
        if (controller.signal.aborted || lease.current !== current) return;
        setFrame(URL.createObjectURL(picture));
        setFrameError("");
      } catch (failure) {
        if (controller.signal.aborted || lease.current !== current) return;
        const message = failure instanceof Error ? failure.message : messages.browser_interaction_failed;
        if (failure instanceof LoginRequestError && ["browser_expired", "browser_closed", "invalid_browser_key"].includes(failure.code)) {
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

  async function open() {
    if (pendingOpen.current) return;
    setBusy("open");
    setError("");
    const pending = request(source, "open").then(response => response.json() as Promise<Lease>);
    pendingOpen.current = pending;
    try {
      const current = await pending;
      if (disposed.current) {
        void request(source, "close", current.access_key).catch(() => {});
        return;
      }
      lease.current = current;
      setActive(current);
    } catch (failure) {
      if (!disposed.current) setError(failure instanceof Error ? failure.message : messages.browser_unavailable);
    } finally {
      pendingOpen.current = null;
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
          setFrameError("");
          setError("");
          const summary = verified.content_item_count != null && verified.city_item_count != null
            ? `微博授权成功。热门内容留存 ${verified.content_item_count} 条，无锡同城留存 ${verified.city_item_count} 条。两个来源已启用。`
            : undefined;
          onVerified({ state: verified.state, saved_at: verified.saved_at }, verified.item_count, summary);
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

  function coordinates(event: MouseEvent<HTMLDivElement>) {
    if (!active || !frame || busy) return null;
    const image = event.currentTarget.querySelector("img");
    const box = (image ?? event.currentTarget).getBoundingClientRect();
    if (!box.width || !box.height) return null;
    if (image && (event.clientX < box.left || event.clientY < box.top || event.clientX > box.right || event.clientY > box.bottom)) return null;
    return {
      x: Math.max(0, Math.min(active.width - 1, Math.floor((event.clientX - box.left) * active.width / box.width))),
      y: Math.max(0, Math.min(active.height - 1, Math.floor((event.clientY - box.top) * active.height / box.height))),
    };
  }

  function startPointer(event: PointerEvent<HTMLDivElement>) {
    if (pointer.current || event.button !== 0) return;
    const point = coordinates(event);
    if (!point) return;
    event.currentTarget.focus({ preventScroll: true });
    try { event.currentTarget.setPointerCapture(event.pointerId); } catch { /* The pointer may already be inactive. */ }
    handledClick.current = false;
    pointer.current = {
      id: event.pointerId, clientX: event.clientX, clientY: event.clientY,
      start: point, last: point, dragging: false, lastMove: 0,
    };
  }

  function movePointer(event: PointerEvent<HTMLDivElement>) {
    const gesture = pointer.current;
    if (!gesture || gesture.id !== event.pointerId) return;
    const point = coordinates(event);
    if (!point) return;
    gesture.last = point;
    if (!gesture.dragging) {
      if (Math.hypot(event.clientX - gesture.clientX, event.clientY - gesture.clientY) < 5) return;
      gesture.dragging = true;
      event.preventDefault();
      send({ kind: "pointer", phase: "down", ...gesture.start });
    }
    const now = performance.now();
    if (gesture.lastMove && now - gesture.lastMove < 90) return;
    gesture.lastMove = now;
    send({ kind: "pointer", phase: "move", ...point });
  }

  function finishPointer(event: PointerEvent<HTMLDivElement>, cancelled = false) {
    const gesture = pointer.current;
    if (!gesture || gesture.id !== event.pointerId) return;
    pointer.current = null;
    if (gesture.dragging) {
      handledClick.current = true;
      const point = cancelled ? gesture.last : coordinates(event) ?? gesture.last;
      send({ kind: "pointer", phase: "up", ...point });
    } else if (!cancelled) {
      handledClick.current = true;
      send({ kind: "pointer", phase: "click", ...gesture.start });
    }
  }

  function clickPreview(event: MouseEvent<HTMLDivElement>) {
    if (handledClick.current) {
      handledClick.current = false;
      return;
    }
    const point = coordinates(event);
    if (!point) return;
    event.currentTarget.focus({ preventScroll: true });
    send({ kind: "pointer", phase: "click", ...point });
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
    {error && !active && <p className="hr-error-banner" role="alert">{error}</p>}
    {result ? <p className="hr-success-banner" role="status">
      {result.content_item_count != null && result.city_item_count != null
        ? <>授权成功。热门内容留存 {result.content_item_count} 条，无锡同城留存 {result.city_item_count} 条。两个来源已启用。</>
        : <>授权成功。会话已加密保存，采集已启用。本次{result.status === "partial" ? "部分采集成功，" : ""}留存 {result.item_count} 条。</>}
    </p> : !active ? <div>
      <p>{note}</p>
      {busy ? <p role="status">正在打开平台登录页…</p> :
        <button type="button" className="hr-button hr-button-primary" onClick={() => void open()}>重试打开登录页</button>}
    </div> : <>
      <p>在下方平台页面扫码，可直接点击页面中的按钮、链接和验证方式。需要验证码时，点击输入框后键入，或按住滑块拖动。</p>
      <div className="hr-login-controls">
        <button className="hr-button" disabled={!!busy} onClick={() => void run("login")}>重新打开登录页</button>
        {active.has_city_page && <button className="hr-button" disabled={!!busy} onClick={() => void run("city")}>打开同城页</button>}
        <button className="hr-button" disabled={!!busy || !frame} onClick={() => send({ kind: "scroll", delta: -420 })}>向上滚动</button>
        <button className="hr-button" disabled={!!busy || !frame} onClick={() => send({ kind: "scroll", delta: 420 })}>向下滚动</button>
      </div>
      {frameError && <p className="hr-error-banner" role="status">{frameError}</p>}
      <div className="hr-login-preview" style={{ aspectRatio: active.width / active.height }}
        role="group" aria-label="平台页面交互区" tabIndex={frame ? 0 : -1}
        onPointerDown={startPointer} onPointerMove={movePointer}
        onPointerUp={event => finishPointer(event)}
        onPointerCancel={event => finishPointer(event, true)}
        onLostPointerCapture={event => finishPointer(event, true)}
        onPointerLeave={event => {
          if (!event.currentTarget.hasPointerCapture(event.pointerId)) finishPointer(event, true);
        }}
        onClick={clickPreview}
        onPaste={event => {
          const text = event.clipboardData.getData("text").slice(0, 128);
          if (!text) return;
          event.preventDefault();
          send({ kind: "text", text });
        }}
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
      >
        {frame ? (
          // The platform frame is a short-lived local blob supplied by the authorization session.
          // eslint-disable-next-line @next/next/no-img-element
          <img src={frame} alt="可操作的平台登录页面" draggable={false} />
        ) : <p role="status">正在读取平台页面…</p>}
      </div>
      {error && <p className="hr-error-banner" role="alert">{error}</p>}
      <div className="hr-login-controls">
        <form onSubmit={sendText} className="hr-login-code" autoComplete="off">
          <input name="code" type="text" aria-label="验证码" placeholder="点击页面输入框后填写验证码" maxLength={128} disabled={!!busy || !frame} required />
          <button className="hr-button" type="submit" disabled={!!busy || !frame}>输入到平台页面</button>
        </form>
        <button className="hr-button hr-button-primary" disabled={!!busy || !frame} onClick={() => void run("verify")}>
          {busy === "verify" ? "正在检查会话并试采…" : "已登录，验证并启用"}
        </button>
      </div>
    </>}
  </dialog>;
}
