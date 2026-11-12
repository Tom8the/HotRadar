from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    platform: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(80))
    home_url: Mapped[str] = mapped_column(Text)
    access_method: Mapped[str] = mapped_column(String(80))


class Board(Base):
    __tablename__ = "boards"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    name: Mapped[str] = mapped_column(String(100))
    item_kind: Mapped[str] = mapped_column(String(20), default="topic")
    metric_name: Mapped[str] = mapped_column(String(50))
    metric_label: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=900)
    parser_version: Mapped[str] = mapped_column(String(40))
    __table_args__ = (CheckConstraint("interval_seconds >= 60"),)


class CollectionRun(Base):
    __tablename__ = "collection_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"), index=True)
    job_key: Mapped[str] = mapped_column(String(180), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    item_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(60))
    error_message: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(40))
    __table_args__ = (
        CheckConstraint("attempt_count >= 0 AND item_count >= 0"),
        Index("ix_runs_board_created", "board_id", "created_at"),
    )


class RawPayload(Base):
    __tablename__ = "raw_payloads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"))
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("board_id", "sha256"),)


class BoardSnapshot(Base):
    __tablename__ = "board_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("collection_runs.id"), unique=True)
    payload_id: Mapped[str] = mapped_column(ForeignKey("raw_payloads.id"))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    upstream_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    item_count: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    from_cache: Mapped[bool] = mapped_column(Boolean, default=False)
    parser_version: Mapped[str] = mapped_column(String(40))
    interval_seconds: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (Index("ix_snapshots_board_observed", "board_id", "observed_at"),)


class Item(Base):
    __tablename__ = "items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"))
    external_id: Mapped[str] = mapped_column(Text)
    item_kind: Mapped[str] = mapped_column(String(20))
    canonical_url: Mapped[str] = mapped_column(Text)
    original_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    first_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("board_id", "external_id"),)


class SnapshotItem(Base):
    __tablename__ = "snapshot_items"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("board_snapshots.id"))
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"))
    title: Mapped[str] = mapped_column(Text)
    original_url: Mapped[str] = mapped_column(Text)
    source_rank: Mapped[int | None] = mapped_column(Integer)
    display_order: Mapped[int] = mapped_column(Integer)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metrics: Mapped[dict] = mapped_column(JSONB)
    flags: Mapped[list] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("snapshot_id", "item_id"),
        Index("ix_snapshot_items_snapshot_order", "snapshot_id", "display_order"),
    )


class SourceSettingChange(Base):
    __tablename__ = "source_setting_changes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    board_id: Mapped[str] = mapped_column(ForeignKey("boards.id"), index=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    before: Mapped[dict] = mapped_column(JSONB)
    after: Mapped[dict] = mapped_column(JSONB)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    manual_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    followed: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list] = mapped_column(JSONB, default=list)
    __table_args__ = (CheckConstraint("revision >= 1"),)


class EventMember(Base):
    __tablename__ = "event_members"
    item_id: Mapped[str] = mapped_column(ForeignKey("items.id"), primary_key=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    basis: Mapped[dict] = mapped_column(JSONB)


class EventChange(Base):
    __tablename__ = "event_changes"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(Text)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    before: Mapped[dict] = mapped_column(JSONB)
    after: Mapped[dict] = mapped_column(JSONB)
    reverted_by: Mapped[str | None] = mapped_column(String(36))


class EventSummary(Base):
    __tablename__ = "event_summaries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    signature: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(60))
    version: Mapped[str] = mapped_column(String(40))
    evidence_ids: Mapped[list] = mapped_column(JSONB)
    data: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("event_id", "signature"),)


class EventProcessedSnapshot(Base):
    __tablename__ = "event_processed_snapshots"
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("board_snapshots.id"), primary_key=True)
    version: Mapped[str] = mapped_column(String(40))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContentObservation(Base):
    """Immutable, independently verified content evidence; never a topic snapshot."""

    __tablename__ = "content_observations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"))
    external_id: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    method: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict] = mapped_column(JSONB)
    __table_args__ = (
        CheckConstraint("source_id IN ('douyin', 'weibo')"),
        Index("ix_content_source_external_observed", "source_id", "external_id", "observed_at"),
    )


class ContentSourceSetting(Base):
    __tablename__ = "content_source_settings"
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification: Mapped[dict] = mapped_column(JSONB, default=dict)
    __table_args__ = (
        CheckConstraint("source_id IN ('douyin', 'weibo') AND interval_seconds >= 900"),
    )


class ContentCollectionRun(Base):
    __tablename__ = "content_collection_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source_id: Mapped[str] = mapped_column(ForeignKey("sources.id"), index=True)
    job_key: Mapped[str] = mapped_column(String(180), unique=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    item_count: Mapped[int | None] = mapped_column(Integer)
    rejected_count: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(60))
    version: Mapped[str] = mapped_column(String(40))
    observation_ids: Mapped[list] = mapped_column(JSONB, default=list)
