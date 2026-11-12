"""Probe the observed public SDK endpoint using only this local platform session."""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from hotradar.session_store import client_cookies, load

root = Path(__file__).resolve().parents[1] / "data" / "douyin-sdk-probes"
params = json.loads((root / "public-request-params.json").read_text("utf-8"))
allowed = {"device_platform", "aid", "channel", "module_id", "tag_id", "count"}
if not set(params) <= allowed:
    raise SystemExit("Unexpected request parameter; probe stopped.")
headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/jingxuan"}
for cookie in load("douyin")["cookies"]:
    if cookie["name"].lower() == "uifid":
        headers["uifid"] = cookie["value"]
with httpx.Client(
    cookies=client_cookies("douyin"), headers=headers, follow_redirects=False, timeout=20
) as client:
    response = client.post("https://www.douyin.com/aweme/v2/web/module/feed/", params=params)
result = {
    "endpoint": "https://www.douyin.com/aweme/v2/web/module/feed/",
    "observed_at": datetime.now(UTC).isoformat(),
    "http_status": response.status_code,
    "request_method": "POST",
    "body_bytes": len(response.content),
    "normal_local_uifid_present": "uifid" in headers,
    "sdk_signature_replayed": False,
    "collector_activation_performed": False,
}
try:
    payload = response.json()
except ValueError:
    payload = None
if isinstance(payload, dict):
    result["status_code"] = payload.get("status_code")
    result["root_keys"] = list(payload)
    if isinstance(payload.get("aweme_list"), list):
        rows = []
        for item in payload["aweme_list"]:
            if not isinstance(item, dict):
                continue
            row = {
                k: item.get(k) for k in ("aweme_id", "desc", "create_time", "aweme_type", "is_ads")
            }
            author = item.get("author")
            stats = item.get("statistics")
            row["author"] = (
                {k: author.get(k) for k in ("uid", "sec_uid", "nickname")}
                if isinstance(author, dict)
                else None
            )
            row["statistics"] = (
                {
                    k: stats.get(k)
                    for k in ("digg_count", "comment_count", "collect_count", "share_count")
                }
                if isinstance(stats, dict)
                else None
            )
            rows.append(row)
        result["content_count"] = len(rows)
        result["metric_coverage"] = {
            k: sum(isinstance((r["statistics"] or {}).get(k), int) for r in rows)
            for k in ("digg_count", "comment_count", "collect_count", "share_count")
        }
        (root / "http-feed.json").write_text(
            json.dumps(
                {
                    "endpoint": result["endpoint"],
                    "request_method": "POST",
                    "http_status": response.status_code,
                    "status_code": payload.get("status_code"),
                    "observed_at": result["observed_at"],
                    "method": "normal-session-sdk-endpoint",
                    "items": rows,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
if response.text in {
    "Blocked by ArgusSecurityPlugin Uifid Not Found",
    "Blocked by ArgusSecurityPlugin Signature Not Found",
}:
    result["reason"] = response.text
else:
    result["reason"] = "Response requires separate business validation."
(root / "http-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result))
