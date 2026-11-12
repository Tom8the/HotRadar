"""One-off anonymous content checks; never stores cookies or request headers."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
from hotradar.collectors.platforms import DOUYIN_URL, WEIBO_URL, sanitize_payload

OUT = Path("data/content-source-probes")


async def check(client, name, url, params=None):
    now = datetime.now(UTC).isoformat()
    try:
        response = await client.get(url, params=params)
        result = {
            "name": name,
            "requested_url": str(response.request.url),
            "observed_at": now,
            "http_status": response.status_code,
            "content_type": response.headers.get("content-type"),
            "body_bytes": len(response.content),
            "body_sha256": hashlib.sha256(response.content).hexdigest(),
            "redirect_host": httpx.URL(response.headers["location"]).host
            if response.headers.get("location", "").startswith("http")
            else None,
        }
        try:
            payload = sanitize_payload(response.json())
            result["json_keys"] = list(payload) if isinstance(payload, dict) else None
            if isinstance(payload, dict):
                result["business_status"] = {
                    k: payload[k]
                    for k in ("ok", "status_code", "msg", "message", "error_code", "error")
                    if k in payload
                }
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / f"{name}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except ValueError:
            result["is_json"] = False
            result["challenge_markers_present"] = any(
                x in response.text.lower()
                for x in ("passport.weibo", "验证码", "captcha", "verifycenter", "acrawler")
            )
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result
    except httpx.HTTPError as exc:
        result = {
            "name": name,
            "requested_url": url,
            "observed_at": now,
            "error_type": type(exc).__name__,
        }
        print(json.dumps(result), flush=True)
        return result


async def main():
    async with (
        httpx.AsyncClient(
            timeout=20,
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://weibo.com/"},
        ) as weibo,
        httpx.AsyncClient(
            timeout=20,
            follow_redirects=False,
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/"},
        ) as douyin,
    ):
        results = await asyncio.gather(
            check(weibo, "weibo-hot-topics", WEIBO_URL),
            check(
                weibo,
                "weibo-hot-content",
                "https://weibo.com/ajax/feed/hottimeline",
                {
                    "since_id": "0",
                    "refresh": "0",
                    "group_id": "102803",
                    "containerid": "102803",
                    "extparam": "discover|new_feed",
                    "max_id": "0",
                    "count": "10",
                },
            ),
            check(douyin, "douyin-content-index", "https://www.douyin.com/shipin"),
            check(
                douyin, "douyin-content-detail", "https://www.douyin.com/video/7696454624705776937"
            ),
            check(
                weibo, "weibo-open-public", "https://api.weibo.com/2/statuses/public_timeline.json"
            ),
            check(
                weibo,
                "weibo-content-detail",
                "https://weibo.com/ajax/statuses/show",
                {"id": "RlUKcaCik"},
            ),
        )
        topics = (
            json.loads((OUT / "weibo-hot-topics.json").read_text("utf-8"))
            if results[0].get("business_status", {}).get("ok") == 1
            else {}
        )
        words = topics.get("data", {}).get("realtime", [])
        if words:
            results.append(
                await check(
                    weibo,
                    "weibo-mobile-search",
                    "https://m.weibo.cn/api/container/getIndex",
                    {
                        "containerid": "100103type=1&q=" + words[0]["word"],
                        "page_type": "searchall",
                        "page": "1",
                    },
                )
            )
        await douyin.get("https://www.douyin.com/passport/general/login_guiding_strategy/?aid=6383")
        results.append(await check(douyin, "douyin-hot-topics", DOUYIN_URL))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(main())
