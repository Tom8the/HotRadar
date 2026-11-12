import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from hotradar.api.queries import serialize_snapshot
from hotradar.collection import collect_board
from hotradar.collectors.platforms import parse_weibo
from hotradar.collectors.types import CollectionError
from hotradar.db.models import Board, BoardSnapshot, CollectionRun, RawPayload, SnapshotItem
from sqlalchemy import func, select

NOW = datetime.now(UTC)


class FakeCollector:
    def __init__(self, batch=None, failures=0):
        self.batch = batch
        self.failures = failures
        self.calls = 0

    async def collect(self):
        self.calls += 1
        if self.calls <= self.failures:
            raise CollectionError("network_error", "Test failure.")
        return self.batch


def sample(title="测试热点", metric=0, observed=NOW):
    return parse_weibo(
        {
            "ok": 1,
            "data": {
                "realtime": [
                    {"word": title, "rank": 0, "num": metric},
                ]
            },
        },
        observed,
    )


@pytest.mark.asyncio
async def test_same_task_is_idempotent_and_old_snapshot_immutable(database):
    factory, board = database
    collector = FakeCollector(sample())
    first = await collect_board(board, "unique-job", collector, retry_delay=0)
    repeated = await collect_board(board, "unique-job", collector, retry_delay=0)
    assert collector.calls == 1 and repeated["reused"]
    assert first["snapshot"]
    await collect_board(
        board, "next-job", FakeCollector(sample(metric=300, observed=NOW + timedelta(minutes=15)))
    )
    with factory() as session:
        assert session.scalar(select(func.count(BoardSnapshot.id))) == 2
        old = session.scalar(
            select(SnapshotItem).where(SnapshotItem.snapshot_id == first["snapshot"])
        )
        assert old.metrics["search_heat"]["value"] == 0
        assert session.scalar(select(func.count(CollectionRun.id))) == 2


@pytest.mark.asyncio
async def test_repeated_payload_keeps_observations_but_deduplicates_raw(database):
    factory, board = database
    await collect_board(board, "a", FakeCollector(sample()))
    await collect_board(board, "b", FakeCollector(sample(observed=NOW + timedelta(minutes=15))))
    with factory() as session:
        assert session.scalar(select(func.count(RawPayload.id))) == 1
        assert session.scalar(select(func.count(BoardSnapshot.id))) == 2


@pytest.mark.asyncio
async def test_failure_retries_preserves_previous_success(database):
    factory, board = database
    await collect_board(board, "success", FakeCollector(sample()))
    bad = FakeCollector(failures=5)
    result = await collect_board(board, "failed", bad, retry_delay=0)
    assert result["status"] == "failed" and bad.calls == 3
    with factory() as session:
        assert session.scalar(select(func.count(BoardSnapshot.id))) == 1
        run = session.scalar(select(CollectionRun).where(CollectionRun.job_key == "failed"))
        assert run.attempt_count == 3 and run.error_code == "network_error"


@pytest.mark.asyncio
async def test_legitimate_empty_snapshot_is_recorded(database):
    factory, board = database
    empty = parse_weibo({"ok": 1, "data": {"realtime": []}}, NOW)
    result = await collect_board(board, "empty", FakeCollector(empty))
    assert result["status"] == "empty"
    with factory() as session:
        snapshot = session.scalar(select(BoardSnapshot))
        assert snapshot.item_count == 0 and snapshot.status == "empty"


@pytest.mark.asyncio
async def test_source_lock_prevents_concurrent_collection(database):
    factory, board = database
    entered = asyncio.Event()
    released = asyncio.Event()

    class Blocking:
        async def collect(self):
            entered.set()
            await released.wait()
            return sample()

    first = asyncio.create_task(collect_board(board, "one", Blocking()))
    await entered.wait()
    second = await collect_board(board, "two", FakeCollector(sample()))
    assert second["status"] == "busy"
    released.set()
    assert (await first)["status"] == "success"


@pytest.mark.asyncio
async def test_final_interrupted_attempt_is_closed_without_extra_requests(database):
    factory, board = database
    with factory.begin() as session:
        session.add(
            CollectionRun(
                board_id=board,
                job_key="interrupted-final",
                status="running",
                attempt_count=3,
                item_count=0,
                parser_version="weibo-hot-v1",
            )
        )
    collector = FakeCollector(sample())
    result = await collect_board(board, "interrupted-final", collector)
    assert result["status"] == "failed" and collector.calls == 0
    with factory() as session:
        assert session.scalar(select(CollectionRun)).error_code == "interrupted"


@pytest.mark.asyncio
@pytest.mark.parametrize("cached,rejected", [(True, 0), (False, 1)])
async def test_cached_or_partial_observation_has_no_rank_delta(database, cached, rejected):
    factory, board = database
    await collect_board(board, "prior", FakeCollector(sample()))
    batch = replace(
        sample(observed=NOW + timedelta(minutes=15)), from_cache=cached, rejected_count=rejected
    )
    result = await collect_board(board, "limited", FakeCollector(batch))
    with factory() as session:
        saved = session.get(BoardSnapshot, result["snapshot"])
        record = serialize_snapshot(session, saved, session.get(Board, board))
        assert record["items"][0]["rank_change"] is None
