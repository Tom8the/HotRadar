"""Read-only audit: stored snapshots against raw source responses and M1 baseline."""

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from hotradar.api.queries import serialize_snapshot
from hotradar.collectors.platforms import parse_douyin, parse_weibo
from hotradar.db.models import Board, BoardSnapshot, RawPayload, SourceSettingChange
from hotradar.db.session import session_factory
from sqlalchemy import select

root = Path(__file__).resolve().parents[1]


def verify():
    baseline = json.loads((root / "docs/M1-verification.json").read_text(encoding="utf-8-sig"))
    previous = {row["snapshot"]: row for row in baseline["snapshots"]}
    records = []
    with session_factory()() as session:
        boards = list(
            session.scalars(select(Board).where(Board.source_id.in_(["weibo", "douyin"])))
        )
        assert {b.id for b in boards} == {"douyin-hot", "weibo-hot"}
        for board in boards:
            for snapshot in session.scalars(
                select(BoardSnapshot)
                .where(BoardSnapshot.board_id == board.id)
                .order_by(BoardSnapshot.observed_at)
            ):
                payload = session.get(RawPayload, snapshot.payload_id)
                parsed = (parse_weibo if board.source_id == "weibo" else parse_douyin)(
                    payload.data, snapshot.observed_at
                )
                result = serialize_snapshot(session, snapshot, board)
                assert len(parsed.items) == len(result["items"]) == snapshot.item_count
                for source, saved in zip(parsed.items, result["items"], strict=True):
                    assert source.title == saved["title"] and source.original_url == saved["url"]
                    assert (
                        source.source_rank == saved["rank"] and source.metrics == saved["metrics"]
                    )
                    assert source.flags == saved["flags"]
                if snapshot.id in previous:
                    assert payload.sha256 == previous[snapshot.id]["raw_sha256"]
                    old_shape = {k: v for k, v in result.items() if k != "interval_seconds"}
                    body = json.dumps(old_shape, default=str, ensure_ascii=False, sort_keys=True)
                    assert (
                        hashlib.sha256(body.encode()).hexdigest() == previous[snapshot.id]["sha256"]
                    )
                records.append(
                    {
                        "board": board.id,
                        "snapshot": snapshot.id,
                        "observed_at": str(snapshot.observed_at),
                        "items": snapshot.item_count,
                        "raw_sha256": payload.sha256,
                        "interval_seconds": snapshot.interval_seconds,
                    }
                )
        assert set(previous).issubset({r["snapshot"] for r in records})
        settings = [
            {"board": b.id, "enabled": b.enabled, "interval_seconds": b.interval_seconds}
            for b in boards
        ]
        changes = [
            {
                "board": c.board_id,
                "changed_at": str(c.changed_at),
                "before": c.before,
                "after": c.after,
            }
            for c in session.scalars(
                select(SourceSettingChange).order_by(SourceSettingChange.changed_at)
            )
        ]
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "stage": "M2",
        "raw_response_trace": "passed",
        "preserved_m1_snapshots": len(previous),
        "settings": settings,
        "changes": changes,
        "snapshots": records,
        "stability_24h": "not yet verified",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/M2-live-verification.json")
    args = parser.parse_args()
    report = verify()
    path = root / args.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"Raw trace passed: {len(report['snapshots'])} snapshots; "
        f"M1 preserved: {report['preserved_m1_snapshots']}. Report: {path}"
    )
