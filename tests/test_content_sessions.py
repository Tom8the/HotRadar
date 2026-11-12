import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from hotradar import content_collection, session_store
from hotradar.collectors.types import CollectionError
from hotradar.db.models import ContentCollectionRun, ContentObservation, ContentSourceSetting
from sqlalchemy import func, select

NOW = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)


def row():
    return {
        "idstr": "123456789",
        "mblogid": "TestPost",
        "created_at": "Wed Oct 14 10:00:02 +0800 2026",
        "user": {"idstr": "12345", "screen_name": "test author"},
        "text_raw": "fixture text",
        "attitudes_count": 0,
        "comments_count": None,
        "reposts_count": 3,
    }


def test_exact_fields_and_raw_provenance():
    parsed = content_collection.parse_weibo_content(row(), NOW)
    assert parsed["metrics"] == {"likes": 0, "comments": None, "reposts": 3}
    assert parsed["published_at"] == "2026-10-14T02:00:02+00:00"
    assert parsed["published_precision"] == "second"
    assert parsed["original_url"] == "https://weibo.com/12345/TestPost"
    assert parsed["raw_business"]["attitudes_count"] == 0
    changed = {**row(), "attitudes_count": "1.5万", "reposts_count": False}
    assert content_collection.parse_weibo_content(changed, NOW) is None
    assert content_collection.parse_weibo_content({**row(), "isAd": True}, NOW) is None
    assert content_collection.parse_weibo_content({**row(), "created_at": "10-14"}, NOW) is None
    assert content_collection.parse_weibo_content({**row(), "user": {}}, NOW) is None
    long = content_collection.parse_weibo_content({**row(), "text_raw": "x" * 500}, NOW)
    assert long["title_is_excerpt"] and len(long["title"]) == 161
    assert len(long["raw_business"]["text_raw"]) == 500


@pytest.mark.asyncio
async def test_normal_feed_detail_validation_and_gate():
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if "hottimeline" in request.url.path:
            return httpx.Response(200, json={"ok": 1, "statuses": [row(), {"invalid": True}]})
        return httpx.Response(200, json={"ok": 1, **row()})

    items, rejected = await content_collection.fetch_weibo(
        verify=True, transport=httpx.MockTransport(handle), cookies=httpx.Cookies()
    )
    assert len(items) == 1 and rejected == 1
    assert calls == ["/ajax/feed/hottimeline", "/ajax/statuses/show"]
    calls.clear()

    def gated(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://attacker.example/collect"})

    with pytest.raises(CollectionError) as failure:
        await content_collection.fetch_weibo(
            transport=httpx.MockTransport(gated), cookies=httpx.Cookies()
        )
    assert failure.value.code == "session_required" and len(calls) == 1


def test_success_idempotence_expiry_pause_and_history(event_db, monkeypatch):
    monkeypatch.setattr(content_collection, "session_factory", lambda: event_db)
    with event_db.begin() as session:
        session.add(ContentSourceSetting(source_id="weibo"))
    requests = []

    async def valid(**kwargs):
        requests.append(kwargs)
        return [content_collection.parse_weibo_content(row(), NOW)], 0

    first = asyncio.run(
        content_collection.collect_weibo("verified-test", verify=True, activate=True, fetcher=valid)
    )
    second = asyncio.run(
        content_collection.collect_weibo("verified-test", verify=True, activate=True, fetcher=valid)
    )
    assert first["enabled"] and second["reused"] and len(requests) == 1

    async def expired(**_kwargs):
        raise CollectionError("session_required", "private-cookie-value-not-for-logs")

    result = asyncio.run(content_collection.collect_weibo("expired-test", fetcher=expired))
    assert result["status"] == "failed" and result["item_count"] is None
    assert not result["enabled"] and "private-cookie" not in str(result)
    with event_db() as session:
        assert session.scalar(select(func.count()).select_from(ContentObservation)) == 1
        assert session.scalar(select(func.count()).select_from(ContentCollectionRun)) == 2
    assert (
        asyncio.run(content_collection.collect_weibo("disabled-test", fetcher=valid))["status"]
        == "disabled"
    )


def test_credential_store_is_local_encrypted_scoped_and_secure(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "ROOT", tmp_path / "sessions")
    credential = [
        {
            "name": "fixture_cookie",
            "value": "only-a-test-value",
            "domain": ".weibo.com",
            "path": "/",
            "expires": -1,
        }
    ]
    result = session_store.save("weibo", credential)
    assert result["encrypted"]
    assert b"only-a-test-value" not in session_store.cookie_path("weibo").read_bytes()
    assert session_store.load("weibo")["cookies"] == credential
    cookies = session_store.client_cookies("weibo")
    assert all(cookie.secure for cookie in cookies.jar)
    with pytest.raises(ValueError):
        session_store.save("weibo", [{**credential[0], "domain": "attacker.example"}])
    assert session_store.load("weibo")["cookies"] == credential


def test_weibo_mobile_cookie_scope_does_not_expand_to_other_sites():
    for domain in (
        "weibo.com",
        ".weibo.com",
        "m.s.weibo.com",
        "weibo.cn",
        ".weibo.cn",
        "m.weibo.cn",
    ):
        assert session_store.allowed_domain(domain, "weibo")
    for domain in (None, "notweibo.cn", "weibo.cn.attacker.example", "douyin.com"):
        assert not session_store.allowed_domain(domain, "weibo")
    assert not session_store.allowed_domain("weibo.cn", "douyin")
    assert session_store.platform_url("https://m.weibo.cn/api/container/getIndex", "weibo")
    assert not session_store.platform_url("http://m.weibo.cn/", "weibo")


def test_bad_batch_rolls_back_all_content_and_never_activates(event_db, monkeypatch):
    monkeypatch.setattr(content_collection, "session_factory", lambda: event_db)
    with event_db.begin() as session:
        session.add(ContentSourceSetting(source_id="weibo"))

    async def duplicate(**_kwargs):
        data = content_collection.parse_weibo_content(row(), NOW)
        return [data, data], 0

    result = asyncio.run(
        content_collection.collect_weibo("bad-batch", verify=True, activate=True, fetcher=duplicate)
    )
    assert result["status"] == "failed" and not result["enabled"]
    with event_db() as session:
        assert session.scalar(select(func.count()).select_from(ContentObservation)) == 0
        assert session.scalar(select(func.count()).select_from(ContentCollectionRun)) == 1
