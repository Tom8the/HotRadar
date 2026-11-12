from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx
import pytest
from hotradar.collectors.types import CollectionError
from hotradar.collectors.weibo_city import (
    CITY,
    CONTAINER,
    PARAMS,
    WeiboWuxiCollector,
    parse_wuxi,
)

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)


def sample():
    topics = []
    for i, title in enumerate(["测试同城话题", "测试指标缺失"], 1):
        topics.append(
            {
                "card_type": 25,
                "title_sub": title,
                "desc": "同城热度 0 | @测试来源" if i == 1 else "来源未提供",
                "itemid": f"_position:{CITY['code']}|pos:1_{i}|qtime:123",
                "scheme": "https://m.weibo.cn/search?"
                + urlencode(
                    {
                        "containerid": urlencode({"type": "1", "t": "10", "q": "#" + title + "#"}),
                        "extparam": urlencode(
                            {"_position": CITY["code"], "filter_type": "hotregion"}
                        ),
                        "private_tracking": "must-not-persist",
                    }
                ),
                "cookie": "must-not-persist",
            }
        )
    return {
        "ok": 1,
        "data": {
            "cardlistInfo": {
                "containerid": CONTAINER,
                "cardlist_head_cards": [
                    {"channel_list": [{"containerid": CONTAINER, "name": "无锡"}]}
                ],
            },
            "cards": [{"card_type": 11, "card_group": topics}],
        },
    }


def test_verified_city_order_metrics_and_sanitized_provenance():
    batch = parse_wuxi(sample(), NOW)
    assert len(batch.items) == 2 and batch.rejected_count == 0
    assert [i.source_rank for i in batch.items] == [1, 2]
    assert batch.items[0].metrics["city_heat"]["value"] == 0
    assert batch.items[1].metrics["city_heat"]["value"] is None
    assert batch.upstream_updated_at is None
    assert batch.raw_payload["city"] == CITY
    assert "must-not-persist" not in str(batch.raw_payload)
    assert "extparam" not in batch.items[0].original_url
    assert batch.items[0].external_id == parse_wuxi(sample(), NOW).items[0].external_id


@pytest.mark.parametrize(
    "variant", ["national", "wrong-city", "wrong-topic-city", "location-error"]
)
def test_wrong_city_and_location_failure_never_become_empty_or_national_data(variant):
    payload = sample()
    info = payload["data"]["cardlistInfo"]
    rows = payload["data"]["cards"][0]["card_group"]
    if variant == "national":
        info["containerid"] = "national"
    if variant == "wrong-city":
        info["cardlist_head_cards"][0]["channel_list"][0]["name"] = "北京"
    if variant == "wrong-topic-city":
        rows[0]["scheme"] = rows[0]["scheme"].replace(CITY["code"], "800861100")
    if variant == "location-error":
        rows.append({"card_type": 42, "desc": "位置信息获取失败"})
    with pytest.raises(CollectionError) as error:
        parse_wuxi(payload, NOW)
    assert error.value.code == "city_unverified"


def test_empty_partial_and_bad_link_are_distinct():
    payload = sample()
    rows = payload["data"]["cards"][0]["card_group"]
    rows[0]["scheme"] = rows[0]["scheme"].replace("m.weibo.cn/search", "attacker.example/search")
    assert parse_wuxi(payload, NOW).rejected_count == 1
    rows[1]["title_sub"] = None
    with pytest.raises(CollectionError) as error:
        parse_wuxi(payload, NOW)
    assert error.value.code == "parse_failed"
    rows.clear()
    assert parse_wuxi(payload, NOW).items == []
    payload["data"]["cards"] = None
    with pytest.raises(CollectionError):
        parse_wuxi(payload, NOW)


@pytest.mark.asyncio
async def test_normal_session_request_and_redirect_gate():
    requests = []

    def respond(request):
        requests.append(request)
        assert request.url.host == "m.weibo.cn"
        assert dict(request.url.params) == PARAMS
        return httpx.Response(200, json=sample())

    batch = await WeiboWuxiCollector(cookies={}, transport=httpx.MockTransport(respond)).collect()
    assert len(batch.items) == 2 and len(requests) == 1

    def redirect(request):
        requests.append(request)
        return httpx.Response(302, headers={"location": "https://attacker.example/"})

    with pytest.raises(CollectionError) as error:
        await WeiboWuxiCollector(cookies={}, transport=httpx.MockTransport(redirect)).collect()
    assert error.value.code == "session_required" and len(requests) == 2


def test_city_persistence_api_and_settings_remain_independent(event_db, monkeypatch):
    from fastapi.testclient import TestClient
    from hotradar import bootstrap
    from hotradar.api import main, source_management
    from hotradar.collection import persist_batch
    from hotradar.db.models import Board, CollectionRun, new_id

    for module in (bootstrap, main, source_management):
        monkeypatch.setattr(module, "session_factory", lambda: event_db)
    bootstrap.seed_sources()
    with event_db.begin() as session:
        board = session.get(Board, "weibo-wuxi")
        assert not board.enabled
        board.enabled = True
        run = CollectionRun(board_id=board.id, job_key=new_id(), parser_version="weibo-wuxi-v1")
        session.add(run)
        session.flush()
        persist_batch(session, run, parse_wuxi(deepcopy(sample()), NOW))
    with TestClient(main.app) as client:
        board = client.get("/api/v1/boards?date=2026-10-15&board=weibo-wuxi").json()["board"]
        assert board["provider"] == "weibo-mobile" and board["source_id"] == "weibo"
        assert board["snapshot"]["item_count"] == 2
        assert client.get("/api/v1/boards/weibo-hot/latest").json()["snapshot"] is None
        assert (
            client.get("/api/v1/boards?date=2026-10-14&board=weibo-wuxi").json()["board"][
                "snapshot"
            ]
            is None
        )
        result = client.patch("/api/v1/boards/weibo-wuxi/settings", json={"enabled": False})
        assert result.status_code == 200
    bootstrap.seed_sources()
    with event_db() as session:
        assert not session.get(Board, "weibo-wuxi").enabled
        assert session.get(Board, "weibo-hot").enabled
