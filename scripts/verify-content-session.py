"""Validate real source fields before enabling either normal-session content adapter."""
import argparse
import asyncio
import json
from datetime import UTC, datetime

from hotradar.content_collection import collect_douyin, collect_weibo

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", choices=["weibo", "douyin"], default="weibo")
parser.add_argument("--activate", action="store_true")
parser.add_argument("--key", default="content-verify:" + datetime.now(UTC).isoformat())
args = parser.parse_args()
collector = collect_weibo if args.source == "weibo" else collect_douyin
result = asyncio.run(collector(args.key, verify=True, activate=args.activate))
print(json.dumps(result))
if result["status"] not in {"success", "partial"}:
    raise SystemExit(1)
