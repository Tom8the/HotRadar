"""Pause a content collector locally without touching topic collection or history."""

import argparse
import json
from datetime import UTC, datetime

from hotradar.db.models import ContentSourceSetting
from hotradar.db.session import session_factory
from sqlalchemy import select

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("source", choices=["weibo", "douyin"])
args = parser.parse_args()
with session_factory().begin() as session:
    setting = session.scalar(
        select(ContentSourceSetting)
        .where(ContentSourceSetting.source_id == args.source)
        .with_for_update()
    )
    if setting is None:
        raise SystemExit("Content source not registered.")
    before = setting.enabled
    setting.enabled = False
    if before:
        proof = dict(setting.verification)
        proof["controls"] = [
            *proof.get("controls", []),
            {
                "action": "pause",
                "at": datetime.now(UTC).isoformat(),
                "before_enabled": True,
                "after_enabled": False,
            },
        ]
        setting.verification = proof
    print(json.dumps({"source": args.source, "enabled": False, "changed": before}))
