"""Verify the official city ranking against national results before independent activation."""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from hotradar.bootstrap import seed_sources
from hotradar.collection import collect_board
from hotradar.collectors.douyin_app_city import BOARD_ID, ENDPOINT, PARAMS, DouyinAppWuxiCollector
from hotradar.collectors.types import CollectionError
from hotradar.db.models import Board, SourceSettingChange
from hotradar.db.session import session_factory


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()
    batch = await DouyinAppWuxiCollector().collect()
    if not batch.items or batch.rejected_count:
        raise CollectionError("verification_incomplete", "Need a complete nonempty city sample.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            ENDPOINT,
            params={
                **PARAMS,
                "board_type": "0",
                "board_sub_type": "",
            },
        )
        response.raise_for_status()
        national = response.json()
    national_rows = national.get("data", {}).get("word_list")
    if national.get("status_code") != 0 or not isinstance(national_rows, list) or not national_rows:
        raise CollectionError("verification_incomplete", "National comparison is unavailable.")
    national_top = [row["word"] for row in national_rows[:5]]
    if [row.title for row in batch.items[:5]] == national_top:
        raise CollectionError("verification_incomplete", "City results duplicate national head.")
    result = {
        "checked_at": datetime.now(UTC).isoformat(),
        "board": BOARD_ID,
        "endpoint": ENDPOINT,
        "params": PARAMS,
        "items": len(batch.items),
        "rank_sequence": [row.source_rank for row in batch.items],
        "upstream_updated_at": batch.upstream_updated_at.isoformat()
        if batch.upstream_updated_at
        else None,
        "city": {"code": "320200", "name": "无锡"},
        "city_top": [
            {"rank": row.source_rank, "title": row.title, "heat": row.metrics["city_heat"]["value"]}
            for row in batch.items[:5]
        ],
        "national_top": national_top,
        "activated": False,
    }
    if args.activate:
        seed_sources()
        with session_factory().begin() as session:
            board = session.get(Board, BOARD_ID)
            before = {"enabled": board.enabled, "interval_seconds": board.interval_seconds}
            board.enabled = True
            after = {**before, "enabled": True}
            if before != after:
                session.add(SourceSettingChange(board_id=BOARD_ID, before=before, after=after))
        result["collection"] = await collect_board(BOARD_ID, "app-city-verify:" + uuid4().hex)
        if result["collection"]["status"] != "success":
            if not before["enabled"]:
                with session_factory().begin() as session:
                    session.get(Board, BOARD_ID).enabled = False
                    session.add(SourceSettingChange(board_id=BOARD_ID, before=after, after=before))
            raise CollectionError("verification_incomplete", "City collection did not persist.")
        result["activated"] = True
    target = Path("data/douyin-app-wuxi-audit/activation.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    asyncio.run(main())
