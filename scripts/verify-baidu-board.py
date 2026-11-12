"""Read-only verification of retained Baidu data, provenance and local API proxies."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from hotradar.db.models import (
    Board,
    BoardSnapshot,
    ContentSourceSetting,
    EventMember,
    Item,
    RawPayload,
    SnapshotItem,
)
from hotradar.db.session import session_factory
from sqlalchemy import func, select


def main():
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    with session_factory()() as session:
        board = session.get(Board, "baidu-hot")
        assert board is not None
        snapshot = session.scalar(
            select(BoardSnapshot)
            .where(BoardSnapshot.board_id == board.id)
            .order_by(BoardSnapshot.observed_at.desc())
            .limit(1)
        )
        assert snapshot is not None and snapshot.status == "success"
        payload = session.get(RawPayload, snapshot.payload_id).data
        raw_rows = {r["word"]: r for r in payload["card"]["content"]}
        rows = list(
            session.scalars(
                select(SnapshotItem)
                .where(SnapshotItem.snapshot_id == snapshot.id)
                .order_by(SnapshotItem.display_order)
            )
        )
        assert len(rows) == snapshot.item_count == len(raw_rows)
        for row in rows:
            raw = raw_rows[row.title]
            assert "\ufffd" not in row.title
            assert row.original_url == raw.get("rawUrl", raw.get("url"))
            assert row.is_pinned == bool(raw.get("isTop"))
            assert row.source_rank == (None if row.is_pinned else raw["index"] + 1)
            metric = row.metrics["search_heat"]
            expected = int(raw["hotScore"]) if raw.get("hotScore") is not None else None
            assert metric["value"] == expected and metric["provider"] == "baidu"
            assert row.published_at is None and row.source_event_at is None
        assert session.get(ContentSourceSetting, "baidu") is None
        events = list(
            session.scalars(
                select(EventMember.event_id)
                .join(Item, Item.id == EventMember.item_id)
                .where(Item.board_id == board.id)
            )
        )
        assert events
        count_before = session.scalar(
            select(func.count(BoardSnapshot.id)).where(BoardSnapshot.board_id == board.id)
        )
        first = session.scalar(
            select(func.min(BoardSnapshot.observed_at)).where(BoardSnapshot.board_id == board.id)
        )
        evidence = {
            "checked_at": datetime.now(UTC).isoformat(),
            "board_id": board.id,
            "snapshot_id": snapshot.id,
            "observed_at": snapshot.observed_at.isoformat(),
            "upstream_updated_at": snapshot.upstream_updated_at.isoformat()
            if snapshot.upstream_updated_at
            else None,
            "first_observed_at": first.isoformat(),
            "items": len(rows),
            "pinned": sum(r.is_pinned for r in rows),
            "event_members": len(events),
            "enabled": board.enabled,
            "interval_seconds": board.interval_seconds,
            "parser_version": snapshot.parser_version,
            "source_url": payload["source_url"],
            "from_cache": snapshot.from_cache,
        }
    with httpx.Client(timeout=30, trust_env=False) as client:
        data = client.get(f"http://127.0.0.1:18811/api/radar?board=baidu-hot&view=1&date={day}")
        data.raise_for_status()
        assert data.json()["board"]["snapshot"]["id"] == snapshot.id
        history = client.get(f"http://127.0.0.1:18811/api/radar?board=baidu-hot&date={day}")
        history.raise_for_status()
        assert any(s["id"] == snapshot.id for s in history.json()["snapshots"])
        detail = client.get(
            f"http://127.0.0.1:18811/api/radar?board=baidu-hot&snapshot={snapshot.id}"
        )
        detail.raise_for_status()
        assert detail.json()["snapshot"]["item_count"] == len(rows)
        management = client.get("http://127.0.0.1:18811/api/sources")
        management.raise_for_status()
        assert any(b["id"] == "baidu-hot" for b in management.json()["sources"])
        trend = client.get(
            f"http://127.0.0.1:18811/api/trends?start={day - timedelta(days=6)}&end={day}"
        )
        trend.raise_for_status()
        source = next(s for s in trend.json()["sources"] if s["id"] == "baidu-hot")
        assert source["coverage"]["sampled_days"] >= 1
        series = client.get(
            f"http://127.0.0.1:18811/api/trends?id={events[0]}"
            f"&start={day - timedelta(days=6)}&end={day}"
        )
        series.raise_for_status()
        assert any(s["platform"] == "baidu" for s in series.json()["series"])
        evidence["api_checks"] = ["board", "history", "snapshot", "management", "trends", "series"]
        evidence["sampled_days"] = source["coverage"]["sampled_days"]
    with session_factory()() as session:
        assert (
            session.scalar(
                select(func.count(BoardSnapshot.id)).where(BoardSnapshot.board_id == "baidu-hot")
            )
            == count_before
        )
    output = Path("data/baidu-probe/verification.json")
    output.parent.mkdir(exist_ok=True, parents=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=True))


if __name__ == "__main__":
    main()
