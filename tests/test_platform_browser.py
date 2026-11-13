from time import monotonic
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from hotradar import platform_browser
from hotradar.api import platform_sessions
from hotradar.api.main import app

ROOT = "/api/v1/platform-sessions/browser/douyin-content/"


def bearer(token):
    return {"Authorization": "Bearer " + token}


@pytest.mark.asyncio
async def test_douyin_browser_retries_with_refreshed_cookies(monkeypatch):
    attempted = []
    names = ["before-refresh"]

    async def cookies():
        return [{"name": "sessionid", "value": names[-1], "domain": ".douyin.com", "path": "/"}]

    async def refresh(url):
        assert url == "https://www.douyin.com/jingxuan"
        names.append("after-refresh")

    async def verify(scope, current):
        assert scope == "douyin-content"
        attempted.append(current[0]["value"])
        if len(attempted) == 1:
            raise platform_sessions.SessionVerificationError("session_required")
        return {"status": "success"}

    monkeypatch.setattr(platform_sessions, "verify_session", verify)
    browser = SimpleNamespace(cookies=cookies, refresh_session=refresh)
    assert await platform_sessions.verify_browser_session(browser, "douyin-content") == {
        "status": "success"
    }
    assert attempted == ["before-refresh", "after-refresh"]


@pytest.mark.asyncio
async def test_douyin_browser_reports_unusable_session_separately():
    session = {"name": "sessionid", "value": "example", "domain": ".douyin.com", "path": "/"}

    async def browser_cookies():
        return [session, {**session, "name": "analytics", "value": "非 HTTP 字符"}]

    browser = platform_browser.LoginBrowser("douyin-content")
    browser.context = SimpleNamespace(cookies=browser_cookies)
    assert [cookie["name"] for cookie in await browser.cookies()] == ["sessionid"]

    async def invalid_cookies():
        return [{**session, "domain": ".example.org"}]

    with pytest.raises(HTTPException, match="session_unavailable"):
        await platform_sessions.verify_browser_session(
            SimpleNamespace(cookies=invalid_cookies), "douyin-content"
        )


def test_browser_only_loads_platform_and_challenge_origins():
    allowed = platform_browser.allowed_request
    assert allowed("https://creator.douyin.com/login", "douyin", document=True)
    assert allowed("https://static.geetest.com/captcha", "weibo", document=True)
    assert allowed("https://p-pc-weboff.byteimg.com/asset", "douyin")
    assert not allowed("https://p-pc-weboff.byteimg.com/asset", "douyin", document=True)
    assert not allowed("http://weibo.com/login", "weibo", document=True)
    assert not allowed("https://weibo.com.example.org", "weibo", document=True)
    assert not allowed("https://127.0.0.1:18810/", "douyin")


def test_browser_commands_are_bound_to_the_window_and_expire(monkeypatch):
    monkeypatch.setenv("HOTRADAR_SESSION_KEY", Fernet.generate_key().decode())
    interactions = []

    class Controls:
        async def press(self, key):
            interactions.append(("key", key))

        async def type(self, text):
            interactions.append(("text", text))

        async def click(self, x, y):
            interactions.append(("click", x, y))

        async def move(self, x, y):
            interactions.append(("move", x, y))

        async def down(self):
            interactions.append(("down",))

        async def up(self):
            interactions.append(("up",))

    controls = Controls()
    page = SimpleNamespace(
        mouse=controls,
        keyboard=controls,
        is_closed=lambda: False,
        viewport_size={"width": platform_browser.WIDTH, "height": platform_browser.HEIGHT},
    )

    async def open_page(_self, url):
        assert url == "https://www.douyin.com/jingxuan"

    async def frame(_self):
        return b"preview"

    async def close(_self):
        interactions.append(("close",))

    async def cookies(_self):
        return [
            {"domain": ".douyin.com", "name": "sessionid", "value": "test-private", "path": "/"}
        ]

    async def verify(scope, credentials):
        assert scope == "douyin-content" and credentials[0]["value"] == "test-private"
        return JSONResponse({"status": "success", "item_count": 1})

    monkeypatch.setattr(platform_browser.LoginBrowser, "open", open_page)
    monkeypatch.setattr(platform_browser.LoginBrowser, "frame", frame)
    monkeypatch.setattr(platform_browser.LoginBrowser, "close", close)
    monkeypatch.setattr(platform_browser.LoginBrowser, "page", lambda _self: page)
    monkeypatch.setattr(platform_browser.LoginBrowser, "cookies", cookies)
    monkeypatch.setattr(platform_sessions, "verify_session", verify)

    with TestClient(app) as client:
        assert (
            client.post(ROOT + "open", headers={"Origin": "https://example.org"}).status_code == 403
        )
        opened = client.post(ROOT + "open")
        assert opened.status_code == 200 and "test-private" not in opened.text
        assert client.post(ROOT + "open").status_code == 409
        key = bearer(opened.json()["access_key"])
        assert client.post(ROOT + "frame").status_code == 401
        assert client.post(ROOT + "frame", headers=key).content == b"preview"
        invalid = client.post(ROOT + "input", headers=key, json={"kind": "text", "text": "x" * 129})
        assert invalid.status_code == 400 and "x" * 129 not in invalid.text
        actions = [
            {"kind": "pointer", "phase": "click", "x": 24, "y": 41},
            {"kind": "text", "text": "123456"},
            {"kind": "key", "key": "Enter"},
            {"kind": "pointer", "phase": "down", "x": 30, "y": 40},
            {"kind": "pointer", "phase": "move", "x": 90, "y": 40},
            {"kind": "pointer", "phase": "up", "x": 160, "y": 40},
        ]
        for event in actions:
            assert client.post(ROOT + "input", headers=key, json=event).status_code == 204
        result = client.post(ROOT + "verify", headers=key)
        assert result.status_code == 200 and "test-private" not in result.text
        assert client.post(ROOT + "frame", headers=key).status_code == 410
        assert interactions == [
            ("click", 24, 41),
            ("text", "123456"),
            ("key", "Enter"),
            ("move", 30, 40),
            ("down",),
            ("move", 90, 40),
            ("move", 160, 40),
            ("up",),
            ("close",),
        ]
        second = client.post(ROOT + "open").json()
        platform_browser.active_browsers["douyin-content"].expires_at = monotonic() - 1
        assert client.post(ROOT + "frame", headers=bearer(second["access_key"])).status_code == 410
    assert not platform_browser.active_browsers
