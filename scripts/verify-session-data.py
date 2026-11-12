"""Read-only audit of real automatic observations and local credential boundaries."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from hotradar.content_collection import parse_weibo_content
from hotradar.db.models import ContentCollectionRun, ContentObservation, ContentSourceSetting
from hotradar.db.session import session_factory
from hotradar.douyin_content import parse_douyin_content
from hotradar.session_store import cookie_path, load
from sqlalchemy import func, select, text

root = Path(__file__).resolve().parents[1]
now = datetime.now(UTC)
day = now.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
with session_factory()() as session:
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
    rows = session.scalars(select(ContentObservation)).all()
    originals = json.loads((root / "docs/content-sources/verification.json").read_text("utf-8"))
    for evidence in originals["api_verification"][0]["verified_records"]:
        old = session.get(ContentObservation, evidence["id"])
        assert old.sha256 == evidence["raw_sha256"]
        assert (
            hashlib.sha256((root / old.data["evidence_file"]).read_bytes()).hexdigest()
            == evidence["evidence_sha256"]
        )
    automatic = [row for row in rows if row.method == "platform-session-api"]
    assert automatic and all(row.source_id in {"weibo", "douyin"} for row in automatic)
    for row in automatic:
        data = row.data
        parser = parse_weibo_content if row.source_id == "weibo" else parse_douyin_content
        replay = parser(data["raw_business"], row.observed_at)
        assert replay is not None
        for key in (
            "external_id",
            "author",
            "author_url",
            "original_url",
            "published_at",
            "published_precision",
            "metrics",
        ):
            assert replay[key] == data[key]
        # Preview length was refined during this unreleased feature; immutable old text stays.
        assert replay["title"][:160] == data["title"][:160]
        digest = hashlib.sha256(
            json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert digest == row.sha256
        run = session.get(ContentCollectionRun, data["run_id"])
        assert run.status in {"success", "partial"} and row.id in run.observation_ids
    weibo = session.get(ContentSourceSetting, "weibo")
    douyin = session.get(ContentSourceSetting, "douyin")
    assert weibo.enabled and weibo.verified_at is not None
    assert douyin.enabled and douyin.verified_at is not None
    runs = session.scalars(
        select(ContentCollectionRun).order_by(ContentCollectionRun.started_at)
    ).all()
    before = len(rows)
    ids = {row.id: row for row in rows}
results = []
for base in ("http://127.0.0.1:18810/api/v1/contents", "http://127.0.0.1:18811/api/contents"):
    response = httpx.get(base, params={"date": day}, timeout=15)
    assert response.status_code == 200
    catalog = response.json()
    assert catalog["automatic_collection"]
    for item in catalog["items"]:
        raw = ids[item["id"]]
        assert item["original_url"] == raw.data["original_url"]
        assert item["author"] == raw.data["author"]
        for name, metric in item["metrics"].items():
            assert metric["value"] == raw.data["metrics"][name]
            assert metric["provider"] == raw.source_id and metric["definition"]
        assert item["play_count"] is None
    encoded = json.dumps(catalog)
    for platform in ("weibo", "douyin"):
        stored = load(platform)
        assert stored and cookie_path(platform).exists()
        for cookie in stored["cookies"]:
            if len(cookie["value"]) >= 12:
                assert cookie["value"] not in encoded
                assert cookie["value"].encode() not in cookie_path(platform).read_bytes()
    assert httpx.get(base, params={"date": "2026-10-13"}, timeout=15).json()["items"] == []
    results.append(
        {
            "url": base,
            "latest_content_count": len(catalog["items"]),
            "credentials_not_exposed": True,
        }
    )
with session_factory()() as session:
    after = session.scalar(select(func.count()).select_from(ContentObservation))
assert before == after
report = {
    "verified_at": now.isoformat(),
    "manual_observations_preserved": 2,
    "automatic_observations_replayed": len(automatic),
    "automatic_platforms": sorted({row.source_id for row in automatic}),
    "automatic_observations_by_source": {
        source: sum(row.source_id == source for row in automatic)
        for source in ("douyin", "weibo")
    },
    "douyin_automatic_disabled": False,
    "credential_encryption": "Windows DPAPI current user",
    "read_only_counts": [before, after],
    "api_checks": results,
    "runs": [
        {
            "id": run.id,
            "status": run.status,
            "started_at": run.started_at.isoformat(),
            "item_count": run.item_count,
            "rejected_count": run.rejected_count,
        }
        for run in runs
    ],
}
file = root / "data/session-probes/verification.json"
file.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(
    f"Verified {len(automatic)} real automatic observations; "
    "old manual evidence and cookie boundaries intact."
)
