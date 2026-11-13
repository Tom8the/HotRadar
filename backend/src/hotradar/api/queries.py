from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from hotradar.db.models import Board, BoardSnapshot, CollectionRun, Item, SnapshotItem, Source


def day_bounds(day: date, timezone: str = "Asia/Shanghai"):
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, zone).astimezone(UTC)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(UTC)
    return start, end


def serialize_snapshot(session, snapshot: BoardSnapshot, board: Board) -> dict:
    previous = session.scalar(
        select(BoardSnapshot)
        .where(
            BoardSnapshot.board_id == board.id,
            BoardSnapshot.observed_at < snapshot.observed_at,
        )
        .order_by(BoardSnapshot.observed_at.desc())
        .limit(1)
    )
    prior_ranks = {}
    comparable = (
        previous is not None
        and (snapshot.observed_at - previous.observed_at).total_seconds()
        <= min(snapshot.interval_seconds or 900, previous.interval_seconds or 900) * 3
        and not (snapshot.from_cache or previous.from_cache)
        and snapshot.status == "success"
        and previous.status == "success"
    )
    if comparable:
        prior_ranks = {
            row.item_id: row.source_rank
            for row in session.scalars(
                select(SnapshotItem).where(SnapshotItem.snapshot_id == previous.id)
            )
        }
    rows = session.execute(
        select(SnapshotItem, Item)
        .join(
            Item,
            Item.id == SnapshotItem.item_id,
        )
        .where(SnapshotItem.snapshot_id == snapshot.id)
        .order_by(SnapshotItem.display_order)
    ).all()
    items = []
    for row, item in rows:
        old_rank = prior_ranks.get(row.item_id)
        change = (
            old_rank - row.source_rank
            if (old_rank is not None and row.source_rank is not None)
            else None
        )
        items.append(
            {
                "id": item.id,
                "external_id": item.external_id,
                "title": row.title,
                "url": row.original_url,
                "item_kind": item.item_kind,
                "rank": row.source_rank,
                "display_order": row.display_order,
                "is_pinned": row.is_pinned,
                "metrics": row.metrics,
                "flags": row.flags,
                "first_observed_at": item.first_observed_at,
                "published_at": row.published_at,
                "source_event_at": row.source_event_at,
                "rank_change": change,
            }
        )
    return {
        "id": snapshot.id,
        "observed_at": snapshot.observed_at,
        "upstream_updated_at": snapshot.upstream_updated_at,
        "item_count": snapshot.item_count,
        "status": snapshot.status,
        "from_cache": snapshot.from_cache,
        "parser_version": snapshot.parser_version,
        "interval_seconds": snapshot.interval_seconds,
        "items": items,
    }


def dashboard(session, day: date):
    now = datetime.now(UTC)
    start, end = day_bounds(day)
    rows = session.execute(
        select(Board, Source)
        .join(Source)
        .where(
            Board.source_id.in_(["weibo", "douyin", "baidu"]),
        )
        .order_by(Board.id)
    ).all()
    boards = []
    for board, source in rows:
        snapshot = session.scalar(
            select(BoardSnapshot)
            .where(
                BoardSnapshot.board_id == board.id,
                BoardSnapshot.observed_at >= start,
                BoardSnapshot.observed_at < end,
            )
            .order_by(BoardSnapshot.observed_at.desc())
            .limit(1)
        )
        run = session.scalar(
            select(CollectionRun)
            .where(
                CollectionRun.board_id == board.id,
            )
            .order_by(CollectionRun.created_at.desc())
            .limit(1)
        )
        last = session.scalar(
            select(BoardSnapshot)
            .where(
                BoardSnapshot.board_id == board.id,
            )
            .order_by(BoardSnapshot.observed_at.desc())
            .limit(1)
        )
        stale = (
            last is None or (now - last.observed_at).total_seconds() > board.interval_seconds * 3
        )
        state = (
            "paused"
            if not board.enabled
            else (
                "failed"
                if run and run.status == "failed"
                else (
                    "collecting"
                    if run and run.status == "running"
                    else (
                        "pending"
                        if last is None
                        else (
                            "stale"
                            if stale
                            else ("partial" if last.status == "partial" else "fresh")
                        )
                    )
                )
            )
        )
        boards.append(
            {
                "id": board.id,
                "source_id": source.id,
                "source_name": source.name,
                "source_url": source.home_url,
                "provider": (
                    "douyin-search-landing"
                    if board.id == "douyin-wuxi-app"
                    else "douyin-creator"
                    if board.id in {"douyin-wuxi", "douyin-creator-hot"}
                    else "weibo-mobile"
                    if board.id == "weibo-wuxi"
                    else source.provider
                ),
                "name": board.name,
                "item_kind": board.item_kind,
                "metric_name": board.metric_name,
                "metric_label": board.metric_label,
                "interval_seconds": board.interval_seconds,
                "enabled": board.enabled,
                "state": state,
                "stale": stale,
                "latest_run": None
                if run is None
                else {
                    "id": run.id,
                    "status": run.status,
                    "created_at": run.created_at,
                    "finished_at": run.finished_at,
                    "attempt_count": run.attempt_count,
                    "error_code": run.error_code,
                    "error_message": run.error_message,
                },
                "last_success_at": last.observed_at if last else None,
                "snapshot": serialize_snapshot(session, snapshot, board) if snapshot else None,
            }
        )
    ids = [b.id for b, _ in rows]
    unique_items = (
        session.scalar(
            select(func.count(Item.id)).where(
                Item.board_id.in_(ids),
                Item.first_observed_at >= start,
                Item.first_observed_at < end,
            )
        )
        or 0
    )
    snapshot_count = (
        session.scalar(
            select(func.count(BoardSnapshot.id)).where(
                BoardSnapshot.board_id.in_(ids),
                BoardSnapshot.observed_at >= start,
                BoardSnapshot.observed_at < end,
            )
        )
        or 0
    )
    first = session.scalar(
        select(func.min(BoardSnapshot.observed_at)).where(
            BoardSnapshot.board_id.in_(ids),
        )
    )
    return {
        "selected_date": day,
        "timezone": "Asia/Shanghai",
        "read_at": now,
        "first_observed_at": first,
        "boards": boards,
        "stats": {
            "enabled_sources": sum(board.enabled for board, _ in rows),
            "healthy_sources": sum(b["state"] in {"fresh", "partial"} for b in boards),
            "new_items": unique_items,
            "visible_items": sum(
                b["snapshot"]["item_count"] if b["snapshot"] else 0 for b in boards
            ),
            "snapshots": snapshot_count,
        },
        "stage": "M4",
        "coverage_note": "历史仅包含实际采集日，尚无数据的日期不补值。",
    }
