"""Baidu's official realtime board; topics and search indices, never video views."""

import json
import re
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx

from hotradar.collectors.platforms import optional_count, optional_timestamp, sanitize_payload
from hotradar.collectors.types import CollectedItem, CollectionBatch, CollectionError

BAIDU_URL = "https://top.baidu.com/board?tab=realtime&platform=pc"
PARSER_VERSION = "baidu-hot-v1"


def parse_baidu(html: str, observed_at: datetime) -> CollectionBatch:
    match = re.search(r"<!--s-data:(.*?)-->", html, re.S)
    if not match:
        raise CollectionError("invalid_shape", "Baidu board data is missing.")
    try:
        payload = json.loads(match[1])
    except ValueError as exc:
        raise CollectionError("invalid_json", "Baidu board data is not valid JSON.") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    cards = data.get("cards") if isinstance(data, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("tab") != "realtime"
        or not isinstance(cards, list)
    ):
        raise CollectionError("invalid_shape", "Baidu realtime board was not returned.")
    cards = [
        c
        for c in cards
        if isinstance(c, dict)
        and c.get("component") == "hotList"
        and c.get("typeName") == "realtime"
    ]
    if len(cards) != 1 or not isinstance(cards[0].get("content"), list):
        raise CollectionError("invalid_shape", "Baidu realtime content list is missing.")
    card = cards[0]
    rows = card["content"]
    items, seen, rejected = [], set(), 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            rejected += 1
            continue
        title = row.get("word")
        title = title.strip() if isinstance(title, str) else ""
        url = row.get("rawUrl") or row.get("url")
        try:
            parsed = urlsplit(url) if isinstance(url, str) else None
            safe_url = (
                parsed is not None
                and parsed.scheme == "https"
                and parsed.hostname == "www.baidu.com"
                and parsed.path == "/s"
                and not parsed.username
                and not parsed.password
                and parsed.port in {None, 443}
            )
        except ValueError:
            safe_url = False
        if not title or "\ufffd" in title or title in seen or not safe_url:
            rejected += 1
            continue
        seen.add(title)
        pinned = row.get("isTop") is True
        is_ad = bool(row.get("is_ad") or row.get("ad_info"))
        rank = optional_count(row.get("index"))
        items.append(
            CollectedItem(
                external_id=title,
                title=title,
                original_url=url,
                source_rank=rank + 1 if rank is not None and not pinned and not is_ad else None,
                display_order=index + 1,
                is_pinned=pinned,
                flags=["广告"] if is_ad else [],
                metrics={
                    "search_heat": {
                        "value": optional_count(row.get("hotScore")),
                        "unit": "热搜指数",
                        "provider": "baidu",
                        "definition": "百度实时热搜网页 hotScore 原值，不是搜索次数或播放量",
                    }
                },
            )
        )
    if rows and not items:
        raise CollectionError("parse_failed", "All Baidu rows failed validation.")
    return CollectionBatch(
        items=items,
        raw_payload=sanitize_payload({"source_url": BAIDU_URL, "tab": "realtime", "card": card}),
        observed_at=observed_at,
        parser_version=PARSER_VERSION,
        upstream_updated_at=optional_timestamp(card.get("updateTime")),
        rejected_count=rejected,
    )


class BaiduCollector:
    def __init__(self, timeout=20, transport=None):
        self.timeout = timeout
        self.transport = transport

    async def collect(self) -> CollectionBatch:
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5),
                headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "zh-CN,zh;q=0.9"},
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                response = await client.get(BAIDU_URL)
                response.raise_for_status()
                batch = parse_baidu(response.text, datetime.now(UTC))
                age = optional_count(response.headers.get("Age"))
                return replace(batch, from_cache=bool(age and age > 0))
        except httpx.HTTPStatusError as exc:
            raise CollectionError("http_error", f"Baidu HTTP {exc.response.status_code}.") from exc
        except httpx.RequestError as exc:
            raise CollectionError("network_error", type(exc).__name__) from exc
