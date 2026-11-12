from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text

from hotradar import trends
from hotradar.db.session import session_factory

router = APIRouter()


def dates(start, end):
    now = datetime.now(UTC)
    end = end or now.astimezone(ZoneInfo("Asia/Shanghai")).date()
    start = start or end - timedelta(days=6)
    try:
        trends.window_bounds(start, end, now)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return start, end, now


@router.get("/api/v1/trends")
def overview(
    start: Annotated[date | None, Query()] = None, end: Annotated[date | None, Query()] = None
):
    start, end, now = dates(start, end)
    with session_factory()() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return trends.overview(session, start, end, now)


@router.get("/api/v1/trends/events/{event_id}")
def event_trend(
    event_id: UUID,
    start: Annotated[date | None, Query()] = None,
    end: Annotated[date | None, Query()] = None,
):
    start, end, now = dates(start, end)
    with session_factory()() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        try:
            return trends.event_series(session, str(event_id), start, end, now)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from None
