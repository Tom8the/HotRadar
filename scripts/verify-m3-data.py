"""Read-only M3 audit of real evidence, summaries, corrections and retained originals."""

import hashlib
import json
import runpy
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from hotradar import events
from hotradar.db.models import (
    Board,
    BoardSnapshot,
    EventChange,
    EventMember,
    EventProcessedSnapshot,
    EventSummary,
    Item,
    SnapshotItem,
)
from hotradar.db.session import session_factory
from sqlalchemy import func, select, text

root = Path(__file__).resolve().parents[1]
raw = runpy.run_path(str(root / "scripts/verify-live-data.py"))["verify"]()
m2 = json.loads((root / "docs/M2-verification.json").read_text(encoding="utf-8-sig"))
current_raw = {r["snapshot"]: r["raw_sha256"] for r in raw["snapshots"]}
assert all(current_raw[r["snapshot"]] == r["raw_sha256"] for r in m2["snapshots"])
day = datetime.now(ZoneInfo("Asia/Shanghai")).date()
with session_factory()() as s:
    s.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    catalog = events.event_catalog(s, day)
    evidence_count = 0
    for event in catalog:
        assert event["total_members"] >= len(event["evidence"])
        assert set(event["platforms"]) <= {"douyin", "weibo"}
        summary = event["summary"]
        assert summary["provider"] == "local-extractive" and summary["model"] is None
        assert summary["input_ids"] == [e["id"] for e in event["evidence"]]
        record = s.get(EventSummary, summary["record_id"])
        assert record and record.signature == summary["signature"]
        assert record.evidence_ids == summary["input_ids"]
        for line, evidence in zip(summary["lines"], event["evidence"], strict=True):
            row = s.get(SnapshotItem, evidence["id"])
            snapshot = s.get(BoardSnapshot, row.snapshot_id)
            assert line["evidence_id"] == row.id and row.title in line["text"]
            assert row.title == evidence["title"] and row.original_url == evidence["url"]
            assert row.source_rank == evidence["rank"] and row.metrics == evidence["metrics"]
            assert snapshot.observed_at.isoformat() == evidence["observed_at"]
            assert s.get(Item, row.item_id).item_kind == "topic"
            assert "广告" not in row.flags
            evidence_count += 1
    for member in s.scalars(select(EventMember)):
        assert s.get(Item, member.item_id).board_id in events.BOARDS
        for key in ("input_evidence_id", "matched_evidence_id"):
            if key in member.basis:
                assert s.get(SnapshotItem, member.basis[key]) is not None
    changes = list(s.scalars(select(EventChange).order_by(EventChange.changed_at)))
    for change in changes:
        for state in (change.before, change.after):
            for event in state.values():
                assert event["revision"] >= 1
                assert all(s.get(Item, item_id) for item_id in event["members"])
        if change.reverted_by:
            assert s.get(EventChange, change.reverted_by).kind == "restore"
    processed = set(s.scalars(select(EventProcessedSnapshot.snapshot_id)))
    snapshots = set(
        s.scalars(select(BoardSnapshot.id).where(BoardSnapshot.board_id.in_(events.BOARDS)))
    )
    assert snapshots == processed
    settings = [
        {"id": b.id, "enabled": b.enabled, "interval_seconds": b.interval_seconds}
        for b in s.scalars(select(Board).where(Board.id.in_(events.BOARDS)))
    ]
    assert len(settings) == 2
    summary_count = s.scalar(select(func.count(EventSummary.id)))
    membership_count = s.scalar(select(func.count(EventMember.item_id)))
    snapshot_fingerprint = hashlib.sha256(
        json.dumps(
            [
                {
                    "id": row.id,
                    "snapshot": row.snapshot_id,
                    "title": row.title,
                    "url": row.original_url,
                    "rank": row.source_rank,
                    "metrics": row.metrics,
                }
                for row in s.scalars(select(SnapshotItem).order_by(SnapshotItem.id))
            ],
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
manifest = json.loads(
    (root / "docs/sources/migration-manifest.json").read_text(encoding="utf-8-sig")
)
for entry in manifest["entries"]:
    destination = root / entry["destination"]
    assert (
        hashlib.sha256(destination.read_bytes()).hexdigest().upper()
        == entry["destination_sha256"].upper()
    )
    if entry["kind"] == "local-reference-copy":
        assert (
            hashlib.sha256(Path(entry["source"]).read_bytes()).hexdigest().upper()
            == entry["source_sha256"].upper()
        )
report = {
    **raw,
    "stage": "M3",
    "checked_at": datetime.now(UTC).isoformat(),
    "selected_date": str(day),
    "snapshot_count": len(snapshots),
    "preserved_m2_snapshots": len(m2["snapshots"]),
    "processed_snapshots": len(processed),
    "source_settings": settings,
    "archive_hashes_preserved": len(manifest["entries"]),
    "events": len(catalog),
    "members": membership_count,
    "cross_platform_events": len([e for e in catalog if len(e["platforms"]) > 1]),
    "source_evidence_checked": evidence_count,
    "summary_versions": summary_count,
    "snapshot_items_sha256": snapshot_fingerprint,
    "event_evidence_trace": "passed",
    "summary_input_trace": "passed",
    "cross_platform_examples": [e for e in catalog if len(e["platforms"]) > 1],
    "manual_changes": [
        {
            "id": c.id,
            "kind": c.kind,
            "reason": c.reason,
            "changed_at": c.changed_at.isoformat(),
            "reverted_by": c.reverted_by,
            "before": c.before,
            "after": c.after,
        }
        for c in changes
    ],
    "concrete_contents": "not connected; no real content samples available",
}
path = root / "data/M3-live-verification.json"
path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    f"M3 trace passed: {len(catalog)} events, {evidence_count} evidence rows, "
    f"{len(snapshots)} snapshots, {len(changes)} manual audit entries. Report: {path}"
)
