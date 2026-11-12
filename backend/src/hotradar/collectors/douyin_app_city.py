"""Native city ranking through Douyin's official search landing-page service."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from hotradar.collectors.platforms import optional_count, optional_timestamp
from hotradar.collectors.types import CollectedItem, CollectionBatch, CollectionError

BOARD_ID = "douyin-wuxi-app"
VERSION = "douyin-wuxi-app-v1"
ENDPOINT = "https://so-landing.douyin.com/aweme/v1/hot/search/list/"
PAGE = "https://so-landing.douyin.com/landings/hotlist"
PARAMS = {
    "detail_list": "1",
    "board_type": "1",
    "board_sub_type": "320200",
    "need_board_tab": "true",
    "aid": "581610",
    "version_code": "32.3.0",
}
ROW_FIELDS = (
    "sentence_id",
    "group_id",
    "word",
    "position",
    "hot_value",
    "event_time",
    "label",
    "word_type",
    "hotlist_param",
)


def parse_app_wuxi(payload, observed_at):
    if not isinstance(payload, dict) or type(payload.get("status_code")) is not int:
        raise CollectionError("invalid_shape", "City ranking business status is missing.")
    if payload["status_code"] != 0:
        raise CollectionError("upstream_rejected", "City ranking request was rejected.")
    data, tabs = payload.get("data"), payload.get("board_tabs")
    if (
        not isinstance(data, dict)
        or data.get("city_code") != "320200"
        or data.get("city_name") != "无锡"
        or not isinstance(tabs, list)
        or not any(
            isinstance(tab, dict)
            and tab.get("board_type") == 1
            and tab.get("board_sub_type") == "320200"
            and tab.get("tab_name") == "无锡榜"
            for tab in tabs
        )
    ):
        raise CollectionError("city_unverified", "Wuxi ranking identity was not verified.")
    rows = data.get("word_list")
    if not isinstance(rows, list):
        raise CollectionError("invalid_shape", "City ranking word list is missing.")
    items, business, seen, ranks = [], [], set(), set()
    rejected = 0
    for index, row in enumerate(rows):
        business.append(
            {key: row.get(key) for key in ROW_FIELDS}
            if isinstance(row, dict)
            else {"invalid_row": True}
        )
        if not isinstance(row, dict):
            rejected += 1
            continue
        # A city-name echo alone can accompany national results on the PC API.
        # Require the per-row city/rank context from the verified landing service.
        try:
            context = json.loads(row.get("hotlist_param", ""))
        except (ValueError, TypeError):
            context = None
        rank = optional_count(row.get("position"))
        if (
            not isinstance(context, dict)
            or context.get("board_type") != 1
            or context.get("board_sub_type") != "320200"
            or context.get("rank") != rank
        ):
            raise CollectionError("city_unverified", "A topic has unverified city ranking context.")
        external_id, title = row.get("sentence_id"), row.get("word")
        if (
            not isinstance(external_id, str)
            or not external_id.isdigit()
            or external_id in seen
            or not isinstance(title, str)
            or not title.strip()
            or rank is None
            or rank < 1
            or rank in ranks
        ):
            rejected += 1
            continue
        seen.add(external_id)
        ranks.add(rank)
        items.append(
            CollectedItem(
                external_id=external_id,
                title=title.strip(),
                original_url=f"https://www.douyin.com/hot/{external_id}",
                source_rank=rank,
                display_order=index + 1,
                source_event_at=optional_timestamp(row.get("event_time")),
                metrics={
                    "city_heat": {
                        "value": optional_count(row.get("hot_value")),
                        "unit": "同城热度",
                        "provider": "douyin-search-landing",
                        "definition": "抖音原生无锡榜 hot_value 原值，非播放量或具体视频互动量",
                    }
                },
            )
        )
    if rows and not items:
        raise CollectionError("parse_failed", "All city ranking rows failed validation.")
    updated = None
    try:
        updated = (
            datetime.strptime(data.get("active_time"), "%Y-%m-%d %H:%M:%S")
            .replace(tzinfo=ZoneInfo("Asia/Shanghai"))
            .astimezone(UTC)
        )
    except (ValueError, TypeError):
        pass
    return CollectionBatch(
        items=items,
        observed_at=observed_at,
        parser_version=VERSION,
        upstream_updated_at=updated,
        rejected_count=rejected,
        raw_payload={
            "request": {"endpoint": ENDPOINT, "params": PARAMS},
            "response": {
                "status_code": 0,
                "board_tabs": [
                    {key: tab.get(key) for key in ("board_type", "board_sub_type", "tab_name")}
                    for tab in tabs
                    if isinstance(tab, dict)
                ],
                "data": {
                    "city_code": data["city_code"],
                    "city_name": data["city_name"],
                    "active_time": data.get("active_time"),
                    "word_list": business,
                },
            },
        },
    )


class DouyinAppWuxiCollector:
    def __init__(self, timeout=20, transport=None):
        self.timeout, self.transport = timeout, transport

    async def collect(self):
        try:
            # This public landing service needs no local account Cookies.
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5),
                transport=self.transport,
                follow_redirects=False,
                headers={"User-Agent": "Mozilla/5.0", "Referer": PAGE},
            ) as client:
                response = await client.get(ENDPOINT, params=PARAMS)
                response.raise_for_status()
                batch = parse_app_wuxi(response.json(), datetime.now(UTC))
                return replace(batch, from_cache=bool(optional_count(response.headers.get("Age"))))
        except httpx.HTTPStatusError as exc:
            raise CollectionError(
                "http_error", f"City ranking HTTP {exc.response.status_code}."
            ) from None
        except httpx.RequestError as exc:
            raise CollectionError("network_error", type(exc).__name__) from None
        except ValueError:
            raise CollectionError("invalid_json", "City ranking did not return JSON.") from None
