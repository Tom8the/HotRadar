from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from hotradar.api import main, source_management
from hotradar.api.queries import serialize_snapshot
from hotradar.collection import collect_board
from hotradar.db.models import Board, BoardSnapshot, CollectionRun, SourceSettingChange
from sqlalchemy import func, select
from test_persistence import NOW, FakeCollector, sample


def setup_management(database, monkeypatch):
    factory, old_id = database
    with factory.begin() as session:
        session.get(Board, old_id).id = "weibo-hot"
    monkeypatch.setattr(main, "session_factory", lambda: factory)
    monkeypatch.setattr(source_management, "session_factory", lambda: factory)
    return factory, "weibo-hot"


@pytest.mark.asyncio
async def test_pause_blocks_worker_preserves_history_and_can_resume(database, monkeypatch):
    factory, board = setup_management(database, monkeypatch)
    await collect_board(board, "good", FakeCollector(sample()))
    with TestClient(main.app) as client:
        r = client.patch(f"/api/v1/boards/{board}/settings", json={"enabled": False})
        assert r.json()["changed"] is True
        dashboard = client.get("/api/v1/boards").json()
        assert dashboard["stats"]["enabled_sources"] == 0
        assert dashboard["boards"][0]["state"] == "paused"
        assert dashboard["boards"][0]["snapshot"]["item_count"] == 1
        assert client.get(f"/api/v1/boards/{board}/latest").json()["snapshot"]
        collector = FakeCollector(sample())
        assert (await collect_board(board, "disabled", collector))["status"] == "disabled"
        assert collector.calls == 0
        assert (
            client.patch(f"/api/v1/boards/{board}/settings", json={"enabled": True}).status_code
            == 200
        )
    assert (await collect_board(board, "resumed", FakeCollector(sample())))["status"] == "success"
    with factory() as session:
        assert session.scalar(select(func.count(BoardSnapshot.id))) == 2
        assert session.scalar(select(func.count(SourceSettingChange.id))) == 2


def test_settings_validation_audit_and_local_origin(database, monkeypatch):
    factory, board = setup_management(database, monkeypatch)
    with TestClient(main.app) as client:
        for body in [{}, {"interval_seconds": 60}, {"enabled": "false"}, {"cookie": "no"}]:
            assert client.patch(f"/api/v1/boards/{board}/settings", json=body).status_code == 422
        assert (
            client.patch("/api/v1/boards/baidu/settings", json={"enabled": True}).status_code == 404
        )
        assert (
            client.patch(
                f"/api/v1/boards/{board}/settings",
                json={"enabled": False},
                headers={"Origin": "https://external.invalid"},
            ).status_code
            == 403
        )
        first = client.patch(f"/api/v1/boards/{board}/settings", json={"interval_seconds": 1800})
        again = client.patch(f"/api/v1/boards/{board}/settings", json={"interval_seconds": 1800})
        assert first.json()["changed"] and not again.json()["changed"]
    with factory() as session:
        assert session.get(Board, board).interval_seconds == 1800
        audit = session.scalar(select(SourceSettingChange))
        assert audit.before == {"enabled": True, "interval_seconds": 900}
        assert audit.after == {"enabled": True, "interval_seconds": 1800}
        assert session.scalar(select(func.count(SourceSettingChange.id))) == 1


@pytest.mark.asyncio
async def test_management_counts_read_only_card_refresh_and_failed_count_unknown(
    database, monkeypatch
):
    factory, board = setup_management(database, monkeypatch)
    await collect_board(board, "good", FakeCollector(sample()))
    await collect_board(board, "bad", FakeCollector(failures=4), retry_delay=0)
    with TestClient(main.app) as client:
        result = client.get("/api/v1/source-management").json()["sources"][0]
        assert result["window"] == {
            "total": 2,
            "success": 1,
            "partial": 0,
            "failed": 1,
            "unfinished": 0,
        }
        assert result["recent_runs"][0]["item_count"] is None
        assert result["state"] == "failed" and result["snapshot_items"] == 1
        card = client.get("/api/v1/boards?board=weibo-hot").json()["board"]
        assert card["snapshot"]["items"][0]["metrics"]["search_heat"]["value"] == 0
        assert client.get("/api/v1/boards?board=baidu").status_code == 404
    with factory() as session:
        assert session.scalar(select(func.count(CollectionRun.id))) == 2


@pytest.mark.asyncio
async def test_cadence_change_does_not_reinterpret_old_rank_comparisons(database):
    factory, board_id = database
    old = await collect_board(board_id, "old", FakeCollector(sample()))
    with factory.begin() as session:
        session.get(Board, board_id).interval_seconds = 3600
    new = await collect_board(
        board_id, "new", FakeCollector(sample(observed=NOW + timedelta(hours=1)))
    )
    with factory() as session:
        assert session.get(BoardSnapshot, old["snapshot"]).interval_seconds == 900
        assert session.get(BoardSnapshot, new["snapshot"]).interval_seconds == 3600
        saved = serialize_snapshot(
            session, session.get(BoardSnapshot, new["snapshot"]), session.get(Board, board_id)
        )
        assert saved["items"][0]["rank_change"] is None


def test_seed_restart_preserves_paused_source_and_custom_interval(database, monkeypatch):
    from hotradar import bootstrap

    factory, board = setup_management(database, monkeypatch)
    monkeypatch.setattr(bootstrap, "session_factory", lambda: factory)
    with TestClient(main.app) as client:
        client.patch(
            f"/api/v1/boards/{board}/settings", json={"enabled": False, "interval_seconds": 3600}
        )
    bootstrap.seed_sources()
    with factory() as session:
        configured = session.get(Board, board)
        assert configured.enabled is False and configured.interval_seconds == 3600
        assert {b.source_id for b in session.scalars(select(Board))} == {
            "weibo", "douyin", "baidu",
        }
