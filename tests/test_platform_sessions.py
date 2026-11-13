import os
from datetime import UTC, datetime
from json import loads
from types import SimpleNamespace

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from hotradar import session_store
from hotradar.api import platform_sessions
from hotradar.api.main import app
from hotradar.collectors.platforms import PlatformCollector
from hotradar.collectors.types import CollectionError
from hotradar.db.models import Board
from hotradar.platform_browser import LoginBrowser
from test_persistence import sample


def credential(value="fixture-login"):
    return [{"name": "session", "value": value, "domain": ".weibo.cn", "path": "/", "expires": -1}]


@pytest.fixture
def portable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOTRADAR_SESSION_DIR", str(tmp_path))
    monkeypatch.setenv("HOTRADAR_SESSION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(session_store, "ROOT", tmp_path)


def test_session_is_encrypted_scope_bound_and_key_bound(portable, monkeypatch):
    session_store.save_portable("weibo-wuxi", credential())
    path = session_store.portable_path("weibo-wuxi")
    assert b"fixture-login" not in path.read_bytes()
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
    assert session_store.read_session("weibo-wuxi")["cookies"] == credential()
    assert session_store.session_status("weibo-content")["state"] == "missing"
    session_store.portable_path("weibo-content").write_bytes(path.read_bytes())
    assert session_store.session_status("weibo-content")["state"] == "unavailable"
    monkeypatch.setenv("HOTRADAR_SESSION_KEY", Fernet.generate_key().decode())
    assert session_store.session_status("weibo-wuxi")["state"] == "unavailable"


def test_board_authorization_validates_before_replacing_session(portable, database, monkeypatch):
    factory, _ = database
    with factory.begin() as session:
        session.add(
            Board(
                id="weibo-wuxi",
                source_id="weibo",
                name="无锡榜",
                item_kind="topic",
                metric_name="search_heat",
                metric_label="热搜指数",
                enabled=False,
                interval_seconds=900,
                parser_version="test",
            )
        )

    class Candidate:
        def __init__(self, *, timeout, cookies):
            self.cookies = cookies

        async def collect(self):
            if self.cookies.get("session") == "rejected":
                raise CollectionError("city_unverified", "Wrong city.")
            return sample()

    monkeypatch.setattr(platform_sessions, "WeiboWuxiCollector", Candidate)
    cookies = credential()

    async def open_page(_self, _url):
        pass

    async def read_cookies(_self):
        return cookies

    monkeypatch.setattr(LoginBrowser, "open", open_page)
    monkeypatch.setattr(LoginBrowser, "cookies", read_cookies)
    path = "/api/v1/platform-sessions/browser/weibo-wuxi/"
    with TestClient(app) as client:
        assert (
            client.post("/api/v1/platform-sessions/weibo-wuxi", json=credential()).status_code
            == 404
        )
        opened = client.post(path + "open").json()
        auth = {"Authorization": "Bearer " + opened["access_key"]}
        assert client.post(path + "verify", headers=auth).status_code == 200
        opened = client.post(path + "open").json()
        auth = {"Authorization": "Bearer " + opened["access_key"]}
        cookies = credential("rejected")
        response = client.post(path + "verify", headers=auth)
        assert response.status_code == 422 and response.json() == {"detail": "city_unverified"}
        cookies = [{**credential()[0], "domain": "weibo.cn.invalid.example"}]
        assert client.post(path + "verify", headers=auth).status_code == 422
        result = client.get("/api/v1/platform-sessions")
        assert "fixture-login" not in result.text and opened["access_key"] not in result.text
        assert result.json()["sources"]["weibo-wuxi"]["state"] == "saved"
    assert session_store.read_session("weibo-wuxi")["cookies"] == credential()
    with factory() as session:
        assert session.get(Board, "weibo-wuxi").enabled


@pytest.mark.asyncio
async def test_douyin_uses_verified_fallback_after_anonymous_rejection(portable):
    session_store.save_portable("douyin-hot", [{**credential()[0], "domain": ".douyin.com"}])
    calls = []

    def respond(request):
        calls.append(request)
        if request.url.path.endswith("login_guiding_strategy/"):
            return httpx.Response(200)
        if "cookie" not in request.headers:
            return httpx.Response(403)
        assert request.headers["cookie"] == "session=fixture-login"
        return httpx.Response(
            200,
            json={
                "status_code": 0,
                "private_field": "must-not-be-retained",
                "data": {"word_list": [{"sentence_id": "123", "word": "话题", "position": 1}]},
            },
        )

    batch = await PlatformCollector("douyin", transport=httpx.MockTransport(respond)).collect()
    assert len(calls) == 3 and batch.items[0].title == "话题"
    assert "must-not-be-retained" not in str(batch.raw_payload)
    assert batch.observed_at <= datetime.now(UTC)


@pytest.mark.asyncio
async def test_one_weibo_login_enables_both_sources_only_after_both_probes(portable, monkeypatch):
    calls = []

    async def fetch(*, verify, cookies):
        calls.append("content")
        assert verify and cookies.get("session")
        return ([{"external_id": "1"}], 0)

    class City:
        def __init__(self, timeout, cookies):
            calls.append("city")
            self.cookies = cookies

        async def collect(self):
            if self.cookies.get("session") == "rejected":
                raise CollectionError("city_unverified", "Wrong city.")
            return sample()

    async def collect_content(key, *, verify, activate, fetcher):
        calls.append("save-content")
        items, rejected = await fetcher(verify=verify)
        assert verify and activate and items and rejected == 0 and key.startswith("browser-auth:")
        return {"status": "success", "item_count": len(items)}

    async def collect_city(board_id, key, collector, activate, max_attempts):
        calls.append("save-city")
        batch = await collector.collect()
        assert board_id == "weibo-wuxi" and activate and max_attempts == 1
        assert key.startswith("browser-auth:")
        return {"status": "success", "items": len(batch.items)}

    monkeypatch.setattr(platform_sessions, "fetch_weibo", fetch)
    monkeypatch.setattr(platform_sessions, "WeiboWuxiCollector", City)
    monkeypatch.setattr(platform_sessions, "collect_weibo", collect_content)
    monkeypatch.setattr(platform_sessions, "collect_board", collect_city)
    monkeypatch.setattr(platform_sessions, "get_settings", lambda: SimpleNamespace(timeout=20))

    with pytest.raises(platform_sessions.SessionVerificationError) as failure:
        await platform_sessions.verify_weibo_login(credential("rejected"))
    assert failure.value.detail == "city_unverified"
    assert calls == ["content", "city"]
    assert not session_store.portable_path("weibo-content").exists()
    assert not session_store.portable_path("weibo-wuxi").exists()

    calls.clear()
    response = await platform_sessions.verify_weibo_login(credential())
    payload = loads(response.body)
    assert calls == ["content", "city", "save-content", "save-city"]
    assert payload["content_item_count"] == 1
    assert payload["city_item_count"] == len(sample().items)
    assert payload["item_count"] == payload["content_item_count"] + payload["city_item_count"]
    assert payload["status"] == "success" and payload["state"] == "saved"
    assert session_store.read_session("weibo-content")["cookies"] == credential()
    assert session_store.read_session("weibo-wuxi")["cookies"] == credential()
