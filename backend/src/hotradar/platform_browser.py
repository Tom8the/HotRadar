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

    async def open(self, url: str):
        from playwright.async_api import Error, async_playwright

        platform = SCOPES[self.scope]
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
        await self.page().goto(url, wait_until="commit", timeout=15000)

    async def open_login(self, url: str):
        from playwright.async_api import TimeoutError

        await self.navigate(url)
        if label := LOGIN_BUTTONS.get(self.scope):
            with suppress(TimeoutError):
                await self.page().get_by_text(label, exact=True).first.click(timeout=6000)

    async def frame(self):
        page = await self.viewport_page()
        if SCOPES[self.scope] == "weibo" and urlsplit(page.url).hostname == "passport.weibo.com":
            await page.evaluate(ENLARGE_WEIBO_QR)
        return await page.screenshot(type="jpeg", quality=82, timeout=3000)

    async def pointer(self, phase: str, x: int, y: int):
        if phase in {"click", "down"}:
            await self.release_pointer()
            page = await self.viewport_page()
            if phase == "click":
                await page.mouse.click(x, y)
                return
            await page.mouse.move(x, y)
            await page.mouse.down()
            self.pointer_page = page
        elif self.pointer_page and not self.pointer_page.is_closed():
            await self.pointer_page.mouse.move(x, y)
            if phase == "up":
                await self.release_pointer()

    async def release_pointer(self):
        page, self.pointer_page = self.pointer_page, None
        if page and not page.is_closed():
            await page.mouse.up()

    async def cookies(self):
        if not self.context:
            raise BrowserLoginError("browser_closed")
        platform = SCOPES[self.scope]
        cookies = [
            cookie
            for cookie in await self.context.cookies()
            if allowed_domain(cookie["domain"], platform)
        ]
        names = {cookie["name"].lower() for cookie in cookies if cookie["value"]}
        if platform == "weibo":
            signed_in = self.scope == "weibo-content" and "ssologinstate" in names
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
