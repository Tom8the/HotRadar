"""Normal mobile Weibo city hot search, guarded by response city evidence."""

import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from hotradar.collectors.platforms import optional_count
from hotradar.collectors.types import CollectedItem, CollectionBatch, CollectionError
from hotradar.session_store import client_cookies

VERSION = "weibo-wuxi-v1"
CITY = {"code": "800863202", "name": "无锡"}
ENDPOINT = "https://m.weibo.cn/api/container/getIndex"
CONTAINER = "106003type=25&t=3&disable_hot=1&filter_type=region"
PARAMS = {"containerid": CONTAINER}
PAGE = "https://m.weibo.cn/p/index?" + urlencode(PARAMS)


def parse_wuxi(payload, observed_at):
    if not isinstance(payload, dict) or type(payload.get("ok")) is not int or payload["ok"] != 1:
        raise CollectionError("session_required", "Mobile Weibo session was not accepted.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise CollectionError("invalid_shape", "City business response is missing.")
    info, cards = data.get("cardlistInfo"), data.get("cards")
    if not isinstance(info, dict) or not isinstance(cards, list):
        raise CollectionError("invalid_shape", "City card list is missing.")
    if info.get("containerid") != CONTAINER:
        raise CollectionError("city_unverified", "Response is not the city hot search container.")
    heads = info.get("cardlist_head_cards", [])
    if not isinstance(heads, list) or any(
        not isinstance(head, dict) or not isinstance(head.get("channel_list", []), list)
        for head in heads
    ):
        raise CollectionError("invalid_shape", "City channel evidence is missing.")
    channels = [
        c
        for head in heads
        if isinstance(head, dict)
        for c in head.get("channel_list", [])
        if isinstance(c, dict) and c.get("containerid") == CONTAINER
    ]
    if len(channels) != 1 or channels[0].get("name") != CITY["name"]:
        raise CollectionError("city_unverified", "The normal session is not set to Wuxi.")
    rows = []
    for card in cards:
        if not isinstance(card, dict) or not isinstance(card.get("card_group"), list):
            raise CollectionError("invalid_shape", "City card group is missing.")
        rows.extend(
            row
            for row in card["card_group"]
            if isinstance(row, dict) and row.get("card_type") == 25
        )
        if any(isinstance(row, dict) and row.get("card_type") == 42 for row in card["card_group"]):
            raise CollectionError("city_unverified", "City location is unavailable.")
    items, evidence, seen = [], [], set()
    rejected = 0
    for index, row in enumerate(rows):
        scheme = row.get("scheme")
        parts = urlsplit(scheme) if isinstance(scheme, str) else None
        params = parse_qs(parts.query) if parts else {}
        ext = parse_qs(params.get("extparam", [""])[0])
        if ext.get("_position") != [CITY["code"]] or ext.get("filter_type") != ["hotregion"]:
            raise CollectionError("city_unverified", "Topic city evidence is not Wuxi.")
        rank_match = re.search(r"(?:^|\|)pos:1_(\d+)(?:\||$)", str(row.get("itemid", "")))
        rank = int(rank_match[1]) if rank_match else None
        description = row.get("desc")
        heat_match = re.fullmatch(
            r"同城热度\s+(\d+)\s*\|\s*@(.+)",
            description if isinstance(description, str) else "",
        )
        heat = int(heat_match[1]) if heat_match else None
        title = row.get("title_sub")
        topic = parse_qs(params.get("containerid", [""])[0]).get("q", [None])[0]
        canonical = (
            ("https://m.weibo.cn/search?" + urlencode({"containerid": params["containerid"][0]}))
            if topic
            else None
        )
        evidence.append(
            {
                "title_sub": title,
                "description": description,
                "rank": rank,
                "city_code": ext["_position"][0],
                "url": canonical,
            }
        )
        if (
            not parts
            or parts.scheme != "https"
            or parts.netloc != "m.weibo.cn"
            or parts.path != "/search"
            or not isinstance(title, str)
            or not title.strip()
            or not topic
            or topic.strip("#") != title
            or rank is None
            or rank <= 0
            or topic in seen
        ):
            rejected += 1
            continue
        seen.add(topic)
        items.append(
            CollectedItem(
                external_id=hashlib.sha256(topic.encode()).hexdigest(),
                title=title,
                original_url=canonical,
                source_rank=rank,
                display_order=index + 1,
                metrics={
                    "city_heat": {
                        "value": heat,
                        "unit": "同城热度",
                        "provider": "weibo-mobile",
                        "definition": (
                            "微博移动端无锡同城热搜 desc 的同城热度原值，非全国热搜指数或播放量"
                        ),
                    }
                },
            )
        )
    if rows and not items:
        raise CollectionError("parse_failed", "All city topics failed validation.")
    return CollectionBatch(
        items=items,
        observed_at=observed_at,
        parser_version=VERSION,
        rejected_count=rejected,
        raw_payload={
            "request": {"endpoint": ENDPOINT, "params": PARAMS},
            "city": CITY,
            "response": {
                "ok": 1,
                "containerid": CONTAINER,
                "city_name": channels[0]["name"],
                "topics": evidence,
            },
        },
    )


class WeiboWuxiCollector:
    def __init__(self, timeout=20, transport=None, cookies=None):
        self.timeout, self.transport, self.cookies = timeout, transport, cookies

    async def collect(self):
        try:
            cookies = (
                self.cookies
                if self.cookies is not None
                else client_cookies("weibo", scope="weibo-wuxi")
            )
        except (RuntimeError, ValueError, OSError):
            raise CollectionError(
                "session_required", "Local Weibo session is unavailable."
            ) from None
        try:
            async with httpx.AsyncClient(
                cookies=cookies,
                transport=self.transport,
                timeout=self.timeout,
                follow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0", "Referer": PAGE},
            ) as client:
                response = await client.get(ENDPOINT, params=PARAMS)
                if response.status_code in {301, 302, 401, 403, 432}:
                    raise CollectionError(
                        "session_required", "Mobile Weibo requires login refresh."
                    )
                response.raise_for_status()
                batch = parse_wuxi(response.json(), datetime.now(UTC))
                return replace(batch, from_cache=bool(optional_count(response.headers.get("Age"))))
        except httpx.HTTPStatusError as exc:
            raise CollectionError(
                "http_error", f"City source HTTP {exc.response.status_code}."
            ) from None
        except httpx.RequestError as exc:
            raise CollectionError("network_error", type(exc).__name__) from None
        except ValueError:
            raise CollectionError(
                "invalid_json", "City source did not return business JSON."
            ) from None
