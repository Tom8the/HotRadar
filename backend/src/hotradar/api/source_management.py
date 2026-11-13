from datetime import UTC, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StrictBool, model_validator
from sqlalchemy import select, text

from hotradar.api.queries import dashboard
from hotradar.collectors.douyin_city import NATIONAL_BOARD
from hotradar.content_collection import source_status
from hotradar.db.models import (
    Board,
    BoardSnapshot,
    CollectionRun,
    ContentSourceSetting,
    SourceSettingChange,
)
from hotradar.db.session import session_factory

router = APIRouter()
BOARD_IDS = {
    "weibo-hot",
    "douyin-hot",
    NATIONAL_BOARD,
    "douyin-wuxi",
    "douyin-wuxi-app",
    "weibo-wuxi",
    "baidu-hot",
}
MANAGED_BOARD_IDS = BOARD_IDS - {"douyin-wuxi"}


class SourceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool | None = None
    interval_seconds: Literal[900, 1800, 3600] | None = None

    @model_validator(mode="after")
    def has_setting(self):
        if self.enabled is None and self.interval_seconds is None:
            raise ValueError("At least one setting is required.")
        return self


@router.get("/api/v1/source-management")
def source_management():
    now = datetime.now(UTC)
    since = now - timedelta(hours=24)
    with session_factory()() as session:
        current = dashboard(session, now.astimezone(ZoneInfo("Asia/Shanghai")).date())
        result = []
        for board in current["boards"]:
            if board["id"] not in MANAGED_BOARD_IDS:
                continue
            runs = list(
                session.scalars(
                    select(CollectionRun)
                    .where(CollectionRun.board_id == board["id"], CollectionRun.created_at >= since)
                    .order_by(CollectionRun.created_at.desc())
                )
            )
            first = session.scalar(
                select(BoardSnapshot.observed_at)
                .where(BoardSnapshot.board_id == board["id"])
                .order_by(BoardSnapshot.observed_at)
                .limit(1)
            )
            changes = list(
                session.scalars(
                    select(SourceSettingChange)
                    .where(SourceSettingChange.board_id == board["id"])
                    .order_by(SourceSettingChange.changed_at.desc())
                    .limit(5)
                )
            )
            result.append(
                {
                    **{key: value for key, value in board.items() if key != "snapshot"},
                    "snapshot_items": board["snapshot"]["item_count"]
                    if board["snapshot"]
                    else None,
                    "first_observed_at": first,
                    "window_start": since,
                    "window": {
                        "total": len(runs),
                        "success": sum(r.status in {"success", "empty"} for r in runs),
                        "partial": sum(r.status == "partial" for r in runs),
                        "failed": sum(r.status == "failed" for r in runs),
                        "unfinished": sum(r.status in {"running", "queued"} for r in runs),
                    },
                    "recent_runs": [
                        {
                            "id": r.id,
                            "created_at": r.created_at,
                            "status": r.status,
                            "attempt_count": r.attempt_count,
                            "error_code": r.error_code,
                            "item_count": r.item_count
                            if r.status in {"success", "partial", "empty"}
                            else None,
                        }
                        for r in runs[:8]
                    ],
                    "changes": [
                        {
                            "id": c.id,
                            "changed_at": c.changed_at,
                            "before": c.before,
                            "after": c.after,
                        }
                        for c in changes
                    ],
                }
            )
        return {
            "sources": result,
            "content_sources": source_status(session, ("douyin",)),
            "read_at": now,
            "coverage_note": "统计最近24小时已记录任务；采样不足24小时不代表连续稳定性通过。",
        }


def require_local_origin(request: Request):
    # Direct browser requests from foreign sites cannot mutate the loopback API.
    origin = request.headers.get("origin")
    if origin and origin not in {"http://127.0.0.1:18811", "http://127.0.0.1:18810"}:
        raise HTTPException(403, "Local workspace only.")


@router.patch("/api/v1/boards/{board_id}/settings")
def change_settings(board_id: str, settings: SourceSettings, request: Request):
    require_local_origin(request)
    if board_id not in BOARD_IDS:
        raise HTTPException(404, "Board not available in this release.")
    with session_factory().begin() as session:
        board = session.scalar(select(Board).where(Board.id == board_id).with_for_update())
        if board is None:
            raise HTTPException(404, "Board not registered.")
        before = {"enabled": board.enabled, "interval_seconds": board.interval_seconds}
        if settings.enabled is not None:
            board.enabled = settings.enabled
        if settings.interval_seconds is not None:
            board.interval_seconds = settings.interval_seconds
        after = {"enabled": board.enabled, "interval_seconds": board.interval_seconds}
        if before != after:
            session.add(SourceSettingChange(board_id=board.id, before=before, after=after))
        return {
            "board_id": board.id,
            **after,
            "changed": before != after,
            "note": "已暂停的来源可能仍完成在途请求，历史快照保留。",
        }


@router.patch("/api/v1/content-sources/douyin/settings")
def change_douyin_content_settings(settings: SourceSettings, request: Request):
    require_local_origin(request)
    with session_factory().begin() as session:
        locked = session.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
            {"key": "content-worker:douyin"},
        )
        if not locked:
            raise HTTPException(409, "collection_busy")
        config = session.get(ContentSourceSetting, "douyin", with_for_update=True)
        if config is None:
            raise HTTPException(404, "Content source not registered.")
        before = {"enabled": config.enabled, "interval_seconds": config.interval_seconds}
        if settings.enabled is True and not config.enabled:
            status = source_status(session, ("douyin",))[0]
            last_run = status["last_run"] or {}
            if (
                not status["verified_at"]
                or not status["session_saved_locally"]
                or last_run.get("error_code") in {"session_required", "upstream_rejected"}
            ):
                raise HTTPException(409, "session_required")
        if settings.enabled is not None:
            config.enabled = settings.enabled
        if settings.interval_seconds is not None:
            config.interval_seconds = settings.interval_seconds
        after = {"enabled": config.enabled, "interval_seconds": config.interval_seconds}
        if before != after:
            action = (
                "interval"
                if before["enabled"] == after["enabled"]
                else ("resume" if config.enabled else "pause")
            )
            config.verification = {
                **config.verification,
                "controls": [
                    *config.verification.get("controls", []),
                    {
                        "action": action,
                        "at": datetime.now(UTC).isoformat(),
                        "before_enabled": before["enabled"],
                        "after_enabled": after["enabled"],
                        "before_interval_seconds": before["interval_seconds"],
                        "after_interval_seconds": after["interval_seconds"],
                    },
                ],
            }
        return {"source_id": "douyin", **after, "changed": before != after}
