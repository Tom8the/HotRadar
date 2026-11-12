from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient
from hotradar import events, trends
from hotradar.api import trends as trend_api
from hotradar.api.main import app
from hotradar.collection import persist_batch
from hotradar.collectors.platforms import parse_weibo
from hotradar.db.models import (
    BoardSnapshot,
    CollectionRun,
    Event,
    EventChange,
    EventMember,
    EventSummary,
    RawPayload,
    SnapshotItem,
    SourceSettingChange,
    new_id,
)
from sqlalchemy import func, select
from test_m3_events import NOW, batch


def selected_event(session):
    return session.scalar(select(EventMember.event_id))


def build(session, title="中朝新鸭绿江大桥", observed=NOW, metric=None):
    snapshot = batch(session, "weibo", title, observed, metric=metric)
    events.process_pending(session)
    return snapshot


def test_real_days_only_and_beijing_midnight_window(event_db):
    with event_db.begin() as s:
        build(s)
        batch(s, "douyin", "中朝新鸭绿江大桥将开通", NOW + timedelta(minutes=1), metric=700)
        events.process_pending(s)
        result = trends.overview(s, date(2026, 10, 8), NOW.date(), NOW + timedelta(hours=2))
        assert result["sampled_days"] == 1 and result["requested_days"] == 7
        assert result["window_start"] == datetime(2026, 10, 7, 16, tzinfo=UTC)
        assert result["window_end"] == NOW + timedelta(hours=2)
        assert len(result["events"]) == 1 and len(result["events"][0]["platforms"]) == 2
        for source in result["sources"]:
            assert source["coverage"]["sampled_days"] == 1
            assert all(d["snapshots"] is None for d in source["coverage"]["days"][:-1])
            assert source["coverage"]["gaps"][0]["kind"] == "before_first"
        assert result["seven_days_verified"] is False


def test_zero_missing_and_no_cross_platform_sum(event_db):
    with event_db.begin() as s:
        build(s, metric=0)
        build(s, observed=NOW + timedelta(minutes=15), metric=20)
        batch(s, "douyin", "中朝新鸭绿江大桥将开通", NOW + timedelta(minutes=16), metric=300)
        events.process_pending(s)
        data = trends.event_series(
            s, selected_event(s), NOW.date(), NOW.date(), NOW + timedelta(minutes=20)
        )
        weibo = next(r for r in data["series"] if r["platform"] == "weibo")
        douyin = next(r for r in data["series"] if r["platform"] == "douyin")
        assert weibo["points"][0]["metric"]["value"] == 0
        assert weibo["comparison"]["metric_delta"] == 20
        assert weibo["comparison"]["metric_percent"] is None
        assert douyin["points"][0]["metric"]["value"] == 300
        assert douyin["comparison"]["metric_delta"] is None
        assert "total_heat" not in data
        build(s, observed=NOW + timedelta(minutes=30), metric=None)
        data = trends.event_series(
            s, selected_event(s), NOW.date(), NOW.date(), NOW + timedelta(minutes=31)
        )
        weibo = next(r for r in data["series"] if r["platform"] == "weibo")
        assert weibo["points"][-1]["metric"]["value"] is None
        assert weibo["comparison"]["metric_delta"] is None


def test_failure_absence_partial_cache_break_connections(event_db):
    with event_db.begin() as s:
        build(s, metric=100)
        event_id = selected_event(s)
        run = CollectionRun(
            id=new_id(),
            board_id="weibo-hot",
            job_key=new_id(),
            parser_version="test",
            status="failed",
            created_at=NOW + timedelta(minutes=5),
            started_at=NOW + timedelta(minutes=5),
            error_code="network_error",
        )
        s.add(run)
        build(s, observed=NOW + timedelta(minutes=15), metric=120)
        data = trends.event_series(s, event_id, NOW.date(), NOW.date(), NOW + timedelta(minutes=16))
        points = data["series"][0]["points"]
        assert points[1]["status"] == "failed" and points[1]["rank"] is None
        assert not points[-1]["connectable_before"]
        assert data["series"][0]["comparison"]["metric_delta"] is None
        empty_run = CollectionRun(
            id=new_id(), board_id="weibo-hot", job_key=new_id(), parser_version="test"
        )
        s.add(empty_run)
        s.flush()
        empty = persist_batch(
            s,
            empty_run,
            parse_weibo({"ok": 1, "data": {"realtime": []}}, NOW + timedelta(minutes=30)),
        )
        events.process_pending(s)
        data = trends.event_series(s, event_id, NOW.date(), NOW.date(), NOW + timedelta(minutes=31))
        assert data["series"][0]["points"][-1]["status"] == "not_on_board"
        assert data["series"][0]["points"][-1]["metric"] is None
        empty.status = "partial"
        s.flush()
        data = trends.event_series(s, event_id, NOW.date(), NOW.date(), NOW + timedelta(minutes=31))
        assert data["series"][0]["points"][-1]["status"] == "unknown_partial"
        cached = build(s, observed=NOW + timedelta(minutes=45), metric=160)
        cached.from_cache = True
        s.flush()
        build(s, observed=NOW + timedelta(minutes=60), metric=180)
        data = trends.event_series(s, event_id, NOW.date(), NOW.date(), NOW + timedelta(minutes=61))
        assert data["series"][0]["points"][-2]["from_cache"]
        assert not data["series"][0]["points"][-1]["connectable_before"]


def test_cadence_pause_and_missing_interval_are_distinct(event_db):
    with event_db.begin() as s:
        first = build(s, metric=100)
        first.interval_seconds = 900
        s.add(
            SourceSettingChange(
                board_id="weibo-hot",
                changed_at=NOW + timedelta(minutes=2),
                before={"enabled": True, "interval_seconds": 900},
                after={"enabled": True, "interval_seconds": 1800},
            )
        )
        second = build(s, observed=NOW + timedelta(minutes=30), metric=120)
        second.interval_seconds = 1800
        s.add(
            SourceSettingChange(
                board_id="weibo-hot",
                changed_at=NOW + timedelta(minutes=35),
                before={"enabled": True, "interval_seconds": 1800},
                after={"enabled": False, "interval_seconds": 1800},
            )
        )
        s.add(
            SourceSettingChange(
                board_id="weibo-hot",
                changed_at=NOW + timedelta(minutes=65),
                before={"enabled": False, "interval_seconds": 1800},
                after={"enabled": True, "interval_seconds": 1800},
            )
        )
        third = build(s, observed=NOW + timedelta(minutes=90), metric=150)
        third.interval_seconds = 1800
        s.flush()
        result = trends.event_series(
            s, selected_event(s), NOW.date(), NOW.date(), NOW + timedelta(minutes=91)
        )
        gaps = next(s for s in result["sources"] if s["id"] == "weibo-hot")["coverage"]["gaps"]
        assert any(g["kind"] == "paused" for g in gaps)
        assert not any(g["kind"] == "missing" and g["start"] < second.observed_at for g in gaps)
        assert any(
            g["kind"] == "missing" and g["start"] >= NOW + timedelta(minutes=65) for g in gaps
        )
        assert not result["series"][0]["points"][-1]["connectable_before"]


def test_metric_definition_parser_change_and_ad_are_not_comparable(event_db):
    with event_db.begin() as s:
        build(s, metric=100)
        event_id = selected_event(s)
        second = build(s, observed=NOW + timedelta(minutes=15), metric=80)
        second.parser_version = "new-version"
        row = s.scalar(select(SnapshotItem).where(SnapshotItem.snapshot_id == second.id))
        row.metrics = {"search_heat": {**row.metrics["search_heat"], "definition": "另一个口径"}}
        s.flush()
        result = trends.event_series(
            s, event_id, NOW.date(), NOW.date(), NOW + timedelta(minutes=16)
        )
        assert not result["series"][0]["points"][0]["metric_compatible"]
        assert result["series"][0]["comparison"]["metric_delta"] is None
        batch(s, "weibo", "中朝新鸭绿江大桥", NOW + timedelta(minutes=30), ad=True)
        events.process_pending(s)
        result = trends.event_series(
            s, event_id, NOW.date(), NOW.date(), NOW + timedelta(minutes=31)
        )
        assert result["series"][0]["points"][-1]["status"] == "not_on_board"
        assert result["series"][0]["points"][-1]["evidence_id"] is None


def test_current_manual_membership_changes_trend_not_raw_observation(event_db):
    with event_db.begin() as s:
        build(s)
        batch(s, "douyin", "中朝新鸭绿江大桥将开通", NOW + timedelta(minutes=1))
        events.process_pending(s)
        event = s.get(Event, selected_event(s))
        members = list(s.scalars(select(EventMember).where(EventMember.event_id == event.id)))
        count = s.scalar(select(func.count(SnapshotItem.id)))
        events.split(s, event.id, [members[0].item_id], {event.id: event.revision}, "测试拆分来源")
        result = trends.event_series(
            s, event.id, NOW.date(), NOW.date(), NOW + timedelta(minutes=2)
        )
        assert len(result["series"]) == 1
        assert count == s.scalar(select(func.count(SnapshotItem.id)))


def test_trend_api_is_readonly_validates_window_and_ids(event_db, monkeypatch):
    monkeypatch.setattr(trend_api, "session_factory", lambda: event_db)
    with event_db.begin() as s:
        build(s, observed=NOW + timedelta(seconds=1))
        event_id = selected_event(s)
        counts = {
            m.__tablename__: s.scalar(select(func.count()).select_from(m))
            for m in (Event, EventSummary, EventChange, RawPayload, BoardSnapshot)
        }
    with TestClient(app) as client:
        result = client.get("/api/v1/trends?start=2026-10-08&end=2026-10-14")
        assert result.status_code == 200 and result.json()["sampled_days"] == 1
        assert (
            client.get(
                f"/api/v1/trends/events/{event_id}?start=2026-10-08&end=2026-10-14"
            ).status_code
            == 200
        )
        for params in [
            "start=invalid",
            "start=2026-10-14&end=2026-10-13",
            "start=2026-01-01&end=2026-10-14",
            "end=2099-01-01",
        ]:
            assert client.get("/api/v1/trends?" + params).status_code == 422
        assert client.get("/api/v1/trends/events/not-a-uuid").status_code == 422
        assert client.get(f"/api/v1/trends/events/{new_id()}").status_code == 404
    with event_db() as s:
        assert counts == {
            m.__tablename__: s.scalar(select(func.count()).select_from(m))
            for m in (Event, EventSummary, EventChange, RawPayload, BoardSnapshot)
        }


def test_beijing_midnight_excludes_neighboring_day_observation(event_db):
    with event_db.begin() as s:
        before = datetime(2026, 10, 13, 15, 59, 59, tzinfo=UTC)
        after = before + timedelta(seconds=1)
        build(s, observed=before, metric=10)
        build(s, observed=after, metric=20)
        data = trends.event_series(
            s, selected_event(s), date(2026, 10, 14), date(2026, 10, 14), NOW
        )
        points = data["series"][0]["points"]
        assert len(points) == 1 and points[0]["observed_at"] == after
        assert points[0]["metric"]["value"] == 20
        assert data["series"][0]["comparison"]["metric_delta"] is None
