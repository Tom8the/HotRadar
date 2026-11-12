from datetime import date, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query
from sqlalchemy import text

from hotradar.contents import catalog
from hotradar.db.session import session_factory

router = APIRouter()


@router.get("/api/v1/contents")
def contents(day: Annotated[date | None, Query(alias="date")] = None):
    selected = day or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    with session_factory()() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return catalog(session, selected)
