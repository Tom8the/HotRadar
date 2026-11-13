from datetime import date, datetime
from importlib.metadata import version
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError

from hotradar.api.contents import router as contents_router
from hotradar.api.events import router as events_router
from hotradar.api.platform_sessions import router as sessions_router
from hotradar.api.queries import dashboard, day_bounds, serialize_snapshot
from hotradar.api.source_management import router as management_router
from hotradar.api.trends import router as trends_router
from hotradar.collectors.douyin_city import NATIONAL_BOARD
from hotradar.db.models import Board, BoardSnapshot
from hotradar.db.session import session_factory
from hotradar.platform_browser import browser_lifespan

app = FastAPI(title="HotRadar", version=version("hotradar"), lifespan=browser_lifespan)
app.include_router(management_router)
app.include_router(events_router)
app.include_router(trends_router)
app.include_router(contents_router)
app.include_router(sessions_router)


@app.exception_handler(SQLAlchemyError)
@app.exception_handler(RuntimeError)
async def database_unavailable(_request, _error):
    return JSONResponse(status_code=503, content={"detail": "HotRadar database unavailable."})


@app.get("/api/health")
def health():
    try:
        with session_factory()() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ok", "service": "HotRadar", "stage": "M4", "database": "connected"}
    except (SQLAlchemyError, RuntimeError):
        raise HTTPException(503, "HotRadar database unavailable; run local setup.") from None


@app.get("/api/v1/boards")
def get_boards(
    day: Annotated[date | None, Query(alias="date")] = None,
    board_id: Annotated[str | None, Query(alias="board")] = None,
):
    selected = day or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    try:
        with session_factory()() as session:
            data = dashboard(session, selected)
            if board_id is not None:
                if board_id not in {
                    "baidu-hot",
                    "douyin-hot",
                    NATIONAL_BOARD,
                    "douyin-wuxi",
                    "douyin-wuxi-app",
                    "weibo-wuxi",
                    "weibo-hot",
                }:
                    raise HTTPException(404, "Board unavailable.")
                board = next((b for b in data["boards"] if b["id"] == board_id), None)
                if board is None:
                    raise HTTPException(404, "Board not registered.")
                return {"board": board, "read_at": data["read_at"], "selected_date": selected}
            return data
    except (SQLAlchemyError, RuntimeError):
        raise HTTPException(503, "Database unavailable; snapshots have not been read.") from None


@app.get("/api/v1/sources")
def get_sources():
    with session_factory()() as session:
        data = dashboard(session, datetime.now(ZoneInfo("Asia/Shanghai")).date())
        return [
            {
                "id": b["source_id"],
                "name": b["source_name"],
                "url": b["source_url"],
                "state": b["state"],
                "last_success_at": b["last_success_at"],
                "interval_seconds": b["interval_seconds"],
            }
            for b in data["boards"]
        ]


@app.get("/api/v1/boards/{board_id}/latest")
def latest(board_id: str):
    with session_factory()() as session:
        board = session.get(Board, board_id)
        if board is None:
            raise HTTPException(404, "Board not registered.")
        snapshot = session.scalar(
            select(BoardSnapshot)
            .where(
                BoardSnapshot.board_id == board_id,
            )
            .order_by(BoardSnapshot.observed_at.desc())
            .limit(1)
        )
        return {
            "board_id": board_id,
            "snapshot": (serialize_snapshot(session, snapshot, board) if snapshot else None),
        }


@app.get("/api/v1/boards/{board_id}/snapshots")
def snapshots(
    board_id: str,
    day: Annotated[date | None, Query(alias="date")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
):
    with session_factory()() as session:
        if session.get(Board, board_id) is None:
            raise HTTPException(404, "Board not registered.")
        query = select(BoardSnapshot).where(BoardSnapshot.board_id == board_id)
        if day is not None:
            start, end = day_bounds(day)
            query = query.where(BoardSnapshot.observed_at >= start, BoardSnapshot.observed_at < end)
        rows = session.scalars(query.order_by(BoardSnapshot.observed_at.desc()).limit(limit))
        return {
            "board_id": board_id,
            "snapshots": [
                {
                    "id": row.id,
                    "observed_at": row.observed_at,
                    "item_count": row.item_count,
                    "status": row.status,
                    "from_cache": row.from_cache,
                }
                for row in rows
            ],
        }


@app.get("/api/v1/boards/{board_id}/snapshots/{snapshot_id}")
def snapshot_detail(board_id: str, snapshot_id: str):
    with session_factory()() as session:
        board = session.get(Board, board_id)
        snapshot = session.get(BoardSnapshot, snapshot_id)
        if board is None or snapshot is None or snapshot.board_id != board_id:
            raise HTTPException(404, "Snapshot not found.")
        return {"board_id": board_id, "snapshot": serialize_snapshot(session, snapshot, board)}
