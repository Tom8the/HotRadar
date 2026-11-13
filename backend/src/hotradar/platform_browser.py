"""Short-lived, operator-controlled browsers for official platform sign-in."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from secrets import token_urlsafe
from time import monotonic
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from hotradar.session_store import SCOPES, allowed_domain, validate_cookies

if TYPE_CHECKING:
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
        Playwright,
        Route,
        WebSocketRoute,
    )

WIDTH, HEIGHT = 1280, 800
LOGIN_TTL = 600
MAX_BROWSERS = 2
WEB_HOSTS = {
    "douyin": (
        "douyin.com",
        "iesdouyin.com",
        "verify.snssdk.com",
        "verify.zijieapi.com",
        "yhgfb-cn-static.com",
    ),
    "weibo": ("weibo.com", "weibo.cn", "sina.com.cn", "sina.cn", "geetest.com"),
}
ASSET_HOSTS = {
    "douyin": (
        "amemv.com",
        "applogcdn.com",
        "byted-static.com",
        "bytedance.com",
        "bytednsdoc.com",
        "bytegoofy.com",
        "byteimg.com",
        "bytescm.com",
        "bytetcc.com",
        "bytetos.com",
        "douyincdn.com",
        "douyinpic.com",
        "douyinstatic.com",
        "douyinvod.com",
        "ibytedapm.com",
        "pstatp.com",
        "snssdk.com",
        "toutiao.com",
        "zijieapi.com",
    ),
    "weibo": ("sinaimg.cn", "sinajs.cn", "weibocdn.com", "weibocdn.cn"),
}
LOGIN_BUTTONS = {
    "douyin-hot": "登录",
    "douyin-content": "登录",
    "weibo": "登录/注册",
    "weibo-wuxi": "登录/注册",
    "weibo-content": "登录/注册",
}
DOUYIN_LOGIN_COOKIES = {"sessionid", "sessionid_ss"}
WEIBO_MOBILE_CONFIG = "https://m.weibo.cn/api/config"
ENLARGE_WEIBO_QR = """
() => {
    const image = document.querySelector('img[src^="https://v2.qr.weibo.cn/"]');
    if (!image || image.style.transform === "scale(2)") return;
    image.style.transform = "scale(2)";
    image.style.position = "relative";
    image.style.zIndex = "1";
    image.style.imageRendering = "pixelated";
    image.parentElement.style.marginBlock = "55px";
    image.parentElement.style.overflow = "visible";
}
"""
# Douyin's creator document is cross-origin isolated and does not deliver Playwright's
# trusted mouse events to the page, so clicks on identity verification do nothing.
# The marker records events that actually land; a miss falls back to DOM activation.
# Classic script, not a function: Playwright evaluates init scripts as programs.
TRUSTED_INPUT_OBSERVER = """
if (window.__hotradarTrustedCount === undefined) window.__hotradarTrustedCount = 0;
addEventListener("pointerdown", (event) => {
    if (event.isTrusted) window.__hotradarTrustedCount += 1;
}, true);
addEventListener("mousedown", (event) => {
    if (event.isTrusted) window.__hotradarTrustedCount += 1;
}, true);
addEventListener("click", (event) => {
    if (event.isTrusted) window.__hotradarTrustedCount += 1;
}, true);
"""
HIT_TEST = """
([x, y]) => {
    let el = document.elementFromPoint(x, y);
    while (el && el.shadowRoot) {
        const next = el.shadowRoot.elementFromPoint(x, y);
        if (!next || next === el) break;
        el = next;
    }
    return el;
}
"""
IFRAME_ORIGIN = """
(el) => {
    if (!el || el.tagName !== "IFRAME") return null;
    const rect = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    return {
        dx: rect.left + (parseFloat(style.borderLeftWidth) || 0),
        dy: rect.top + (parseFloat(style.borderTopWidth) || 0),
    };
}
"""
DOM_POINTER = """
(el, point) => {
    const x = point.x;
    const y = point.y;
    const phase = point.phase;
    const base = {
        bubbles: true, cancelable: true, composed: true, view: window,
        clientX: x, clientY: y, screenX: x, screenY: y, button: 0,
    };
    const pointer = (type, buttons) => el.dispatchEvent(new PointerEvent(type, {
        ...base, buttons, pointerId: 1, pointerType: "mouse", isPrimary: true,
        width: 1, height: 1, pressure: buttons ? 0.5 : 0,
    }));
    const mouse = (type, buttons) => el.dispatchEvent(new MouseEvent(type, {...base, buttons}));
    if (phase === "down" || phase === "click") {
        try { el.focus({preventScroll: true}); } catch (error) { /* not focusable */ }
        pointer("pointerover", 0);
        mouse("mouseover", 0);
        pointer("pointerdown", 1);
        mouse("mousedown", 1);
    }
    if (phase === "move") {
        pointer("pointermove", 1);
        mouse("mousemove", 1);
    }
    if (phase === "up" || phase === "click") {
        pointer("pointerup", 0);
        mouse("mouseup", 0);
    }
    if (phase === "click" && typeof el.click === "function") el.click();
}
"""
FIND_EDITABLE = """
([x, y]) => {
    const blocked = new Set([
        "button", "submit", "reset", "checkbox", "radio",
        "file", "hidden", "image", "range", "color",
    ]);
    function isEditable(node) {
        if (!node || node.nodeType !== 1) return false;
        if (node.tagName === "TEXTAREA") return !node.disabled && !node.readOnly;
        if (node.tagName === "INPUT") {
            const type = (node.type || "text").toLowerCase();
            return !blocked.has(type) && !node.disabled && !node.readOnly;
        }
        return node.isContentEditable === true;
    }
    const stack = document.elementsFromPoint(x, y);
    for (const node of stack) {
        if (isEditable(node)) return node;
    }
    const top = stack[0];
    if (!top || top.tagName === "IFRAME") return null;
    const label = top.closest && top.closest("label");
    if (label) {
        const control = label.control || label.querySelector("input, textarea");
        if (isEditable(control)) return control;
    }
    let host = top;
    for (let depth = 0; host && depth < 4; depth += 1) {
        const rect = host.getBoundingClientRect();
        if (rect.width > 0 && rect.width <= 640 && rect.height > 0 && rect.height <= 160) {
            const selector = "input, textarea, [contenteditable='true']";
            const inner = [...host.querySelectorAll(selector)].find(isEditable);
            if (inner) return inner;
        }
        host = host.parentElement;
    }
    return null;
}
"""
READ_VALUE = """
(el) => el.isContentEditable ? (el.innerText || "") : String(el.value ?? "")
"""
FOCUS_FIELD = """
(el) => {
    try { el.focus({preventScroll: true}); } catch (error) { /* not focusable */ }
    if (typeof el.click === "function") el.click();
}
"""
WRITE_VALUE = """
(el, text) => {
    function inserted(next) {
        const selector = "button, [role='button'], div, a, span";
        const button = [...el.ownerDocument.querySelectorAll(selector)].find(node => {
            if (node.contains(el)) return false;
            return (node.innerText || "").trim() === "验证";
        });
        let verifyEnabled = null;
        if (button) {
            let disabled = false;
            let current = button;
            for (let depth = 0; current && depth < 4; depth += 1) {
                const marked = current.getAttribute("aria-disabled") === "true";
                if (current.disabled || marked) disabled = true;
                current = current.parentElement;
            }
            verifyEnabled = !disabled;
        }
        return {value: next, verifyEnabled};
    }
    try { el.focus({preventScroll: true}); } catch (error) { /* not focusable */ }
    if (el.isContentEditable) {
        const next = (el.innerText || "") + text;
        el.textContent = next;
        const inputEvent = new InputEvent("input", {
            bubbles: true, composed: true, data: text, inputType: "insertText",
        });
        el.dispatchEvent(inputEvent);
        el.dispatchEvent(new Event("change", {bubbles: true, composed: true}));
        return inserted(el.innerText || "");
    }
    const value = String(el.value ?? "");
    const start = typeof el.selectionStart === "number" ? el.selectionStart : value.length;
    const end = typeof el.selectionEnd === "number" ? el.selectionEnd : start;
    const current = value;
    const next = current.slice(0, start) + text + current.slice(end);
    const prototype = el.tagName === "TEXTAREA"
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, "value").set;
    setter.call(el, next);
    const tracker = el._valueTracker;
    if (tracker) tracker.setValue(current);
    const caret = start + text.length;
    try { el.setSelectionRange(caret, caret); } catch (error) { /* unsupported */ }
    const last = text.slice(-1);
    el.dispatchEvent(new KeyboardEvent("keydown", {
        bubbles: true, composed: true, key: last, cancelable: true,
    }));
    el.dispatchEvent(new InputEvent("beforeinput", {
        bubbles: true, composed: true, cancelable: true,
        data: text, inputType: "insertText",
    }));
    el.dispatchEvent(new InputEvent("input", {
        bubbles: true, composed: true, data: text, inputType: "insertText",
    }));
    el.dispatchEvent(new KeyboardEvent("keyup", {
        bubbles: true, composed: true, key: last,
    }));
    el.dispatchEvent(new CompositionEvent("compositionend", {
        bubbles: true, composed: true, data: text,
    }));
    el.dispatchEvent(new Event("change", {bubbles: true, composed: true}));
    return inserted(String(el.value ?? ""));
}
"""
EDIT_VALUE = """
(el, key) => {
    try { el.focus({preventScroll: true}); } catch (error) { /* not focusable */ }
    if (el.isContentEditable) return el.innerText || "";
    const value = String(el.value ?? "");
    let start = typeof el.selectionStart === "number" ? el.selectionStart : value.length;
    let end = typeof el.selectionEnd === "number" ? el.selectionEnd : start;
    if (key === "ControlOrMeta+A") {
        try { el.setSelectionRange(0, value.length); } catch (error) { /* unsupported */ }
        return value;
    }
    if (start === end && key === "Backspace" && start > 0) start -= 1;
    else if (start === end && key === "Delete" && end < value.length) end += 1;
    else if (start === end) return value;
    const next = value.slice(0, start) + value.slice(end);
    const prototype = el.tagName === "TEXTAREA"
        ? HTMLTextAreaElement.prototype
        : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value").set.call(el, next);
    const tracker = el._valueTracker;
    if (tracker) tracker.setValue(value);
    try { el.setSelectionRange(start, start); } catch (error) { /* unsupported */ }
    el.dispatchEvent(new InputEvent("input", {
        bubbles: true, composed: true, inputType: "deleteContentBackward",
    }));
    el.dispatchEvent(new Event("change", {bubbles: true, composed: true}));
    return String(el.value ?? "");
}
"""
VERIFY_BUTTON = """
(el) => {
    const selector = "button, [role='button'], div, a, span";
    const button = [...el.ownerDocument.querySelectorAll(selector)].find(node => {
        if (node.contains(el)) return false;
        return (node.innerText || "").trim() === "验证";
    });
    if (!button) return null;
    let current = button;
    for (let depth = 0; current && depth < 4; depth += 1) {
        if (current.disabled || current.getAttribute("aria-disabled") === "true") return false;
        current = current.parentElement;
    }
    return true;
}
"""


def browser_platform(scope: str) -> str:
    if scope == "weibo":
        return "weibo"
    return SCOPES[scope]


def trusted_input_landed(before, after) -> bool:
    if before is None or after is None or len(before) != len(after):
        return False
    return any(
        isinstance(left, (int, float)) and isinstance(right, (int, float)) and right > left
        for left, right in zip(before, after, strict=True)
    )


def text_was_inserted(before, after, text) -> bool:
    return isinstance(before, str) and isinstance(after, str) and after != before and text in after


def can_address_dom(page) -> bool:
    return callable(getattr(page, "evaluate_handle", None))


class BrowserLoginError(Exception):
    def __init__(self, code: str):
        self.code = code


def allowed_request(url: str, platform: str, *, document=False, websocket=False) -> bool:
    parts = urlsplit(url)
    if parts.scheme != ("wss" if websocket else "https") or not parts.hostname:
        return False
    hosts = WEB_HOSTS[platform]
    if not document:
        hosts += ASSET_HOSTS[platform]
    return any(parts.hostname == host or parts.hostname.endswith("." + host) for host in hosts)


@dataclass
class LoginBrowser:
    scope: str
    access_key: str = field(default_factory=lambda: token_urlsafe(32), repr=False)
    expires_at: float = field(default_factory=lambda: monotonic() + LOGIN_TTL)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    driver: Playwright | None = None
    browser: Browser | None = None
    context: BrowserContext | None = None
    expiry: asyncio.Task | None = None
    pointer_page: Page | None = None
    pointer_dom: bool = False
    text_target: object | None = None

    async def open(self, url: str):
        from playwright.async_api import Error, async_playwright

        platform = browser_platform(self.scope)
        self.driver = await async_playwright().start()
        configured = os.environ.get("HOTRADAR_BROWSER_CHANNEL")
        channels = ("msedge", "chrome", "chromium") if os.name == "nt" else ("chromium",)
        for channel in (configured,) if configured else channels:
            try:
                self.browser = await self.driver.chromium.launch(
                    channel=channel, headless=True, chromium_sandbox=True, timeout=15000
                )
                break
            except Error:
                continue
        if not self.browser:
            raise BrowserLoginError("browser_unavailable")
        self.context = await self.browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
            accept_downloads=False,
            service_workers="block",
        )

        async def http_request(route: Route):
            request = route.request
            if allowed_request(request.url, platform, document=request.resource_type == "document"):
                await route.continue_()
            else:
                await route.abort()

        async def socket_request(route: WebSocketRoute):
            if allowed_request(route.url, platform, websocket=True):
                route.connect_to_server()
            else:
                await route.close()

        await self.context.route("**/*", http_request)
        await self.context.route_web_socket("**/*", socket_request)
        await self.context.add_init_script(TRUSTED_INPUT_OBSERVER)
        await self.context.new_page()
        await self.open_login(url)

    def page(self) -> Page:
        if self.context:
            for page in reversed(self.context.pages):
                if not page.is_closed():
                    return page
        raise BrowserLoginError("browser_closed")

    async def viewport_page(self) -> Page:
        page = self.page()
        viewport = {"width": WIDTH, "height": HEIGHT}
        if page.viewport_size != viewport:
            await page.set_viewport_size(viewport)
        return page

    async def navigate(self, url: str):
        await self.release_pointer()
        await self._clear_text_target()
        await self.page().goto(url, wait_until="commit", timeout=15000)

    async def open_login(self, url: str):
        from playwright.async_api import TimeoutError

        await self.navigate(url)
        label = LOGIN_BUTTONS.get(self.scope)
        if not label:
            return
        page = self.page()
        before = await self._input_marks(page)
        with suppress(TimeoutError):
            await page.get_by_text(label, exact=True).first.click(timeout=6000)
        if before is None or trusted_input_landed(before, await self._input_marks(page)):
            return
        try:
            box = await page.get_by_text(label, exact=True).first.bounding_box(timeout=1000)
        except Exception:  # noqa: BLE001 -- the login control may already be absent
            return
        if not box:
            return
        await self._dom_pointer(
            page,
            "click",
            int(box["x"] + box["width"] / 2),
            int(box["y"] + box["height"] / 2),
        )

    async def frame(self):
        page = await self.viewport_page()
        on_weibo_passport = (
            browser_platform(self.scope) == "weibo"
            and urlsplit(page.url).hostname == "passport.weibo.com"
        )
        if on_weibo_passport:
            await page.evaluate(ENLARGE_WEIBO_QR)
        return await page.screenshot(type="jpeg", quality=82, timeout=3000)

    async def _input_marks(self, page):
        frames = getattr(page, "frames", None)
        if not frames and getattr(page, "evaluate", None) is None:
            return None
        marks = []
        for frame in frames or [page]:
            try:
                count = await frame.evaluate("() => window.__hotradarTrustedCount || 0")
            except Exception:  # noqa: BLE001 -- a detached frame has no input marker
                marks.append(None)
            else:
                marks.append(count)
        return marks

    async def _element_at(self, page, x, y):
        target = page
        for _ in range(8):
            handle = await target.evaluate_handle(HIT_TEST, [x, y])
            element = handle.as_element()
            if element is None:
                await handle.dispose()
                return None
            origin = await element.evaluate(IFRAME_ORIGIN)
            if not origin:
                return element, x, y
            frame = await element.content_frame()
            await element.dispose()
            if frame is None:
                return None
            x -= origin["dx"]
            y -= origin["dy"]
            target = frame
        return None

    async def _dom_pointer(self, page, phase, x, y):
        found = await self._element_at(page, x, y)
        if not found:
            return
        element, local_x, local_y = found
        try:
            await element.evaluate(DOM_POINTER, {"phase": phase, "x": local_x, "y": local_y})
        finally:
            await element.dispose()

    async def pointer(self, phase: str, x: int, y: int):
        if phase in {"click", "down"}:
            await self.release_pointer()
            page = await self.viewport_page()
            before = await self._input_marks(page)
            if phase == "click":
                await page.mouse.click(x, y)
                if before is not None and not trusted_input_landed(
                    before, await self._input_marks(page)
                ):
                    await self._dom_pointer(page, "click", x, y)
                await self._remember_text_target(page, x, y)
                return
            await page.mouse.move(x, y)
            await page.mouse.down()
            self.pointer_page = page
            self.pointer_dom = before is not None and not trusted_input_landed(
                before, await self._input_marks(page)
            )
            if self.pointer_dom:
                await self._dom_pointer(page, "down", x, y)
        elif self.pointer_page and not self.pointer_page.is_closed():
            await self.pointer_page.mouse.move(x, y)
            if phase == "up":
                if self.pointer_dom:
                    await self._dom_pointer(self.pointer_page, "up", x, y)
                await self.release_pointer()
            elif self.pointer_dom:
                await self._dom_pointer(self.pointer_page, "move", x, y)

    async def _clear_text_target(self):
        target, self.text_target = self.text_target, None
        if target is not None:
            with suppress(Exception):
                await target.dispose()

    async def _editable_at(self, page, x, y):
        target = page
        for _ in range(8):
            handle = await target.evaluate_handle(FIND_EDITABLE, [x, y])
            element = handle.as_element()
            if element is not None:
                return element
            await handle.dispose()
            hit = await target.evaluate_handle(HIT_TEST, [x, y])
            hit_element = hit.as_element()
            if hit_element is None:
                await hit.dispose()
                return None
            origin = await hit_element.evaluate(IFRAME_ORIGIN)
            if not isinstance(origin, dict):
                await hit_element.dispose()
                return None
            frame = await hit_element.content_frame()
            await hit_element.dispose()
            if frame is None:
                return None
            x -= origin["dx"]
            y -= origin["dy"]
            target = frame
        return None

    async def _remember_text_target(self, page, x, y):
        if not can_address_dom(page):
            return
        try:
            element = await self._editable_at(page, x, y)
        except Exception:  # noqa: BLE001 -- hit testing must not break the click itself
            return
        if element is None:
            return
        await self._clear_text_target()
        with suppress(Exception):
            await element.evaluate(FOCUS_FIELD)
        self.text_target = element

    async def _current_text_target(self, page):
        target = self.text_target
        if target is not None:
            try:
                connected = await target.evaluate(
                    "el => Boolean(el && el.isConnected && !el.disabled && !el.readOnly)"
                )
            except Exception:  # noqa: BLE001 -- a detached input handle is simply stale
                connected = False
            if connected:
                return target
            await self._clear_text_target()
        frames = getattr(page, "frames", None) or [page]
        for frame in frames:
            try:
                handle = await frame.evaluate_handle(
                    """() => {
                        const el = document.activeElement;
                        if (!el || el === document.body) return null;
                        const type = (el.type || "text").toLowerCase();
                        const blocked = [
                            "button", "submit", "reset", "checkbox", "radio", "file", "hidden",
                        ];
                        if (el.tagName === "INPUT" && blocked.includes(type)) return null;
                        const editable = el.tagName === "INPUT" || el.tagName === "TEXTAREA";
                        if (editable || el.isContentEditable) {
                            return el;
                        }
                        return null;
                    }"""
                )
            except Exception:  # noqa: BLE001 -- the frame may already be gone
                continue
            element = handle.as_element()
            if element is not None:
                self.text_target = element
                return element
            await handle.dispose()
        return None

    async def _verify_button_ready(self, target, text):
        if len(text) < 4:
            return
        try:
            enabled = await target.evaluate(VERIFY_BUTTON)
        except Exception:  # noqa: BLE001 -- button probing is confirmatory
            return
        if enabled is False:
            raise BrowserLoginError("text_input_rejected")

    async def insert_text(self, text: str):
        page = await self.viewport_page()
        if not can_address_dom(page):
            await page.keyboard.type(text)
            return
        target = await self._current_text_target(page)
        if target is None:
            raise BrowserLoginError("text_target_missing")
        before = str(await target.evaluate(READ_VALUE) or "")
        with suppress(Exception):
            await target.evaluate(FOCUS_FIELD)
        with suppress(Exception):
            await page.keyboard.type(text)
        after = str(await target.evaluate(READ_VALUE) or "")
        if text_was_inserted(before, after, text):
            await self._verify_button_ready(target, text)
            return
        written = await target.evaluate(WRITE_VALUE, text)
        value = written.get("value") if isinstance(written, dict) else written
        if not text_was_inserted(before, str(value or ""), text):
            raise BrowserLoginError("text_input_rejected")
        if (
            len(text) >= 4
            and isinstance(written, dict)
            and written.get("verifyEnabled") is False
        ):
            raise BrowserLoginError("text_input_rejected")

    async def press_key(self, key: str):
        page = await self.viewport_page()
        if not can_address_dom(page):
            await page.keyboard.press(key)
            return
        editing = key in {"Backspace", "Delete", "ControlOrMeta+A"}
        target = await self._current_text_target(page)
        if editing and target is None:
            raise BrowserLoginError("text_target_missing")
        before = str(await target.evaluate(READ_VALUE) or "") if target is not None else ""
        with suppress(Exception):
            if target is not None:
                await target.evaluate(FOCUS_FIELD)
            await page.keyboard.press(key)
        if target is not None and key == "ControlOrMeta+A":
            await target.evaluate(EDIT_VALUE, key)
            return
        if target is None or key not in {"Backspace", "Delete"}:
            return
        after = str(await target.evaluate(READ_VALUE) or "")
        if after == before and before:
            await target.evaluate(EDIT_VALUE, key)

    async def release_pointer(self):
        page, self.pointer_page = self.pointer_page, None
        self.pointer_dom = False
        if page and not page.is_closed():
            await page.mouse.up()

    async def cookies(self):
        if not self.context:
            raise BrowserLoginError("browser_closed")
        platform = browser_platform(self.scope)
        cookies = [
            cookie
            for cookie in await self.context.cookies()
            if allowed_domain(cookie["domain"], platform)
        ]
        names = {cookie["name"].lower() for cookie in cookies if cookie["value"]}
        if platform == "weibo":
            signed_in = self.scope in {"weibo", "weibo-content"} and "ssologinstate" in names
            if not signed_in and "sub" in names:
                from playwright.async_api import Error

                with suppress(Error, ValueError):
                    response = await self.context.request.get(
                        WEIBO_MOBILE_CONFIG, max_redirects=0, timeout=8000
                    )
                    if response.ok:
                        payload = await response.json()
                        data = payload.get("data") if isinstance(payload, dict) else None
                        signed_in = isinstance(data, dict) and data.get("login") is True
        else:
            # Other platform cookies may be partitioned or unsuitable for the worker's HTTP jar.
            # Keep eligible cookies and require a usable sign-in cookie from this same context.
            portable = []
            for cookie in cookies:
                with suppress(ValueError):
                    portable.extend(validate_cookies(platform, [cookie]))
            cookies = portable
            signed_in = bool(names & DOUYIN_LOGIN_COOKIES)
            if signed_in and not any(
                cookie["name"].lower() in DOUYIN_LOGIN_COOKIES and cookie["value"]
                for cookie in cookies
            ):
                raise BrowserLoginError("session_unavailable")
        if not signed_in:
            raise BrowserLoginError("login_pending")
        return cookies

    async def refresh_session(self, url: str):
        from playwright.async_api import Error, TimeoutError

        page = self.page()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=12000)
        except Error:
            raise BrowserLoginError("source_page_unavailable") from None
        with suppress(TimeoutError):
            await page.wait_for_load_state("load", timeout=4000)

    async def close(self):
        if self.browser:
            with suppress(Exception):
                await asyncio.wait_for(self.browser.close(), 5)
        if self.driver:
            with suppress(Exception):
                await asyncio.wait_for(self.driver.stop(), 5)


active_browsers: dict[str, LoginBrowser] = {}


async def close_browser(browser: LoginBrowser):
    async with browser.lock:
        if active_browsers.get(browser.scope) is not browser:
            return
        try:
            await browser.close()
        finally:
            active_browsers.pop(browser.scope, None)
            if browser.expiry and browser.expiry is not asyncio.current_task():
                browser.expiry.cancel()


async def expire_browser(browser: LoginBrowser):
    await asyncio.sleep(max(0, browser.expires_at - monotonic()))
    await close_browser(browser)


async def start_browser(scope: str, url: str) -> LoginBrowser:
    # There is no await between checking the registry and reserving a slot.
    if scope in active_browsers or len(active_browsers) >= MAX_BROWSERS:
        raise BrowserLoginError("browser_busy")
    browser = LoginBrowser(scope)
    active_browsers[scope] = browser
    try:
        async with browser.lock:
            await browser.open(url)
        browser.expiry = asyncio.create_task(expire_browser(browser))
        return browser
    except asyncio.CancelledError:
        await close_browser(browser)
        raise
    except Exception:
        await close_browser(browser)
        raise BrowserLoginError("browser_unavailable") from None


@asynccontextmanager
async def browser_lifespan(_app):
    try:
        yield
    finally:
        await asyncio.gather(
            *(close_browser(browser) for browser in list(active_browsers.values()))
        )
