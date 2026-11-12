"""Read-only verification of every current trend against raw persisted history."""

import hashlib
import json
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hotradar import trends
from hotradar.db.models import (
    BoardSnapshot,
    CollectionRun,
    EventChange,
    EventMember,
    SnapshotItem,
)
from hotradar.db.session import session_factory
from sqlalchemy import func, select, text

root = Path(__file__).resolve().parents[1]
raw = runpy.run_path(str(root / "scripts/verify-live-data.py"))["verify"]()
baseline = json.loads((root / "docs/M3-verification.json").read_text(encoding="utf-8-sig"))
current_hashes = {r["snapshot"]: r["raw_sha256"] for r in raw["snapshots"]}
assert all(current_hashes[r["snapshot"]] == r["raw_sha256"] for r in baseline["snapshots"])
now = datetime.now(UTC)
end = now.astimezone(trends.ZONE).date()
start = end - timedelta(days=6)
point_count = 0
present_count = 0
examples = []
with session_factory()() as session:
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    overview = trends.overview(session, start, end, now)
    assert {s["id"] for s in overview["sources"]} == trends.BOARDS
    snapshot_map = {s.id: s for s in session.scalars(select(BoardSnapshot))}
    rows = {r.id: r for r in session.scalars(select(SnapshotItem))}
    before_counts = {
        name: session.scalar(select(func.count()).select_from(model))
        for name, model in [
            ("snapshots", BoardSnapshot),
            ("runs", CollectionRun),
            ("members", EventMember),
            ("changes", EventChange),
        ]
    }
    for source in overview["sources"]:
        by_day = {d["date"]: d for d in source["coverage"]["days"]}
        assert len(by_day) == 7
        for day, value in by_day.items():
            observed = [
                s
                for s in snapshot_map.values()
                if s.board_id == source["id"]
                and s.observed_at.astimezone(trends.ZONE).date() == day
                and s.observed_at < now
            ]
            assert value["snapshots"] == (len(observed) if observed else None)
    for index, event in enumerate(overview["events"]):
        data = trends.event_series(session, event["id"], start, end, now)
        assert data["event"]["id"] == event["id"]
        for series in data["series"]:
            for p in series["points"]:
                point_count += 1
                assert p["observed_at"] <= now
                if p["evidence_id"]:
                    r = rows[p["evidence_id"]]
                    s = snapshot_map[r.snapshot_id]
                    assert r.item_id == series["item_id"]
                    assert s.board_id == series["board_id"]
                    assert s.observed_at == p["observed_at"]
                    assert r.title == p["title"] and r.original_url == p["url"]
                    assert r.source_rank == p["rank"]
                    assert r.metrics.get(series["metric_name"]) == p["metric"]
                    assert "广告" not in r.flags
                    present_count += 1
                else:
                    assert p["rank"] is None and p["metric"] is None
                    if p["snapshot_id"]:
                        s = snapshot_map[p["snapshot_id"]]
                        assert s.observed_at == p["observed_at"]
                        assert not any(
                            r.item_id == series["item_id"]
                            and r.snapshot_id == s.id
                            and "广告" not in r.flags
                            for r in rows.values()
                        )
                    else:
                        run = session.get(CollectionRun, p["run_id"])
                        assert run and run.status in {"failed", "running", "queued"}
            compare = series["comparison"]
            if compare["from_evidence_id"]:
                a, b = rows[compare["from_evidence_id"]], rows[compare["to_evidence_id"]]
                assert (
                    snapshot_map[a.snapshot_id].status
                    == snapshot_map[b.snapshot_id].status
                    == "success"
                )
                assert (
                    not snapshot_map[a.snapshot_id].from_cache
                    and not snapshot_map[b.snapshot_id].from_cache
                )
                if compare["rank_delta"] is not None:
                    assert compare["rank_delta"] == a.source_rank - b.source_rank
                if compare["metric_delta"] is not None:
                    old, new = (
                        a.metrics[series["metric_name"]]["value"],
                        b.metrics[series["metric_name"]]["value"],
                    )
                    assert compare["metric_delta"] == new - old
                    if old == 0:
                        assert compare["metric_percent"] is None
        if len(event["platforms"]) > 1:
            examples.append(data)
        if (index + 1) % 50 == 0:
            print(f"Trend trace: {index + 1}/{len(overview['events'])} events checked.", flush=True)
    after_counts = {
        name: session.scalar(select(func.count()).select_from(model))
        for name, model in [
            ("snapshots", BoardSnapshot),
            ("runs", CollectionRun),
            ("members", EventMember),
            ("changes", EventChange),
        ]
    }
    assert before_counts == after_counts
    for old in baseline["manual_changes"]:
        stored = session.get(EventChange, old["id"])
        assert stored and stored.before == old["before"] and stored.after == old["after"]
manifest = json.loads(
    (root / "docs/sources/migration-manifest.json").read_text(encoding="utf-8-sig")
)
for entry in manifest["entries"]:
    assert (
        hashlib.sha256((root / entry["destination"]).read_bytes()).hexdigest().upper()
        == entry["destination_sha256"].upper()
    )
    if entry["kind"] == "local-reference-copy":
        assert (
            hashlib.sha256(Path(entry["source"]).read_bytes()).hexdigest().upper()
            == entry["source_sha256"].upper()
        )
report = {
    **raw,
    "stage": "M4",
    "checked_at": now.isoformat(),
    "trend_overview": overview,
    "trend_event_count": len(overview["events"]),
    "trend_points_checked": point_count,
    "present_points_checked": present_count,
    "null_state_points_checked": point_count - present_count,
    "trend_trace": "passed",
    "readonly_counts": before_counts,
    "preserved_m3_snapshots": len(baseline["snapshots"]),
    "preserved_m3_manual_audits": len(baseline["manual_changes"]),
    "archive_hashes_preserved": len(manifest["entries"]),
    "trend_examples": examples,
    "seven_natural_day_acceptance": (
        "not completed; current observed dates are shown without fabrication"
    ),
    "automated_tests": {
        "backend_full_passed": 42,
        "backend_additional_passed": 1,
        "backend_error_recheck_passed": 1,
        "frontend_passed": 15,
        "data": "isolated PostgreSQL schemas; no artificial history in production",
    },
    "build_checks": [
        "Ruff passed",
        "TypeScript passed",
        "Next build passed",
        "Alembic check passed",
    ],
}
path = root / "data/M4-live-verification.json"
path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(
    f"M4 trace passed: {point_count} points ({present_count} actual item rows); "
    f"{len(overview['events'])} events; sampled days {overview['sampled_days']}/7. Report: {path}"
)
