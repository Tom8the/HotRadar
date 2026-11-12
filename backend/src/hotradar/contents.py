"""Read-only catalog of immutable reviewed and verified normal-session observations."""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, StrictInt, field_validator, model_validator
from sqlalchemy import select, text

from hotradar.api.queries import day_bounds
from hotradar.db.models import ContentObservation

VERSION = "content-browser-evidence-v1"
METRICS = {
    "douyin": {
        "likes": ("点赞", "页面爱心数"),
        "comments": ("评论", "页面评论数"),
        "favorites": ("收藏", "页面收藏数"),
        "shares": ("分享", "页面分享数"),
    },
    "weibo": {
        "likes": ("点赞", "页面赞数"),
        "comments": ("评论", "页面评论数"),
        "reposts": ("转发", "页面转发数"),
    },
}


class BrowserSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: Literal["douyin", "weibo"]
    external_id: str
    item_kind: Literal["video", "post"]
    original_url: str
    title: str
    author: str
    author_url: str
    published_at_display: str
    published_timezone: Literal["Asia/Shanghai"]
    published_precision: Literal["minute"]
    observed_at: datetime
    metrics: dict[str, StrictInt | None]
    discovery_label: str
    discovery_url: str
    evidence_file: str
    evidence_sha256: str
    verification_note: str

    @field_validator("title", "author", "discovery_label", "verification_note")
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError("A verified field is missing.")
        return value.strip()

    @model_validator(mode="after")
    def validate_evidence(self):
        if self.observed_at.tzinfo is None or self.observed_at > datetime.now(UTC):
            raise ValueError("Observation requires a real past timestamp with timezone.")
        published = datetime.strptime(self.published_at_display, "%Y-%m-%d %H:%M").replace(
            tzinfo=ZoneInfo(self.published_timezone)
        )
        if published > self.observed_at:
            raise ValueError("Publication is after observation.")
        parts = urlsplit(self.original_url)
        author = urlsplit(self.author_url)
        discover = urlsplit(self.discovery_url)
        allowed = (
            {"www.douyin.com"} if self.source_id == "douyin" else {"weibo.com", "www.weibo.com"}
        )
        for url in (parts, author, discover):
            if (
                url.scheme != "https"
                or url.hostname not in allowed
                or url.username
                or url.password
                or url.port
                or url.query
                or url.fragment
            ):
                raise ValueError(
                    "Only platform HTTPS evidence URLs without credentials are accepted."
                )
        if self.source_id == "douyin":
            if (
                self.item_kind != "video"
                or not re.fullmatch(r"\d{15,25}", self.external_id)
                or parts.path != "/video/" + self.external_id
                or not author.path.startswith("/user/")
            ):
                raise ValueError("A Douyin topic URL is not a concrete video.")
        else:
            if (
                self.item_kind != "post"
                or not re.fullmatch(r"[A-Za-z0-9]+", self.external_id)
                or not re.fullmatch(r"/\d+/" + re.escape(self.external_id), parts.path)
                or not re.fullmatch(r"/u/\d+", author.path)
            ):
                raise ValueError("A Weibo search URL is not a concrete post.")
        if set(self.metrics) != set(METRICS[self.source_id]):
            raise ValueError("Interaction schema does not match the verified platform.")
        if not any(value is not None for value in self.metrics.values()):
            raise ValueError("At least one verified interaction is required.")
        if any(value is not None and value < 0 for value in self.metrics.values()):
            raise ValueError("Interaction counts cannot be negative.")
        if not re.fullmatch(r"[a-f0-9]{64}", self.evidence_sha256) or not re.fullmatch(
            r"docs/content-sources/screens/[a-z0-9-]+\.jpg", self.evidence_file
        ):
            raise ValueError("A local screenshot evidence hash is required.")
        return self


def import_sample(session, sample: BrowserSample):
    # Same source identity is serialized; repeated imports do not manufacture observations.
    session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"content:{sample.source_id}:{sample.external_id}"},
    )
    data = sample.model_dump(mode="json")
    digest = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    existing = session.scalar(select(ContentObservation).where(ContentObservation.sha256 == digest))
    if existing:
        return existing, False
    row = ContentObservation(
        source_id=sample.source_id,
        external_id=sample.external_id,
        observed_at=sample.observed_at,
        sha256=digest,
        method="manual-browser-review",
        version=VERSION,
        data=data,
    )
    session.add(row)
    session.flush()
    return row, True


def catalog(session, selected: date):
    start, end = day_bounds(selected)
    rows = session.scalars(
        select(ContentObservation)
        .where(
            ContentObservation.observed_at >= start,
            ContentObservation.observed_at < end,
        )
        .distinct(ContentObservation.source_id, ContentObservation.external_id)
        .order_by(
            ContentObservation.source_id,
            ContentObservation.external_id,
            ContentObservation.observed_at.desc(),
            ContentObservation.id.desc(),
        )
    ).all()
    items = []
    for row in sorted(rows, key=lambda row: row.observed_at, reverse=True):
        item = {
            **row.data,
            "id": row.id,
            "method": row.method,
            "version": row.version,
            "raw_sha256": row.sha256,
        }
        item["metrics"] = {
            name: {
                "value": value,
                "label": METRICS[row.source_id][name][0],
                "unit": "次",
                "provider": row.source_id,
                "definition": row.data.get("metric_definitions", {}).get(name)
                or METRICS[row.source_id][name][1] + "，人工核查时原值，非播放量",
            }
            for name, value in row.data["metrics"].items()
        }
        item["play_count"] = None
        items.append(item)
    from hotradar.content_collection import source_status

    sources = source_status(session)
    return {
        "selected_date": selected,
        "read_at": datetime.now(UTC),
        "items": items,
        "collection_mode": "verified-platform-content",
        "sources": sources,
        "automatic_collection": any(source["enabled"] for source in sources),
        "selection_rule": "平台热门候选与人工核查样本；未定义爆款阈值，不代表全平台榜单。",
    }
