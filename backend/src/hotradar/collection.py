import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, text

from hotradar.collectors.baidu import BaiduCollector
from hotradar.collectors.douyin_app_city import DouyinAppWuxiCollector
from hotradar.collectors.douyin_city import DouyinWuxiCollector
from hotradar.collectors.platforms import PlatformCollector
from hotradar.collectors.types import CollectionBatch, CollectionError
from hotradar.collectors.weibo_city import WeiboWuxiCollector
from hotradar.config import get_settings
from hotradar.db.models import (
    Board,
    BoardSnapshot,
    CollectionRun,
    Item,
    RawPayload,
    SnapshotItem,
    SourceSettingChange,
    new_id,
)
from hotradar.db.session import get_engine, session_factory

TERMINAL = {"success", "empty", "partial", "failed"}


def persist_batch(
    session, run: CollectionRun, batch: CollectionBatch, interval_seconds: int | None = None
) -> BoardSnapshot:
    existing = session.scalar(select(BoardSnapshot).where(BoardSnapshot.run_id == run.id))
    if existing is not None:
        return existing
    payload_hash = hashlib.sha256(
        json.dumps(batch.raw_payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    payload = session.scalar(
        select(RawPayload).where(
            RawPayload.board_id == run.board_id,
            RawPayload.sha256 == payload_hash,
        )
    )
    if payload is None:
        payload = RawPayload(
            id=new_id(),
            board_id=run.board_id,
            sha256=payload_hash,
            data=batch.raw_payload,
        )
        session.add(payload)
        session.flush()
    status = "partial" if batch.rejected_count else ("success" if batch.items else "empty")
    snapshot = BoardSnapshot(
        id=new_id(),
        board_id=run.board_id,
        run_id=run.id,
        payload_id=payload.id,
        observed_at=batch.observed_at,
        upstream_updated_at=batch.upstream_updated_at,
        item_count=len(batch.items),
        status=status,
        from_cache=batch.from_cache,
        parser_version=batch.parser_version,
        interval_seconds=interval_seconds or session.get(Board, run.board_id).interval_seconds,
    )
    session.add(snapshot)
    session.flush()
    for row in batch.items:
        item = session.scalar(
            select(Item).where(
                Item.board_id == run.board_id,
                Item.external_id == row.external_id,
            )
        )
        if item is None:
            item = Item(
                id=new_id(),
                board_id=run.board_id,
                external_id=row.external_id,
                item_kind="topic",
                canonical_url=row.original_url,
                original_url=row.original_url,
                title=row.title,
                first_observed_at=batch.observed_at,
                last_observed_at=batch.observed_at,
            )
            session.add(item)
        else:
            item.title = row.title
            item.original_url = row.original_url
            item.last_observed_at = batch.observed_at
        session.flush()
        session.add(
            SnapshotItem(
                snapshot_id=snapshot.id,
                item_id=item.id,
                title=row.title,
                original_url=row.original_url,
                source_rank=row.source_rank,
                display_order=row.display_order,
                is_pinned=row.is_pinned,
                published_at=row.published_at,
                source_event_at=row.source_event_at,
                metrics=row.metrics,
                flags=row.flags,
            )
        )
    run.status = status
    run.finished_at = datetime.now(UTC)
    run.item_count = len(batch.items)
    run.error_code = "partial_parse" if batch.rejected_count else None
    run.error_message = f"{batch.rejected_count} rows rejected." if batch.rejected_count else None
    return snapshot


async def collect_board(
    board_id: str, job_key: str, collector=None, retry_delay=2, *, activate=False, max_attempts=3
) -> dict:
    lock_id = int.from_bytes(hashlib.sha256(board_id.encode()).digest()[:8], signed=True)
    factory = session_factory()
    with get_engine().connect() as lock_connection:
        acquired = lock_connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_id}
        )
        if not acquired:
            return {"board": board_id, "status": "busy"}
        try:
            with factory.begin() as session:
                board = session.get(Board, board_id)
                if board is None or (not board.enabled and not activate):
                    return {"board": board_id, "status": "disabled"}
                run = session.scalar(select(CollectionRun).where(CollectionRun.job_key == job_key))
                if run is not None and run.status in TERMINAL:
                    return {"board": board_id, "status": run.status, "reused": True, "run": run.id}
                if run is None:
                    run = CollectionRun(
                        id=new_id(),
                        board_id=board_id,
                        job_key=job_key,
                        parser_version=board.parser_version,
                    )
                    session.add(run)
                    session.flush()
                run_id = run.id
                source_id = board.source_id
                interval_seconds = board.interval_seconds
                attempts = run.attempt_count
                if attempts >= max_attempts:
                    run.status = "failed"
                    run.finished_at = datetime.now(UTC)
                    run.error_code = "interrupted"
                    run.error_message = "Interrupted after the final attempt."
                    return {"board": board_id, "status": "failed", "run": run_id}
            source_collector = collector or (
                BaiduCollector(timeout=get_settings().timeout)
                if board_id == "baidu-hot"
                else DouyinAppWuxiCollector(
                    timeout=get_settings().timeout, proxy=get_settings().douyin_wuxi_proxy
                )
                if board_id == "douyin-wuxi-app"
                else DouyinWuxiCollector(timeout=get_settings().timeout)
                if board_id == "douyin-wuxi"
                else WeiboWuxiCollector(timeout=get_settings().timeout)
                if board_id == "weibo-wuxi"
                else PlatformCollector(source_id, timeout=get_settings().timeout)
            )
            error_code = None
            while attempts < max_attempts:
                attempts += 1
                with factory.begin() as session:
                    run = session.get(CollectionRun, run_id)
                    run.status = "running"
                    run.attempt_count = attempts
                    run.started_at = run.started_at or datetime.now(UTC)
                    run.error_code = None
                    run.error_message = None
                try:
                    batch = await source_collector.collect()
                    with factory.begin() as session:
                        run = session.get(CollectionRun, run_id)
                        if activate:
                            board = session.get(Board, board_id, with_for_update=True)
                            interval_seconds = board.interval_seconds
                            if not board.enabled:
                                board.enabled = True
                                settings = {"interval_seconds": interval_seconds}
                                session.add(
                                    SourceSettingChange(
                                        board_id=board_id,
                                        before={**settings, "enabled": False},
                                        after={**settings, "enabled": True},
                                    )
                                )
                        snapshot = persist_batch(session, run, batch, interval_seconds)
                        result = {
                            "board": board_id,
                            "status": run.status,
                            "items": len(batch.items),
                            "run": run_id,
                            "snapshot": snapshot.id,
                            "reused": False,
                        }
                    return result
                except CollectionError as exc:
                    error_code = exc.code
                    with factory.begin() as session:
                        run = session.get(CollectionRun, run_id)
                        run.error_code = exc.code
                        run.error_message = str(exc)[:240]
                        if attempts == max_attempts:
                            run.status = "failed"
                            run.finished_at = datetime.now(UTC)
                    if attempts < max_attempts:
                        await asyncio.sleep(retry_delay * attempts)
                except Exception:
                    # Do not put exception reprs (which may contain connection secrets) in records.
                    with factory.begin() as session:
                        run = session.get(CollectionRun, run_id)
                        run.status = "failed"
                        run.finished_at = datetime.now(UTC)
                        run.error_code = "internal_error"
                        run.error_message = (
                            "Collection could not be persisted; inspect local runtime."
                        )
                    raise
            return {"board": board_id, "status": "failed", "run": run_id, "error_code": error_code}
        finally:
            lock_connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock_id})
            lock_connection.commit()


def enabled_boards():
    with session_factory()() as session:
        return list(
            session.scalars(
                select(Board).where(
                    Board.enabled.is_(True),
                    Board.source_id.in_(["douyin", "weibo", "baidu"]),
                )
            )
        )


def recovery_jobs():
    # A new worker may resume an interrupted queued/running job; the advisory lock
    # prevents taking over a job still owned by a live worker.
    with session_factory()() as session:
        return [
            (r.board_id, r.job_key)
            for r in session.scalars(
                select(CollectionRun)
                .where(
                    CollectionRun.status.in_(["queued", "running"]),
                    CollectionRun.created_at > datetime.now(UTC) - timedelta(days=1),
                )
                .order_by(CollectionRun.created_at)
            )
        ]
