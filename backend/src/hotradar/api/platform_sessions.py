from hmac import compare_digest
from json import loads
from os import environ
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from hotradar.collection import collect_board
from hotradar.collectors.douyin_city import PAGE as CREATOR_PAGE
from hotradar.collectors.douyin_city import DouyinWuxiCollector
from hotradar.collectors.platforms import PlatformCollector
from hotradar.collectors.types import CollectionError
from hotradar.collectors.weibo_city import PAGE as WEIBO_CITY_PAGE
from hotradar.collectors.weibo_city import WeiboWuxiCollector
from hotradar.config import PROJECT_ROOT, get_settings
from hotradar.content_collection import collect_douyin, collect_weibo, fetch_douyin, fetch_weibo
from hotradar.session_store import (
    MAX_SESSION_BYTES,
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
    "douyin-wuxi": CREATOR_PAGE,
    "douyin-content": "https://www.douyin.com/jingxuan",
    "weibo-wuxi": WEIBO_CITY_PAGE,
    "weibo-content": "https://weibo.com/",
}
CONTENT = {
    "douyin-content": (collect_douyin, fetch_douyin),
    "weibo-content": (collect_weibo, fetch_weibo),
}


def operator_token():
    load_dotenv(PROJECT_ROOT / ".env")
    return environ.get("HOTRADAR_SESSION_ADMIN_TOKEN", "")


def import_available(token):
    if len(token) < 32:
        return False
    try:
        session_cipher()
    except RuntimeError:
        return False
    return True


@router.get("")
def status():
    return JSONResponse(
        {
            "import_available": import_available(operator_token()),
            "sources": {
                scope: {"login_url": LOGIN_PAGES[scope], **session_status(scope)}
                for scope in SCOPES
            },
        },
        headers={"Cache-Control": "no-store"},
    )


async def imported_cookies(request, platform):
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_SESSION_BYTES:
                raise HTTPException(413, "session_too_large")
            body.extend(chunk)
        data = loads(body)
        if isinstance(data, dict):
            data = data.get("cookies")
        cookies = validate_cookies(platform, data)
    except (ValueError, RecursionError, ClientDisconnect):
        raise HTTPException(400, "invalid_session") from None
    return cookies


class VerifiedBoardImport:
    def __init__(self, batch, store):
        self.batch = batch
        self.store = store

    async def collect(self):
        self.store()
        return self.batch


@router.post("/{scope}")
async def import_session(scope: str, request: Request):
    if scope not in SCOPES:
        raise HTTPException(404, "unknown_scope")
    token = operator_token()
    if not import_available(token):
        raise HTTPException(503, "import_not_configured")
    supplied = request.headers.get("authorization", "")
    if not compare_digest(supplied.encode(), ("Bearer " + token).encode()):
        raise HTTPException(401, "invalid_operator_token")
    if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
        raise HTTPException(415, "invalid_session")
    cookies = await imported_cookies(request, SCOPES[scope])
    jar = cookie_jar(cookies)
    if not jar.jar:
        raise HTTPException(400, "expired_session")

    def store():
        try:
            save_portable(scope, cookies)
        except (OSError, RuntimeError):
            raise CollectionError("session_storage_failed", "Could not save session.") from None

    key = "session-import:" + uuid4().hex
    if scope in CONTENT:
        collect, fetch = CONTENT[scope]
        try:
            batch = await fetch(verify=True, cookies=jar)
        except CollectionError as exc:
            raise HTTPException(422, exc.code) from None

        async def store_verified_batch(*, verify):
            store()
            return batch

        result = await collect(key, verify=True, activate=True, fetcher=store_verified_batch)
    else:
        timeout = get_settings().timeout
        if scope == "douyin-wuxi":
            collector = DouyinWuxiCollector(timeout=timeout, cookies=jar)
        elif scope == "weibo-wuxi":
            collector = WeiboWuxiCollector(timeout=timeout, cookies=jar)
        else:
            collector = PlatformCollector("douyin", timeout=timeout, cookies=jar)
        try:
            board_batch = await collector.collect()
        except CollectionError as exc:
            raise HTTPException(422, exc.code) from None
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
        headers={"Cache-Control": "no-store"},
    )
