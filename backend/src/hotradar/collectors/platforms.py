from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from hotradar.collectors.types import CollectedItem, CollectionBatch, CollectionError
from hotradar.session_store import cookie_jar, read_session

WEIBO_URL = "https://weibo.com/ajax/side/hotSearch"
DOUYIN_URL = (
    "https://www.douyin.com/aweme/v1/web/hot/search/list/"
    "?device_platform=webapp&aid=6383&channel=channel_pc_web&detail_list=1"
)
SECRET_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "token",
    "access_token",
    "refresh_token",
    "passport_csrf_token",
    "headers",
}


def sanitize_payload(value):
    if isinstance(value, dict):
        return {
            k: sanitize_payload(v) for k, v in value.items() if str(k).lower() not in SECRET_KEYS
        }
    if isinstance(value, list):
        return [sanitize_payload(x) for x in value]
    return value


def optional_count(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number >= 0 else None


def optional_timestamp(value) -> datetime | None:
    try:
        stamp = int(value)
        if stamp <= 0:
            return None
        return datetime.fromtimestamp(stamp, UTC)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _rows(payload, source):
    if not isinstance(payload, dict):
        raise CollectionError("invalid_shape", "Response root is not an object.")
    if source == "weibo" and payload.get("ok") != 1:
        raise CollectionError("upstream_rejected", "Weibo did not accept the request.")
    if source == "douyin" and payload.get("status_code") != 0:
        raise CollectionError("upstream_rejected", "Douyin did not accept the request.")
    data = payload.get("data")
    key = "realtime" if source == "weibo" else "word_list"
    if not isinstance(data, dict) or not isinstance(data.get(key), list):
        raise CollectionError("invalid_shape", f"Required {key} list is missing.")
    return data[key]


def parse_weibo(payload: dict, observed_at: datetime) -> CollectionBatch:
    rows = _rows(payload, "weibo")
    items = []
    rejected = 0
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            rejected += 1
            continue
        title = row.get("word", "").strip() if isinstance(row.get("word"), str) else ""
        if not title or title in seen:
            rejected += 1
            continue
        seen.add(title)
        is_ad = bool(row.get("is_ad") or row.get("ad_info"))
        rank_raw = optional_count(row.get("rank"))
        rank = None if is_ad else (rank_raw + 1 if rank_raw is not None else None)
        flags = ["广告"] if is_ad else []
        label = row.get("label_name")
        if isinstance(label, str) and label.strip():
            flags.append(label.strip())
        metric = optional_count(row.get("num"))
        items.append(
            CollectedItem(
                external_id=title,
                title=title,
                original_url="https://s.weibo.com/weibo?"
                + urlencode(
                    {
                        "q": row.get("word_scheme") or title,
                        "Refer": "top",
                    }
                ),
                source_rank=rank,
                display_order=index + 1,
                metrics={
                    "search_heat": {
                        "value": metric,
                        "unit": "热搜指数",
                        "provider": "weibo",
                        "definition": "微博热搜接口 num 原值，不是播放量",
                    }
                },
                flags=flags,
                is_pinned=bool(row.get("is_top")),
                source_event_at=optional_timestamp(row.get("onboard_time")),
            )
        )
    if rows and not items:
        raise CollectionError("parse_failed", "All Weibo rows failed validation.")
    return CollectionBatch(
        items,
        sanitize_payload(payload),
        observed_at,
        "weibo-hot-v1",
        rejected_count=rejected,
    )


def parse_douyin(payload: dict, observed_at: datetime) -> CollectionBatch:
    rows = _rows(payload, "douyin")
    items = []
    rejected = 0
    seen = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            rejected += 1
            continue
        title = row.get("word", "").strip() if isinstance(row.get("word"), str) else ""
        external_id = str(row.get("sentence_id") or "").strip()
        if not title or not external_id or external_id in seen or not external_id.isdigit():
            rejected += 1
            continue
        seen.add(external_id)
        rank = optional_count(row.get("position"))
        if rank is not None and rank < 1:
            rank = None
        items.append(
            CollectedItem(
                external_id=external_id,
                title=title,
                original_url=f"https://www.douyin.com/hot/{external_id}",
                source_rank=rank,
                display_order=index + 1,
                metrics={
                    "topic_heat": {
                        "value": optional_count(row.get("hot_value")),
                        "unit": "话题热度",
                        "provider": "douyin",
                        "definition": "抖音 hot_value 原值，不是视频播放量",
                    }
                },
                source_event_at=optional_timestamp(row.get("event_time")),
            )
        )
    if rows and not items:
        raise CollectionError("parse_failed", "All Douyin rows failed validation.")
    return CollectionBatch(
        items,
        sanitize_payload(payload),
        observed_at,
        "douyin-hot-v1",
        rejected_count=rejected,
    )


class PlatformCollector:
    def __init__(self, source: str, timeout=20, transport=None, cookies=None):
        if source not in {"weibo", "douyin"}:
            raise ValueError("Unregistered source")
        self.source = source
        self.timeout = timeout
        self.transport = transport
        self.cookies = cookies

    async def collect(self) -> CollectionBatch:
        if self.source != "douyin" or self.cookies is not None:
            return await self._collect(self.cookies)
        try:
            anonymous = await self._collect(None)
            if anonymous.items:
                return anonymous
        except CollectionError as exc:
            if exc.code == "network_error":
                raise
            cookies = self._fallback_cookies()
            if cookies is None:
                raise
        else:
            cookies = self._fallback_cookies()
            if cookies is None:
                return anonymous
        return await self._collect(cookies)

    @staticmethod
    def _fallback_cookies():
        try:
            session = read_session("douyin-hot", allow_legacy=False)
        except (RuntimeError, ValueError, OSError):
            raise CollectionError("session_required", "Douyin session is unavailable.") from None
        if session is None:
            return None
        cookies = cookie_jar(session["cookies"])
        if not cookies.jar:
            raise CollectionError("session_required", "Douyin session has expired.")
        return cookies

    async def _collect(self, cookies) -> CollectionBatch:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.weibo.com/"
            if self.source == "weibo"
            else "https://www.douyin.com/",
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=5),
                headers=headers,
                cookies=cookies,
                follow_redirects=False,
                transport=self.transport,
            ) as client:
                if self.source == "douyin" and cookies is None:
                    # Anonymous, server-issued temporary cookies remain in this client only.
                    response = await client.get(
                        "https://www.douyin.com/passport/general/login_guiding_strategy/?aid=6383"
                    )
                    response.raise_for_status()
                response = await client.get(WEIBO_URL if self.source == "weibo" else DOUYIN_URL)
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError as exc:
                    if cookies is not None:
                        raise CollectionError(
                            "session_required", "Douyin did not return business JSON."
                        ) from None
                    raise CollectionError("invalid_json", "Source did not return JSON.") from exc
                observed_at = datetime.now(UTC)
                batch = (
                    parse_weibo(payload, observed_at)
                    if self.source == "weibo"
                    else parse_douyin(payload, observed_at)
                )
                if cookies is not None:
                    fields = ("sentence_id", "word", "position", "hot_value", "event_time")
                    batch = replace(
                        batch,
                        raw_payload={
                            "status_code": 0,
                            "data": {
                                "word_list": [
                                    {key: row.get(key) for key in fields}
                                    if isinstance(row, dict)
                                    else {"invalid_row": True}
                                    for row in payload["data"]["word_list"]
                                ]
                            },
                        },
                    )
                age = optional_count(response.headers.get("Age"))
                return replace(batch, from_cache=bool(age and age > 0))
        except httpx.HTTPStatusError as exc:
            if cookies is not None and (
                exc.response.is_redirect or exc.response.status_code in {401, 403}
            ):
                raise CollectionError("session_required", "Douyin session rejected.") from None
            raise CollectionError("http_error", f"Source HTTP {exc.response.status_code}.") from exc
        except httpx.RequestError as exc:
            raise CollectionError("network_error", type(exc).__name__) from exc
