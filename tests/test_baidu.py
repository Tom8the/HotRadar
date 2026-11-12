import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from hotradar import bootstrap, collection, events, trends
from hotradar.api import main, source_management
from hotradar.collectors.baidu import BAIDU_URL, BaiduCollector, parse_baidu
from hotradar.collectors.types import CollectionError
from hotradar.db.models import Board, BoardSnapshot, ContentSourceSetting
from sqlalchemy import func, select

NOW = datetime.now(UTC)


def row(title="百度真实字段测试话题", **values):
    return {
        "word": title,
        "rawUrl": "https://www.baidu.com/s?wd=" + title,
        "index": 0,
        "hotScore": "123456",
        **values,
    }


def html(rows, **card_values):
    return (
        "<!--s-data:"
        + json.dumps(
            {
                "tab": "realtime",
                "data": {
                    "cards": [
                        {
                            "component": "hotList",
                            "typeName": "realtime",
                            "content": rows,
                            "updateTime": int(NOW.timestamp()),
                            **card_values,
                        }
                    ]
                },
            },
            ensure_ascii=False,
        )
        + "-->"
    )


def test_baidu_pinned_rank_metric_provenance_and_missing_times():
    batch = parse_baidu(
        html(
            [
                row("置顶话题", isTop=True),
                row(hotScore="0"),
                row("缺失指标", index=1, hotScore=None),
                row("缺失榜位", index=None),
            ]
        ),
        NOW,
    )
    assert [r.source_rank for r in batch.items] == [None, 1, 2, None]
    assert [r.display_order for r in batch.items] == [1, 2, 3, 4]
    assert batch.items[0].is_pinned
    assert batch.items[1].metrics["search_heat"]["value"] == 0
    assert batch.items[2].metrics["search_heat"]["value"] is None
    assert batch.items[1].metrics["search_heat"]["provider"] == "baidu"
    assert batch.items[1].published_at is None and batch.items[1].source_event_at is None
    assert batch.upstream_updated_at == NOW.replace(microsecond=0)
    assert batch.raw_payload["source_url"] == BAIDU_URL


@pytest.mark.parametrize("value", [None, "", "2万", True, -1, 1.5])
def test_baidu_invalid_metrics_never_become_zero(value):
    assert (
        parse_baidu(html([row(hotScore=value)]), NOW).items[0].metrics["search_heat"]["value"]
        is None
    )


@pytest.mark.parametrize(
    "page",
    [
        "<html>验证页面</html>",
        "<!--s-data:broken-->",
        "<!--s-data:[]-->",
        html([row()], typeName="novel"),
        '<!--s-data:{"tab":"realtime","data":{}}-->',
    ],
)
def test_baidu_wrong_board_challenge_or_changed_structure_fails(page):
    with pytest.raises(CollectionError):
        parse_baidu(page, NOW)


def test_baidu_empty_partial_duplicates_corrupted_titles_and_unsafe_links():
    assert parse_baidu(html([]), NOW).items == []
    batch = parse_baidu(
        html(
            [
                row(),
                row(),
                row("损坏\ufffd标题"),
                row("危险链接", rawUrl="javascript:alert(1)"),
                None,
                row("外站", rawUrl="https://www.baidu.com.evil.invalid/s?wd=x"),
                row("广告话题", is_ad=True),
            ]
        ),
        NOW,
    )
    assert len(batch.items) == 2 and batch.rejected_count == 5
    assert batch.items[1].flags == ["广告"] and batch.items[1].source_rank is None
    with pytest.raises(CollectionError, match="All Baidu"):
        parse_baidu(html([row("坏\ufffd")]), NOW)


@pytest.mark.asyncio
async def test_baidu_http_cache_and_failure_states():
    def respond(request):
        assert str(request.url) == BAIDU_URL
        return httpx.Response(200, text=html([row()]), headers={"Age": "60"})

    batch = await BaiduCollector(transport=httpx.MockTransport(respond)).collect()
    assert batch.from_cache and len(batch.items) == 1
    for status in [302, 403, 429, 500]:
        with pytest.raises(CollectionError, match=f"HTTP {status}"):
            await BaiduCollector(
                transport=httpx.MockTransport(lambda request, status=status: httpx.Response(status))
            ).collect()
    with pytest.raises(CollectionError, match="missing"):
        await BaiduCollector(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, text="<html>challenge</html>")
            )
        ).collect()


@pytest.mark.asyncio
async def test_baidu_dispatch_persistence_events_trends_settings_and_seed_preservation(
    database,
    monkeypatch,
):
    factory, original = database
    monkeypatch.setattr(bootstrap, "session_factory", lambda: factory)
    monkeypatch.setattr(main, "session_factory", lambda: factory)
    monkeypatch.setattr(source_management, "session_factory", lambda: factory)
    bootstrap.seed_sources()
    collector = BaiduCollector(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=html([row(hotScore=None)]))
        )
    )
    monkeypatch.setattr(collection, "BaiduCollector", lambda **kwargs: collector)
    assert "baidu-hot" in {b.id for b in collection.enabled_boards()}
    result = await collection.collect_board("baidu-hot", "baidu-dispatch")
    assert result["status"] == "success" and result["items"] == 1
    assert (await collection.collect_board("baidu-hot", "baidu-dispatch"))["reused"]
    with factory.begin() as s:
        assert events.process_pending(s)["snapshots"] == 1
        event = events.event_catalog(s, NOW.astimezone(trends.ZONE).date())[0]
        assert event["platforms"] == ["baidu"]
        rows = events.evidence_rows(s, event_ids={event["id"]})
        _, evidence = events.summarize(s.get(events.Event, event["id"]), rows)
        assert evidence[0]["source_name"] == "百度"
        series = trends.event_series(
            s,
            event["id"],
            (NOW - timedelta(days=6)).astimezone(trends.ZONE).date(),
            NOW.astimezone(trends.ZONE).date(),
            datetime.now(UTC),
        )
        assert len(series["series"]) == 1
        assert series["series"][0]["source_name"] == "百度"
    with TestClient(main.app) as client:
        data = client.get("/api/v1/boards?board=baidu-hot").json()["board"]
        assert data["source_name"] == "百度"
        assert data["snapshot"]["items"][0]["metrics"]["search_heat"]["value"] is None
        assert (
            client.get("/api/v1/boards?board=baidu-hot&date=2020-01-01").json()["board"]["snapshot"]
            is None
        )
        assert client.get("/api/v1/boards/baidu-hot/snapshots").json()["snapshots"]
        assert any(
            b["id"] == "baidu-hot"
            for b in client.get("/api/v1/source-management").json()["sources"]
        )
        assert (
            client.patch(
                "/api/v1/boards/baidu-hot/settings",
                json={
                    "enabled": False,
                    "interval_seconds": 1800,
                },
            ).status_code
            == 200
        )
    bootstrap.seed_sources()
    with factory() as s:
        board = s.get(Board, "baidu-hot")
        assert not board.enabled and board.interval_seconds == 1800
        assert s.get(Board, original).enabled
        assert s.scalar(select(func.count(BoardSnapshot.id))) == 1
        assert s.get(ContentSourceSetting, "baidu") is None
    assert (await collection.collect_board("baidu-hot", "paused"))["status"] == "disabled"
