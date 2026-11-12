from dataclasses import dataclass, field
from datetime import datetime


class CollectionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CollectedItem:
    external_id: str
    title: str
    original_url: str
    source_rank: int | None
    display_order: int
    metrics: dict
    flags: list[str] = field(default_factory=list)
    is_pinned: bool = False
    published_at: datetime | None = None
    source_event_at: datetime | None = None


@dataclass(frozen=True)
class CollectionBatch:
    items: list[CollectedItem]
    raw_payload: dict
    observed_at: datetime
    parser_version: str
    upstream_updated_at: datetime | None = None
    from_cache: bool = False
    rejected_count: int = 0
