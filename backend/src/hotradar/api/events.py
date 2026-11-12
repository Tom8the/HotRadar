from datetime import UTC, date, datetime
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator
from sqlalchemy import func, select, text

from hotradar import events
from hotradar.api.queries import dashboard
from hotradar.db.models import (
    BoardSnapshot,
    Event,
    EventChange,
    EventMember,
    EventProcessedSnapshot,
)
from hotradar.db.session import session_factory

router = APIRouter()


class Correction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=2, max_length=300)

    @field_validator("reason")
    @classmethod
    def meaningful_reason(cls, value):
        if len(value.strip()) < 2:
            raise ValueError("填写修正原因。")
        return value.strip()


class Merge(Correction):
    target_id: UUID
    other_ids: list[UUID] = Field(min_length=1, max_length=10)
    revisions: dict[UUID, StrictInt]


class Split(Correction):
    event_id: UUID
    item_ids: list[UUID] = Field(min_length=1, max_length=100)
    revisions: dict[UUID, StrictInt]


class Restore(Correction):
    change_id: UUID


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    revision: StrictInt
    followed: StrictBool
    tags: list[str] = Field(max_length=12)

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, tags):
        clean = list(dict.fromkeys(t.strip() for t in tags))
        if any(not t or len(t) > 24 for t in clean):
            raise ValueError("标签须为1至24字。")
        return clean


def local(request):
    origin = request.headers.get("origin")
    if origin and origin not in {"http://127.0.0.1:18811", "http://127.0.0.1:18810"}:
        raise HTTPException(403, "Local workspace only.")


def selected_day(day):
    return day or datetime.now(ZoneInfo("Asia/Shanghai")).date()


@router.get("/api/v1/events")
def catalog(day: Annotated[date | None, Query(alias="date")] = None):
    day = selected_day(day)
    with session_factory()() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        result = events.event_catalog(session, day)
        pending = session.scalar(
            select(func.count())
            .select_from(BoardSnapshot)
            .where(
                BoardSnapshot.board_id.in_(events.BOARDS),
                ~BoardSnapshot.id.in_(select(EventProcessedSnapshot.snapshot_id)),
            )
        )
        return {
            "selected_date": day,
            "read_at": datetime.now(UTC),
            "events": result,
            "pending_snapshots": pending,
            "algorithm": events.ALGORITHM,
            "sources": [
                {
                    "id": b["id"],
                    "name": b["source_name"],
                    "state": b["state"],
                    "last_success_at": b["last_success_at"],
                }
                for b in dashboard(session, day)["boards"]
                if b["id"] in events.BOARDS
            ],
            "coverage_note": (
                "按选定日期实际观测关联；历史日期采用当前修正关系。"
                "跨平台按独立平台计数，热度不相加。"
            ),
        }


@router.get("/api/v1/events/{event_id}")
def detail(event_id: UUID, day: Annotated[date | None, Query(alias="date")] = None):
    with session_factory()() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        event_id = str(event_id)
        catalog = events.event_catalog(session, selected_day(day))
        chosen = next((e for e in catalog if e["id"] == event_id), None)
        if chosen is None:
            raise HTTPException(404, "该日期没有此事件的观测记录。")
        all_rows = events.evidence_rows(session, event_ids={event_id})
        _, all_evidence = events.summarize(session.get(Event, event_id), all_rows)
        candidates = []
        for other in catalog:
            if other["id"] == event_id:
                continue
            pairs = [
                events.similarity(a["title"], b["title"])
                for a in chosen["evidence"]
                for b in other["evidence"]
            ]
            score, reason = max(pairs)
            if score >= 0.45:
                candidates.append(
                    {
                        "id": other["id"],
                        "title": other["title"],
                        "score": score,
                        "reason": reason,
                        "revision": other["revision"],
                    }
                )
        candidates.sort(key=lambda c: (-c["score"], c["id"]))
        changes = list(
            session.scalars(
                select(EventChange)
                .where(
                    EventChange.after.has_key(event_id),  # noqa: W601 -- PostgreSQL JSONB operator
                )
                .order_by(EventChange.changed_at.desc())
                .limit(30)
            )
        )
        return {
            "event": chosen,
            "all_evidence": all_evidence,
            "candidates": candidates[:8],
            "changes": [
                {
                    "id": c.id,
                    "kind": c.kind,
                    "reason": c.reason,
                    "changed_at": c.changed_at,
                    "reverted_by": c.reverted_by,
                    "before": c.before,
                    "after": c.after,
                    "can_restore": c.kind in {"merge", "split"}
                    and not c.reverted_by
                    and events.capture(session, set(c.after)) == c.after,
                }
                for c in changes
            ],
        }


def mutation(request, action):
    local(request)
    try:
        with session_factory().begin() as session:
            change = action(session)
            return {"change_id": change.id, "kind": change.kind, "saved": True}
    except events.EventConflict as exc:
        raise HTTPException(409, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None


@router.post("/api/v1/events/merge")
def merge(body: Merge, request: Request):
    return mutation(
        request,
        lambda s: events.merge(
            s,
            str(body.target_id),
            [str(i) for i in body.other_ids],
            {str(k): v for k, v in body.revisions.items()},
            body.reason,
        ),
    )


@router.post("/api/v1/events/split")
def split(body: Split, request: Request):
    return mutation(
        request,
        lambda s: events.split(
            s,
            str(body.event_id),
            [str(i) for i in body.item_ids],
            {str(k): v for k, v in body.revisions.items()},
            body.reason,
        ),
    )


@router.post("/api/v1/events/restore")
def restore(body: Restore, request: Request):
    return mutation(request, lambda s: events.restore(s, str(body.change_id), body.reason))


@router.post("/api/v1/events/metadata")
def metadata(body: Metadata, request: Request):
    def action(session):
        events.lock_events(session)
        event_id = str(body.event_id)
        events.require_versions(session, {event_id: body.revision}, {event_id})
        before = events.capture(session, {event_id})
        event = session.get(Event, event_id)
        if not session.scalar(select(EventMember.item_id).where(EventMember.event_id == event_id)):
            raise events.EventConflict("空事件无法设置关注。")
        event.followed = body.followed
        event.tags = body.tags
        event.revision += 1
        return events.audit(session, "metadata", "更新事件关注与标签", before, {event_id})

    return mutation(request, action)
