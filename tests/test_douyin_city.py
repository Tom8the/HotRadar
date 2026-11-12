from datetime import UTC, datetime

import httpx
import pytest
from hotradar.collectors.douyin_city import CITY, PARAMS, DouyinWuxiCollector, parse_wuxi
from hotradar.collectors.types import CollectionError

NOW = datetime(2026, 10, 15, 2, 0, tzinfo=UTC)


def sample():
    return {
        "status_code": 0,
        "update_time": 20261015100500,
        "item_list": [
            {
                "query_id": "123",
                "title": "测试同城话题",
                "rank": 1082,
                "hot_score": 0,
                "cover": {"url_list": ["https://example.test/?signature=omit"]},
            },
            {"query_id": "456", "title": "测试缺失指标", "rank": 1086},
        ],
    }


def test_city_board_preserves_source_rank_zero_missing_and_update_time():
    batch = parse_wuxi(sample(), NOW, CITY)
    assert [i.source_rank for i in batch.items] == [1082, 1086]
    assert batch.items[0].metrics["city_heat"]["value"] == 0
    assert batch.items[1].metrics["city_heat"]["value"] is None
    assert batch.items[0].original_url == "https://www.douyin.com/hot/123"
    assert batch.upstream_updated_at == datetime(2026, 10, 15, 2, 5, tzinfo=UTC)
    assert batch.raw_payload["request"]["params"]["city_code"] == "320200"
    assert "cover" not in batch.raw_payload["response"]["item_list"][0]
    with pytest.raises(CollectionError, match="Wuxi"):
        parse_wuxi(sample(), NOW, {"code": 110000, "name": "北京"})


def test_city_partial_keeps_rejected_business_evidence_and_distinguishes_empty():
    payload = sample()
    payload["item_list"] += [{"title": "缺少ID", "cookie": "must-not-persist"}, None]
    batch = parse_wuxi(payload, NOW, CITY)
    assert len(batch.items) == 2 and batch.rejected_count == 2
    assert len(batch.raw_payload["response"]["item_list"]) == 4
    rejected = batch.raw_payload["response"]["item_list"][2]
    assert rejected["title"] == "缺少ID" and "cookie" not in rejected
    assert parse_wuxi({"status_code": 0, "item_list": []}, NOW, CITY).items == []
    with pytest.raises(CollectionError, match="failed validation"):
        parse_wuxi({"status_code": 0, "item_list": [{"title": "缺少ID"}]}, NOW, CITY)


@pytest.mark.asyncio
async def test_city_collector_checks_real_city_config_and_never_uses_national_endpoint():
    requests = []

    def respond(request):
        requests.append(request)
        if request.url.path.endswith("config/"):
            return httpx.Response(200, json={"status_code": 0, "city_code_list": [CITY]})
        assert dict(request.url.params) == PARAMS
        return httpx.Response(200, json=sample())

    batch = await DouyinWuxiCollector(cookies={}, transport=httpx.MockTransport(respond)).collect()
    assert len(batch.items) == 2 and len(requests) == 2
    assert all(request.url.host == "creator.douyin.com" for request in requests)
    assert batch.parser_version == "douyin-wuxi-v1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config,code",
    [
        ({"status_code": 8}, "session_required"),
        ({"status_code": 0, "city_code_list": []}, "city_unverified"),
    ],
)
async def test_rejected_session_or_missing_city_cannot_be_successful_empty(config, code):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=config)

    with pytest.raises(CollectionError) as error:
        await DouyinWuxiCollector(cookies={}, transport=httpx.MockTransport(respond)).collect()
    assert error.value.code == code and len(requests) == 1


def test_city_api_and_snapshots_are_separate_from_national(event_db, monkeypatch):
    from fastapi.testclient import TestClient
    from hotradar import bootstrap
    from hotradar.api import main, source_management
    from hotradar.collection import persist_batch
    from hotradar.db.models import Board, CollectionRun, new_id

    monkeypatch.setattr(bootstrap, "session_factory", lambda: event_db)
    monkeypatch.setattr(main, "session_factory", lambda: event_db)
    monkeypatch.setattr(source_management, "session_factory", lambda: event_db)
    bootstrap.seed_sources()
    with event_db.begin() as session:
        city = session.get(Board, "douyin-wuxi")
        assert city.enabled is False
        city.enabled = True
        run = CollectionRun(board_id=city.id, job_key=new_id(), parser_version="douyin-wuxi-v1")
        session.add(run)
        session.flush()
        persist_batch(session, run, parse_wuxi(sample(), NOW, CITY))
    with TestClient(main.app) as client:
        board = client.get("/api/v1/boards?date=2026-10-15&board=douyin-wuxi").json()["board"]
        assert board["source_id"] == "douyin" and board["provider"] == "douyin-creator"
        assert len(board["snapshot"]["items"]) == 2
        assert client.get("/api/v1/boards/douyin-hot/latest").json()["snapshot"] is None
        assert (
            client.get("/api/v1/boards?date=2026-10-14&board=douyin-wuxi").json()["board"][
                "snapshot"
            ]
            is None
        )
        assert (
            client.patch("/api/v1/boards/douyin-wuxi/settings", json={"enabled": False}).status_code
            == 200
        )
    bootstrap.seed_sources()
    with event_db() as session:
        assert session.get(Board, "douyin-wuxi").enabled is False
