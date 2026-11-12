"""Correct Creator Center source labeling without changing observations or collection settings."""

import json
from datetime import UTC, datetime
from pathlib import Path

from hotradar.db.models import Board, BoardSnapshot, SourceSettingChange
from hotradar.db.session import session_factory
from sqlalchemy import select

NAME = "无锡创作热点 · 创作者中心"


def main():
    with session_factory().begin() as session:
        board = session.scalar(select(Board).where(Board.id == "douyin-wuxi").with_for_update())
        if board is None or board.source_id != "douyin":
            raise RuntimeError("Expected Douyin Creator Center source is not registered.")
        snapshots = set(
            session.scalars(select(BoardSnapshot.id).where(BoardSnapshot.board_id == board.id))
        )
        before = {
            "name": board.name,
            "enabled": board.enabled,
            "interval_seconds": board.interval_seconds,
        }
        board.name = NAME
        after = {**before, "name": NAME}
        if before != after:
            session.add(SourceSettingChange(board_id=board.id, before=before, after=after))
        result = {
            "checked_at": datetime.now(UTC).isoformat(),
            "board": board.id,
            "before": before,
            "after": after,
            "existing_snapshots_preserved": len(snapshots),
        }
    target = Path("data/douyin-app-wuxi-audit/label-correction.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
