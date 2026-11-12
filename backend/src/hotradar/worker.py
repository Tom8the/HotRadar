import argparse
import asyncio
import json
from datetime import UTC, datetime

from sqlalchemy import select

from hotradar.collection import collect_board, enabled_boards, recovery_jobs
from hotradar.content_collection import run_due
from hotradar.db.models import CollectionRun
from hotradar.db.session import session_factory
from hotradar.events import process_events


async def run_once(key: str | None = None):
    jobs = []
    for board in enabled_boards():
        if key is None:
            with session_factory()() as session:
                latest = session.scalar(
                    select(CollectionRun)
                    .where(
                        CollectionRun.board_id == board.id,
                    )
                    .order_by(CollectionRun.created_at.desc())
                    .limit(1)
                )
                if (
                    latest
                    and (datetime.now(UTC) - latest.created_at).total_seconds()
                    < board.interval_seconds
                ):
                    continue
        bucket = int(datetime.now(UTC).timestamp()) // board.interval_seconds
        job_key = f"{key}:{board.id}" if key else f"scheduled:{board.id}:{bucket}"
        jobs.append(collect_board(board.id, job_key))
    results = await asyncio.gather(*jobs)
    for result in results:
        print(json.dumps(result, ensure_ascii=False), flush=True)
    event_result = process_events()
    if event_result["snapshots"]:
        print(json.dumps({"events": event_result}, ensure_ascii=False), flush=True)
    content_result = await run_due()
    if content_result:
        print(json.dumps({"contents": content_result}, ensure_ascii=False), flush=True)
    return results


async def run_forever():
    print("HotRadar worker: persistent collection of Douyin, Weibo and Baidu.", flush=True)
    for board_id, key in recovery_jobs():
        result = await collect_board(board_id, key)
        print(json.dumps(result, ensure_ascii=False), flush=True)
    while True:
        try:
            await run_once()
        except Exception as exc:  # noqa: BLE001 -- report and recover after persistence/network faults
            print(json.dumps({"status": "worker_error", "type": type(exc).__name__}), flush=True)
        await asyncio.sleep(15)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--key", help="Stable run key for repeatable one-shot collection.")
    args = parser.parse_args()
    try:
        if args.once:
            results = asyncio.run(run_once(args.key))
            if any(r["status"] not in {"success", "partial", "empty"} for r in results):
                raise SystemExit(1)
        else:
            asyncio.run(run_forever())
    except KeyboardInterrupt:
        print("HotRadar worker stopped.", flush=True)


if __name__ == "__main__":
    main()
