"""National and Wuxi topic boards from the official Douyin Creator Center."""

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from hotradar.collectors.platforms import optional_count
from hotradar.collectors.types import CollectedItem, CollectionBatch, CollectionError
from hotradar.session_store import client_cookies

VERSION = "douyin-wuxi-v1"
NATIONAL_BOARD = "douyin-creator-hot"
NATIONAL_VERSION = "douyin-creator-hot-v1"
BASE = "https://creator.douyin.com/web/api/creator/material/center/"
PAGE = "https://creator.douyin.com/creator-micro/creative-guidance"
COMMON = {"aid": "2906", "app_name": "aweme_creator_platform", "device_platform": "web"}
CITY = {"code": 320200, "name": "无锡"}
PARAMS = {
    **COMMON,
    "billboard_type": "2",
    "billboard_tag": "0",
    "hot_search_type": "2",
    "city_code": str(CITY["code"]),
}
NATIONAL_PARAMS = {
    **COMMON,
    "billboard_type": "2",
    "billboard_tag": "0",
    "hot_search_type": "1",
}


def accepted(payload):
    if not isinstance(payload, dict) or type(payload.get("status_code")) is not int:
        raise CollectionError("invalid_shape", "City source business status is missing.")
    if payload["status_code"] == 8:
        raise CollectionError("session_required", "Creator Center session needs a normal refresh.")
    if payload["status_code"] != 0:
        raise CollectionError("upstream_rejected", "City source request was rejected.")


def parse_wuxi(payload, observed_at, city):
    if city != CITY:
        raise CollectionError("city_unverified", "Wuxi city configuration was not verified.")
    return parse_creator_board(payload, observed_at, city=city)


def parse_creator_board(payload, observed_at, *, city=None):
    accepted(payload)
    metric = "city_heat" if city else "topic_heat"
    unit = "同城话题热度" if city else "创作热点热度"
    board_name = "无锡同城榜" if city else "全国创作热点榜"
    rows = payload.get("item_list")
    if not isinstance(rows, list):
        raise CollectionError("invalid_shape", "Creator Center topic list is missing.")
    items, business, seen = [], [], set()
    rejected = 0
    for index, row in enumerate(rows):
        # Preserve the business fields of rejected rows too, for partial-parse review.
        business.append(
            {
                key: row.get(key)
                for key in ("query_id", "title", "rank", "rank_diff", "hot_score", "category")
            }
            if isinstance(row, dict)
            else {"invalid_row": True}
        )
        if not isinstance(row, dict):
            rejected += 1
            continue
        external_id, title = row.get("query_id"), row.get("title")
        if (
            not isinstance(external_id, str)
            or not external_id.isdigit()
            or external_id in seen
            or not isinstance(title, str)
            or not title.strip()
        ):
            rejected += 1
            continue
        seen.add(external_id)
        rank = optional_count(row.get("rank"))
        items.append(
            CollectedItem(
                external_id=external_id,
                title=title.strip(),
                original_url=f"https://www.douyin.com/hot/{external_id}",
                source_rank=rank if rank and rank > 0 else None,
                display_order=index + 1,
                metrics={
                    metric: {
                        "value": optional_count(row.get("hot_score")),
                        "unit": unit,
                        "provider": "douyin-creator",
                        "definition": (
                            f"抖音创作者中心{board_name} hot_score 原值，非播放量。"
                            "rank 保留来源原值"
                        ),
                    }
                },
                flags=[row["category"]] if isinstance(row.get("category"), str) else [],
            )
        )
    if rows and not items:
        raise CollectionError("parse_failed", "All Creator Center topics failed validation.")
    updated = None
    try:
        updated = (
            datetime.strptime(str(payload.get("update_time")), "%Y%m%d%H%M%S")
            .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            .astimezone(UTC)
        )
    except (ValueError, TypeError):
        pass
    return CollectionBatch(
        items=items,
        observed_at=observed_at,
        parser_version=VERSION if city else NATIONAL_VERSION,
        upstream_updated_at=updated,
        rejected_count=rejected,
        raw_payload={
            "request": {
                "endpoint": BASE + "billboard/",
                "params": PARAMS if city else NATIONAL_PARAMS,
            },
            **({"city": city} if city else {"region": "national"}),
            "response": {
                "status_code": 0,
                "update_time": payload.get("update_time"),
                "item_list": business,
            },
        },
    )


class DouyinWuxiCollector:
    scope = "douyin-wuxi"
    city = CITY

    def __init__(self, timeout=20, transport=None, cookies=None):
        self.timeout, self.transport, self.cookies = timeout, transport, cookies

    async def collect(self):
        try:
            cookies = (
                self.cookies
                if self.cookies is not None
                else client_cookies("douyin", scope=self.scope)
            )
        except (RuntimeError, ValueError, OSError):
            raise CollectionError(
                "session_required", "Local Douyin session is unavailable."
            ) from None
        try:
            async with httpx.AsyncClient(
                cookies=cookies,
                transport=self.transport,
                timeout=self.timeout,
                follow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0", "Referer": PAGE},
            ) as client:
                if self.city:
                    config = await client.get(BASE + "config/", params=COMMON)
                    config.raise_for_status()
                    configuration = config.json()
                    accepted(configuration)
                    cities = configuration.get("city_code_list")
                    if not isinstance(cities, list) or self.city not in cities:
                        raise CollectionError(
                            "city_unverified", "Wuxi is absent from source city list."
                        )
                response = await client.get(
                    BASE + "billboard/", params=PARAMS if self.city else NATIONAL_PARAMS
                )
                response.raise_for_status()
                batch = parse_creator_board(response.json(), datetime.now(UTC), city=self.city)
                return replace(batch, from_cache=bool(optional_count(response.headers.get("Age"))))
        except httpx.HTTPStatusError as exc:
            if exc.response.is_redirect or exc.response.status_code in {401, 403}:
                raise CollectionError(
                    "session_required", "Creator Center session rejected."
                ) from None
            raise CollectionError(
                "http_error", f"Creator Center HTTP {exc.response.status_code}."
            ) from None
        except httpx.RequestError as exc:
            raise CollectionError("network_error", type(exc).__name__) from None
        except ValueError:
            raise CollectionError(
                "invalid_json", "Creator Center did not return business JSON."
            ) from None


class DouyinCreatorNationalCollector(DouyinWuxiCollector):
    scope = NATIONAL_BOARD
    city = None
