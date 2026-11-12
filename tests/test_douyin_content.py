import asyncio
import copy
from datetime import UTC, datetime

import httpx
import pytest
from hotradar import content_collection
from hotradar.collectors.types import CollectionError
from hotradar.db.models import ContentCollectionRun, ContentObservation, ContentSourceSetting
from hotradar.douyin_content import FEED_PARAMS, fetch_douyin, parse_douyin_content
from sqlalchemy import func, select

NOW = datetime(2026, 10, 14, 14, 50, tzinfo=UTC)


def fixture():
    # Explicit fixture, never sent to the real platform or production database.
    return {
        "aweme_id": "1234567890123456789",
        "desc": "test video",
        "aweme_type": 0,
        "create_time": int(datetime(2026, 10, 14, 10, 0, 12, tzinfo=UTC).timestamp()),
        "author": {"nickname": "test author", "uid": "123", "sec_uid": "test_public_author"},
        "statistics": {
            "digg_count": 0,
            "comment_count": None,
            "collect_count": "2.3万",
            "share_count": 5,
            "play_count": 0,
        },
        "cookie": "fixture-secret-not-for-business",
        "is_ads": False,
    }


def test_precise_fields_missing_values_and_credential_whitelist():
    data = parse_douyin_content(fixture(), NOW)
    assert data["metrics"] == {"likes": 0, "comments": None, "favorites": None, "shares": 5}
    assert data["published_at_display"] == "2026-10-14 18:00:12"
    assert data["original_url"] == "https://www.douyin.com/video/1234567890123456789"
    assert "cookie" not in str(data) and "play_count" not in str(data)
    assert data["request_method"] == "POST" and data["published_precision"] == "second"
    assert parse_douyin_content(
        {**fixture(), "desc": "source……版本过低，升级后可展示全部信息"}, NOW
    )["title_is_excerpt"]


def test_incomplete_future_ads_and_non_video_rejected():
    for update in (
        {"create_time": True},
        {"create_time": -1},
        {"create_time": 9999999999999},
        {"create_time": int(NOW.timestamp()) + 1},
        {"is_ads": True},
        {"aweme_type": 101},
        {"aweme_id": "topic"},
        {"author": {}},
        {"statistics": {}},
        {"desc": ""},
    ):
        assert parse_douyin_content({**fixture(), **update}, NOW) is None
    bad = copy.deepcopy(fixture())
    bad["author"]["sec_uid"] = "../../.env"
    assert parse_douyin_content(bad, NOW) is None


@pytest.mark.asyncio
async def test_sdk_post_scope_validation_no_signature_and_redirect_protection():
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == "POST"
        assert dict(request.url.params) == FEED_PARAMS
        assert "a_bogus" not in str(request.url) and "signature" not in str(request.url)
        return httpx.Response(200, json={"status_code": 0, "aweme_list": [fixture(), {}]})

    rows, rejected = await fetch_douyin(
        verify=True, cookies=httpx.Cookies(), transport=httpx.MockTransport(handler)
    )
    assert len(rows) == 1 and rejected == 1 and len(seen) == 1
    for response in (
        httpx.Response(302, headers={"location": "https://attacker.example"}),
        httpx.Response(200, json={"status_code": 9}),
        httpx.Response(200, json={"status_code": 0, "aweme_list": []}),
    ):
        seen.clear()

        def denied(request, response=response):
            seen.append(request)
            return response

        with pytest.raises(CollectionError):
            await fetch_douyin(
                verify=True, cookies=httpx.Cookies(), transport=httpx.MockTransport(denied)
            )
        assert len(seen) == 1


def test_independent_source_activation_idempotence_and_failure_pause(event_db, monkeypatch):
    monkeypatch.setattr(content_collection, "session_factory", lambda: event_db)
    with event_db.begin() as session:
        session.add(ContentSourceSetting(source_id="douyin"))
        session.add(ContentSourceSetting(source_id="weibo", enabled=True, verified_at=NOW))
    calls = []

    async def real_shape(**_kwargs):
        calls.append(1)
        return [parse_douyin_content(fixture(), NOW)], 0

    first = asyncio.run(
        content_collection.collect_douyin(
            "sdk-fixture", verify=True, activate=True, fetcher=real_shape
        )
    )
    assert first["enabled"] and first["item_count"] == 1
    repeated = asyncio.run(content_collection.collect_douyin("sdk-fixture", fetcher=real_shape))
    assert repeated["reused"] and len(calls) == 1

    async def expired(**_kwargs):
        raise CollectionError("upstream_rejected", "fixture-private-cookie")

    failed = asyncio.run(content_collection.collect_douyin("expired-dy", fetcher=expired))
    assert failed["status"] == "failed" and not failed["enabled"]
    assert failed["item_count"] is None and "private-cookie" not in str(failed)
    with event_db() as session:
        assert session.get(ContentSourceSetting, "weibo").enabled
        assert session.scalar(select(func.count()).select_from(ContentObservation)) == 1
        assert session.scalar(select(func.count()).select_from(ContentCollectionRun)) == 2


def test_bad_douyin_batch_rolls_back_without_touching_other_source(event_db, monkeypatch):
    monkeypatch.setattr(content_collection, "session_factory", lambda: event_db)
    with event_db.begin() as session:
        session.add(ContentSourceSetting(source_id="douyin"))

    async def duplicate(**_kwargs):
        row = parse_douyin_content(fixture(), NOW)
        return [row, row], 0

    result = asyncio.run(
        content_collection.collect_douyin(
            "dy-bad-batch", verify=True, activate=True, fetcher=duplicate
        )
    )
    assert result["status"] == "failed" and not result["enabled"]
    with event_db() as session:
        assert session.scalar(select(func.count()).select_from(ContentObservation)) == 0


def test_scheduler_checks_both_sources_with_independent_cadence(event_db, monkeypatch):
    monkeypatch.setattr(content_collection, "session_factory", lambda: event_db)
    with event_db.begin() as session:
        session.add(ContentSourceSetting(source_id="weibo", enabled=True, verified_at=NOW))
        session.add(ContentSourceSetting(source_id="douyin", enabled=False, verified_at=NOW))
    calls = []

    async def weibo(key):
        calls.append(("weibo", key))
        return {"source_id": "weibo", "status": "success"}

    async def douyin(key):
        calls.append(("douyin", key))
        return {"source_id": "douyin", "status": "success"}

    monkeypatch.setattr(content_collection, "collect_weibo", weibo)
    monkeypatch.setattr(content_collection, "collect_douyin", douyin)
    assert len(asyncio.run(content_collection.run_due())) == 1
    assert calls[-1][0] == "weibo"
    with event_db.begin() as session:
        session.get(ContentSourceSetting, "douyin").enabled = True
        session.add(
            ContentCollectionRun(
                source_id="weibo", job_key="fresh-wb", version="test", started_at=datetime.now(UTC)
            )
        )
    assert len(asyncio.run(content_collection.run_due())) == 1
    assert calls[-1][0] == "douyin"
    with event_db.begin() as session:
        session.get(ContentSourceSetting, "douyin").verified_at = None
    assert asyncio.run(content_collection.run_due()) is None
