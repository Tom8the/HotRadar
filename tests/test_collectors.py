from datetime import UTC, date, datetime

import httpx
import pytest
from hotradar.api.queries import day_bounds
from hotradar.collectors.platforms import (
    PlatformCollector,
    parse_douyin,
    parse_weibo,
    sanitize_payload,
)
from hotradar.collectors.types import CollectionError

NOW = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)


def test_weibo_null_zero_rank_and_ad_separate():
    data = {
        "ok": 1,
        "data": {
            "realtime": [
                {"word": "第一条", "rank": 0, "num": 0},
                {"word": "缺失指标", "rank": 2},
                {"word": "广告", "is_ad": True, "rank": 3, "num": 100},
            ]
        },
    }
    result = parse_weibo(data, NOW)
    assert result.items[0].metrics["search_heat"]["value"] == 0
    assert result.items[0].source_rank == 1
    assert result.items[1].metrics["search_heat"]["value"] is None
    assert result.items[2].source_rank is None
    assert result.items[2].flags == ["广告"]
    assert all(row.published_at is None for row in result.items)


def test_douyin_topics_are_not_videos_or_publication_times():
    result = parse_douyin(
        {
            "status_code": 0,
            "data": {
                "word_list": [
                    {
                        "word": "热点",
                        "sentence_id": "123",
                        "position": 4,
                        "hot_value": "320",
                        "event_time": 1791943352,
                    },
                    {"word": "另一条", "sentence_id": "456", "position": 7},
                ]
            },
        },
        NOW,
    )
    assert result.items[0].source_rank == 4
    assert result.items[0].original_url.endswith("/hot/123")
    assert result.items[0].source_event_at is not None
    assert result.items[0].published_at is None
    assert result.items[1].metrics["topic_heat"]["value"] is None


@pytest.mark.parametrize(
    "payload", [{"ok": 0}, {"ok": 1, "data": {}}, {"ok": 1, "data": {"realtime": [{"rank": 0}]}}]
)
def test_invalid_response_is_not_successful_empty(payload):
    with pytest.raises(CollectionError):
        parse_weibo(payload, NOW)


def test_valid_empty_is_distinct_from_rejected_and_partial():
    assert parse_weibo({"ok": 1, "data": {"realtime": []}}, NOW).items == []
    batch = parse_weibo(
        {
            "ok": 1,
            "data": {
                "realtime": [
                    {"word": "有效", "rank": 4},
                    {"invalid": True},
                ]
            },
        },
        NOW,
    )
    assert len(batch.items) == 1 and batch.rejected_count == 1


def test_secret_fields_are_removed_recursively():
    assert sanitize_payload(
        {"data": {"cookie": "hidden", "title": "保留", "nested": [{"Authorization": "hidden"}]}}
    ) == {"data": {"title": "保留", "nested": [{}]}}


def test_beijing_midnight_boundary():
    start, end = day_bounds(date(2026, 10, 14))
    assert start == datetime(2026, 10, 13, 16, 0, tzinfo=UTC)
    assert end == datetime(2026, 10, 14, 16, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_http_failure_is_a_failed_collection():
    collector = PlatformCollector(
        "weibo", transport=httpx.MockTransport(lambda request: httpx.Response(403, text="Denied"))
    )
    with pytest.raises(CollectionError) as error:
        await collector.collect()
    assert error.value.code == "http_error"


@pytest.mark.asyncio
async def test_non_json_login_page_is_not_empty_data():
    collector = PlatformCollector(
        "weibo",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>Login</html>")
        ),
    )
    with pytest.raises(CollectionError) as error:
        await collector.collect()
    assert error.value.code == "invalid_json"
