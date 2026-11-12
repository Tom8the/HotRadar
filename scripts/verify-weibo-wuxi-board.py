"""Verify real city access before enabling the independent Wuxi board."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from hotradar.bootstrap import seed_sources
from hotradar.collection import collect_board
from hotradar.collectors.types import CollectionError
from hotradar.collectors.weibo_city import WeiboWuxiCollector
from hotradar.db.models import Board, SourceSettingChange
from hotradar.db.session import session_factory


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    batch = await WeiboWuxiCollector().collect()
    if not batch.items:
        raise CollectionError(
            "verification_incomplete", "An empty board cannot verify city access."
        )
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "board": "weibo-wuxi",
        "city": batch.raw_payload["city"],
        "items": len(batch.items),
        "upstream_updated_at": batch.upstream_updated_at.isoformat()
        if batch.upstream_updated_at
        else None,
        "activated": args.activate,
    }
    if args.activate:
        seed_sources()
        with session_factory().begin() as session:
            board = session.get(Board, "weibo-wuxi")
            before = {"enabled": board.enabled, "interval_seconds": board.interval_seconds}
            board.enabled = True
            after = {"enabled": True, "interval_seconds": board.interval_seconds}
            if before != after:
                session.add(SourceSettingChange(board_id=board.id, before=before, after=after))
        # Exercise the production dispatch and make a fresh real request, not a fixture import.
        result["collection"] = await collect_board("weibo-wuxi", "city-verify:" + uuid4().hex)
        if result["collection"]["status"] not in {"success", "partial"}:
            raise CollectionError("verification_incomplete", "City collection did not persist.")
    target = Path("data/weibo-wuxi-probe/verification.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except CollectionError as error:
        print(json.dumps({"status": "failed", "code": error.code}))
        raise SystemExit(1) from None
