"""Read cookies from a private stdin pipe and save encrypted locally; never echo them."""
import argparse
import json
import sys

from hotradar.session_store import save

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("platform", choices=["weibo", "douyin"])
args = parser.parse_args()
try:
    result = save(args.platform, json.load(sys.stdin))
except Exception as exc:
    print(json.dumps({"saved": False, "error_type": type(exc).__name__}))
    raise SystemExit(1) from None
print(json.dumps(result))
