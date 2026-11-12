import json
from datetime import UTC, datetime

import httpx
import pytest
from hotradar.collectors.douyin_app_city import (
    BOARD_ID,
    PARAMS,
    DouyinAppWuxiCollector,
    parse_app_wuxi,
)
from hotradar.collectors.types import CollectionError

NOW = datetime(2026, 10, 19, 8, 10, tzinfo=UTC)


def sample():
    return {
        "status_code": 0,
        "board_tabs": [{"board_type": 1, "board_sub_type": "320200", "tab_name": "无锡榜"}],
        "data": {
            "city_code": "320200",
            "city_name": "无锡",
            "active_time": "2026-10-19 16:03:15",
            "word_list": [
                {
                    "sentence_id": str(100 + i),
                    "word": f"测试城市话题{i}",
                    "position": i,
                    "hot_value": 0 if i == 1 else None,
                    "event_time": 1792024228,
                    "hotlist_param": json.dumps(
                        {
                            "board_type": 1,
                            "board_sub_type": "320200",
                            "rank": i,
                        }
                    ),
                    "cookie": "must-not-persist",
                    "word_cover": {"signed": "omit"},
                }
                for i in (1, 2)
            ],
        },
    }


def test_native_city_rank_metric_time_and_safe_raw_evidence():
    batch = parse_app_wuxi(sample(), NOW)
    assert [row.source_rank for row in batch.items] == [1, 2]
    assert batch.items[0].metrics["city_heat"]["value"] == 0
    assert batch.items[1].metrics["city_heat"]["value"] is None
    assert batch.items[0].published_at is None
    assert batch.items[0].source_event_at is not None
    assert batch.items[0].original_url == "https://www.douyin.com/hot/101"
    assert batch.upstream_updated_at == datetime(2026, 10, 19, 8, 3, 15, tzinfo=UTC)
    assert batch.observed_at == NOW
    assert "must-not-persist" not in json.dumps(batch.raw_payload)
    assert "word_cover" not in batch.raw_payload["response"]["data"]["word_list"][0]


@pytest.mark.parametrize("change", ["city", "tabs", "national_context", "missing_context"])
def test_city_echo_is_insufficient_and_national_fallback_is_rejected(change):
    payload = sample()
    if change == "city":
        payload["data"]["city_name"] = "北京"
    elif change == "tabs":
        payload["board_tabs"] = []
    elif change == "national_context":
        payload["data"]["word_list"][0]["hotlist_param"] = '{"board_type":0,"rank":1}'
    else:
        payload["data"]["word_list"][0].pop("hotlist_param")
    with pytest.raises(CollectionError) as error:
        parse_app_wuxi(payload, NOW)
    assert error.value.code == "city_unverified"


def test_partial_empty_and_failed_are_distinct():
    payload = sample()
    payload["data"]["word_list"][1]["sentence_id"] = "101"
    batch = parse_app_wuxi(payload, NOW)
    assert len(batch.items) == 1 and batch.rejected_count == 1
    assert len(batch.raw_payload["response"]["data"]["word_list"]) == 2
    payload["data"]["word_list"] = []
    assert parse_app_wuxi(payload, NOW).items == []
    payload["status_code"] = 8
    with pytest.raises(CollectionError) as error:
        parse_app_wuxi(payload, NOW)
    assert error.value.code == "upstream_rejected"


@pytest.mark.asyncio
async def test_only_verified_official_route_no_local_cookie_and_cache_marker():
    def respond(request):
        assert request.url.host == "so-landing.douyin.com"
        assert request.url.path == "/aweme/v1/hot/search/list/"
        assert dict(request.url.params) == PARAMS
        assert "cookie" not in request.headers
        return httpx.Response(200, json=sample(), headers={"Age": "10"})

    batch = await DouyinAppWuxiCollector(transport=httpx.MockTransport(respond)).collect()
    assert len(batch.items) == 2 and batch.from_cache is True


@pytest.mark.asyncio
async def test_production_dispatch_persistence_api_settings_and_history_isolation(
    event_db, monkeypatch
):
    from fastapi.testclient import TestClient
    from hotradar import bootstrap
    from hotradar.api import main, source_management
    from hotradar.collection import collect_board
    from hotradar.db.models import Board

    monkeypatch.setattr(bootstrap, "session_factory", lambda: event_db)
    monkeypatch.setattr(main, "session_factory", lambda: event_db)
    monkeypatch.setattr(source_management, "session_factory", lambda: event_db)
    bootstrap.seed_sources()
    with event_db.begin() as session:
        assert session.get(Board, BOARD_ID).enabled is False
        session.get(Board, BOARD_ID).enabled = True
    monkeypatch.setattr(DouyinAppWuxiCollector, "collect", lambda self: collected())

    async def collected():
        return parse_app_wuxi(sample(), NOW)

    result = await collect_board(BOARD_ID, "native-city-test", retry_delay=0)
    assert result["status"] == "success" and result["items"] == 2
    with TestClient(main.app) as client:
        board = client.get(f"/api/v1/boards?date=2026-10-19&board={BOARD_ID}").json()["board"]
        assert board["provider"] == "douyin-search-landing"
        assert board["snapshot"]["items"][1]["rank"] == 2
        assert client.get("/api/v1/boards/douyin-wuxi/latest").json()["snapshot"] is None
        assert client.get("/api/v1/boards/douyin-hot/latest").json()["snapshot"] is None
        assert (
            client.get(f"/api/v1/boards?date=2026-10-18&board={BOARD_ID}").json()["board"][
                "snapshot"
            ]
            is None
        )
        assert client.get(f"/api/v1/boards/{BOARD_ID}/snapshots").json()["snapshots"]
        assert (
            client.patch(f"/api/v1/boards/{BOARD_ID}/settings", json={"enabled": False}).status_code
            == 200
        )
    bootstrap.seed_sources()
    with event_db() as session:
        assert session.get(Board, BOARD_ID).enabled is False
