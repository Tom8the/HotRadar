from hmac import compare_digest
from importlib.util import find_spec
from json import loads
from time import monotonic
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from starlette.requests import ClientDisconnect

from hotradar.collection import collect_board
from hotradar.collectors.douyin_city import (
    NATIONAL_BOARD,
    DouyinCreatorNationalCollector,
    DouyinWuxiCollector,
)
from hotradar.collectors.douyin_city import PAGE as CREATOR_PAGE
from hotradar.collectors.platforms import PlatformCollector
from hotradar.collectors.types import CollectionError
from hotradar.collectors.weibo_city import PAGE as WEIBO_CITY_PAGE
from hotradar.collectors.weibo_city import WeiboWuxiCollector
from hotradar.config import get_settings
from hotradar.content_collection import collect_douyin, collect_weibo, fetch_douyin, fetch_weibo
from hotradar.platform_browser import (
    HEIGHT,
    WIDTH,
    BrowserLoginError,
    active_browsers,
    close_browser,
    start_browser,
)
from hotradar.session_store import (
    SCOPES,
    cookie_jar,
    save_portable,
    session_cipher,
    session_status,
    validate_cookies,
)

router = APIRouter(prefix="/api/v1/platform-sessions")
LOGIN_PAGES = {
    "douyin-hot": "https://www.douyin.com/",
    NATIONAL_BOARD: CREATOR_PAGE,
    "douyin-wuxi": CREATOR_PAGE,
    "douyin-content": "https://www.douyin.com/jingxuan",
    "weibo-wuxi": WEIBO_CITY_PAGE,
    "weibo-content": "https://weibo.com/",
}
BROWSER_PAGES = {
    **LOGIN_PAGES,
    "weibo-wuxi": "https://weibo.com/login.php",
    "weibo-content": "https://weibo.com/login.php",
}
CITY_PAGES = {
    "douyin-content": CREATOR_PAGE,
    "weibo-content": WEIBO_CITY_PAGE,
    "weibo-wuxi": WEIBO_CITY_PAGE,
}
NO_STORE = {"Cache-Control": "no-store"}
CONTENT = {
    "douyin-content": (collect_douyin, fetch_douyin),
    "weibo-content": (collect_weibo, fetch_weibo),
}


def storage_configured():
    try:
        session_cipher()
    except RuntimeError:
        return False
    return True


def bearer_matches(request, key):
    return compare_digest(
        request.headers.get("authorization", "").encode(), ("Bearer " + key).encode()
    )


@router.get("")
def status():
    configured = storage_configured()
    return JSONResponse(
        {
            "storage_configured": configured,
            "browser_available": configured and find_spec("playwright") is not None,
            "sources": {
                scope: {"login_url": LOGIN_PAGES[scope], **session_status(scope)}
                for scope in SCOPES
            },
        },
        headers=NO_STORE,
    )


async def read_input(request):
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > 2048:
                raise HTTPException(413, "invalid_browser_input")
            body.extend(chunk)
        return loads(body)
    except (ValueError, RecursionError, ClientDisconnect):
        raise HTTPException(400, "invalid_browser_input") from None


class VerifiedBoardImport:
    def __init__(self, batch, store):
        self.batch = batch
        self.store = store

    async def collect(self):
        self.store()
        return self.batch


class SessionVerificationError(HTTPException):
    def __init__(self, code):
        super().__init__(422, code)


async def verify_session(scope, cookies):
    jar = cookie_jar(cookies)
    if not jar.jar:
        raise HTTPException(400, "expired_session")

    def store():
        try:
            save_portable(scope, cookies)
        except (OSError, RuntimeError):
            raise CollectionError("session_storage_failed", "Could not save session.") from None

    key = "browser-auth:" + uuid4().hex
    if scope in CONTENT:
        collect, fetch = CONTENT[scope]
        try:
            batch = await fetch(verify=True, cookies=jar)
        except CollectionError as exc:
            raise SessionVerificationError(exc.code) from None

        async def store_verified_batch(*, verify):
            store()
            return batch

        result = await collect(key, verify=True, activate=True, fetcher=store_verified_batch)
    else:
        timeout = get_settings().timeout
        if scope == "douyin-wuxi":
            collector = DouyinWuxiCollector(timeout=timeout, cookies=jar)
        elif scope == NATIONAL_BOARD:
            collector = DouyinCreatorNationalCollector(timeout=timeout, cookies=jar)
        elif scope == "weibo-wuxi":
            collector = WeiboWuxiCollector(timeout=timeout, cookies=jar)
        else:
            collector = PlatformCollector("douyin", timeout=timeout, cookies=jar)
        try:
            board_batch = await collector.collect()
        except CollectionError as exc:
            raise SessionVerificationError(exc.code) from None
        if not board_batch.items:
            raise HTTPException(422, "verification_incomplete")
        result = await collect_board(
            scope,
            key,
            collector=VerifiedBoardImport(board_batch, store),
            activate=True,
            max_attempts=1,
        )
    if result["status"] in {"busy", "locked"}:
        raise HTTPException(409, "collection_busy")
    if result["status"] not in {"success", "partial"}:
        raise HTTPException(422, result.get("error_code") or "verification_incomplete")
    return JSONResponse(
        {
            "scope": scope,
            "item_count": result.get("item_count", result.get("items")),
            "status": result["status"],
            **session_status(scope),
        },
        headers=NO_STORE,
    )


async def verify_browser_session(browser, scope):
    async def credentials():
        try:
            return validate_cookies(SCOPES[scope], await browser.cookies())
        except ValueError:
            raise HTTPException(422, "session_unavailable") from None

    cookies = await credentials()
    try:
        return await verify_session(scope, cookies)
    except SessionVerificationError as exc:
        retry_codes = {"session_required", "upstream_rejected"}
        if SCOPES[scope] != "douyin" or exc.detail not in retry_codes:
            raise
    await browser.refresh_session(BROWSER_PAGES[scope])
    refreshed = await credentials()
    try:
        return await verify_session(scope, refreshed)
    except SessionVerificationError as exc:
        if exc.detail in retry_codes:
            raise HTTPException(422, "collector_session_rejected") from None
        raise


class PointerInput(BaseModel):
    kind: Literal["pointer"]
    phase: Literal["click", "down", "move", "up"]
    x: int = Field(ge=0, lt=WIDTH, strict=True)
    y: int = Field(ge=0, lt=HEIGHT, strict=True)


class KeyInput(BaseModel):
    kind: Literal["key"]
    key: Literal[
        "Enter",
        "Backspace",
        "Delete",
        "Tab",
        "Shift+Tab",
        "ArrowLeft",
        "ArrowRight",
        "ArrowUp",
        "ArrowDown",
        "Escape",
        "Home",
        "End",
        "ControlOrMeta+A",
    ]


class TextInput(BaseModel):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=128)


class ScrollInput(BaseModel):
    kind: Literal["scroll"]
    delta: int = Field(ge=-900, le=900, strict=True)


BROWSER_INPUT = TypeAdapter(
    Annotated[PointerInput | KeyInput | TextInput | ScrollInput, Field(discriminator="kind")]
)


async def browser_input(browser, request):
    data = await read_input(request)
    try:
        action = BROWSER_INPUT.validate_python(data)
    except ValidationError:
        raise HTTPException(400, "invalid_browser_input") from None
    if isinstance(action, PointerInput):
        await browser.pointer(action.phase, action.x, action.y)
    elif isinstance(action, KeyInput):
        await browser.page().keyboard.press(action.key)
    elif isinstance(action, TextInput):
        await browser.page().keyboard.type(action.text)
    else:
        await browser.page().mouse.wheel(0, action.delta)


@router.post("/browser/{scope}/{action}")
async def browser_action(
    scope: str,
    action: Literal["open", "frame", "input", "city", "login", "verify", "close"],
    request: Request,
):
    if scope not in SCOPES:
        raise HTTPException(404, "unknown_scope")
    # Browser requests go through the same-origin workbench proxy.
    if "origin" in request.headers:
        raise HTTPException(403, "origin_not_allowed")
    if action == "open":
        if not storage_configured():
            raise HTTPException(503, "storage_not_configured")
        try:
            browser = await start_browser(scope, BROWSER_PAGES[scope])
        except BrowserLoginError as exc:
            raise HTTPException(409 if exc.code == "browser_busy" else 503, exc.code) from None
        return JSONResponse(
            {
                "access_key": browser.access_key,
                "width": WIDTH,
                "height": HEIGHT,
                "has_city_page": scope in CITY_PAGES,
            },
            headers=NO_STORE,
        )
    browser = active_browsers.get(scope)
    if browser is None or browser.expires_at <= monotonic():
        raise HTTPException(410, "browser_expired")
    if not bearer_matches(request, browser.access_key):
        raise HTTPException(401, "invalid_browser_key")
    if action == "close":
        await close_browser(browser)
        return Response(status_code=204, headers=NO_STORE)
    async with browser.lock:
        if active_browsers.get(scope) is not browser or browser.expires_at <= monotonic():
            raise HTTPException(410, "browser_expired")
        try:
            if action == "frame":
                return Response(await browser.frame(), media_type="image/jpeg", headers=NO_STORE)
            if action == "input":
                await browser_input(browser, request)
            elif action == "login":
                await browser.open_login(BROWSER_PAGES[scope])
            elif action == "city":
                if scope not in CITY_PAGES:
                    raise HTTPException(404, "unknown_scope")
                await browser.navigate(CITY_PAGES[scope])
            else:
                await browser.release_pointer()
                result = await verify_browser_session(browser, scope)
        except HTTPException:
            raise
        except BrowserLoginError as exc:
            raise HTTPException(422, exc.code) from None
        except Exception:
            if action != "verify":
                raise HTTPException(502, "browser_interaction_failed") from None
            raise
        if action != "verify":
            return Response(status_code=204, headers=NO_STORE)
    await close_browser(browser)
    return result
