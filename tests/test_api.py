import pytest
from fastapi.testclient import TestClient
from hotradar.api import events as event_api
from hotradar.api import main
from hotradar.api import trends as trend_api
from hotradar.collection import collect_board
from sqlalchemy.exc import SQLAlchemyError
from test_persistence import FakeCollector, sample


@pytest.mark.asyncio
async def test_api_source_isolation_and_empty_historical_date(database, monkeypatch):
    factory, board = database
    await collect_board(board, "api-success", FakeCollector(sample()))
    monkeypatch.setattr(main, "session_factory", lambda: factory)
    with TestClient(main.app) as client:
        assert client.get("/api/health").json()["database"] == "connected"
        result = client.get("/api/v1/boards").json()
        assert result["stats"]["enabled_sources"] == 1
        assert result["boards"][0]["snapshot"]["items"][0]["metrics"]["search_heat"]["value"] == 0
        past = client.get("/api/v1/boards?date=2020-01-01").json()
        assert past["boards"][0]["snapshot"] is None
        assert past["stats"]["new_items"] == 0
        snapshots = client.get(f"/api/v1/boards/{board}/snapshots").json()["snapshots"]
        assert len(snapshots) == 1
        saved = client.get(f"/api/v1/boards/{board}/snapshots/{snapshots[0]['id']}")
        assert saved.json()["snapshot"]["items"][0]["title"] == "测试热点"
        assert client.get("/api/v1/boards/missing/latest").status_code == 404
        assert client.get("/api/v1/boards?date=invalid").status_code == 422


def test_unavailable_database_returns_safe_errors(monkeypatch):
    def unavailable():
        raise SQLAlchemyError("private connection details")

    monkeypatch.setattr(main, "session_factory", unavailable)
    monkeypatch.setattr(event_api, "session_factory", unavailable)
    monkeypatch.setattr(trend_api, "session_factory", unavailable)
    with TestClient(main.app) as client:
        for path in [
            "/api/health",
            "/api/v1/boards",
            "/api/v1/sources",
            "/api/v1/boards/weibo-hot/latest",
            "/api/v1/boards/weibo-hot/snapshots",
            "/api/v1/boards/weibo-hot/snapshots/missing",
            "/api/v1/events?date=2026-10-14",
            "/api/v1/events/00000000-0000-0000-0000-000000000000?date=2026-10-14",
            "/api/v1/trends?start=2026-10-08&end=2026-10-14",
            "/api/v1/trends/events/00000000-0000-0000-0000-000000000000",
        ]:
            result = client.get(path)
            assert result.status_code == 503
            assert "private" not in result.text
