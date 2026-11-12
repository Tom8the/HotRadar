"""The public selected-content POST endpoint observed in the normal page SDK."""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx

from hotradar.collectors.platforms import optional_count
from hotradar.collectors.types import CollectionError
from hotradar.session_store import client_cookies

VERSION = "douyin-selected-session-content-v1"
FEED_URL = "https://www.douyin.com/aweme/v2/web/module/feed/"
# These non-authentication parameters came from the page's selected/all SDK request.
FEED_PARAMS = {
    "device_platform": "webapp",
    "aid": "6383",
    "channel": "channel_pc_web",
    "module_id": "3003101",
    "tag_id": "",
    "count": "20",
}
STAT_FIELDS = {
    "likes": "digg_count",
    "comments": "comment_count",
    "favorites": "collect_count",
    "shares": "share_count",
}


def parse_douyin_content(row, observed_at):
    if not isinstance(row, dict) or row.get("is_ads") or row.get("aweme_type") not in (0, 4):
        return None
    author, stats = row.get("author"), row.get("statistics")
    if not isinstance(author, dict) or not isinstance(stats, dict):
        return None
    external_id, sec_uid = row.get("aweme_id"), author.get("sec_uid")
    nickname, title, created = author.get("nickname"), row.get("desc"), row.get("create_time")
    if (
        not isinstance(external_id, str)
        or not re.fullmatch(r"\d{15,25}", external_id)
        or not isinstance(sec_uid, str)
        or not re.fullmatch(r"[\w-]{10,200}", sec_uid, re.ASCII)
        or not isinstance(nickname, str)
        or not nickname.strip()
        or not isinstance(title, str)
        or not title.strip()
        or type(created) is not int
        or created <= 0
        or observed_at.tzinfo is None
    ):
        return None
    try:
        published = datetime.fromtimestamp(created, UTC)
    except (ValueError, OSError, OverflowError):
        return None
    if published > observed_at:
        return None
    metrics = {name: optional_count(stats.get(key)) for name, key in STAT_FIELDS.items()}
    if all(value is None for value in metrics.values()):
        return None
    raw = {key: row.get(key) for key in ("aweme_id", "desc", "create_time", "aweme_type", "is_ads")}
    raw["author"] = {key: author.get(key) for key in ("uid", "sec_uid", "nickname")}
    raw["statistics"] = {key: stats.get(key) for key in STAT_FIELDS.values()}
    return {
        "source_id": "douyin",
        "external_id": external_id,
        "item_kind": "video",
        "original_url": "https://www.douyin.com/video/" + external_id,
        "author": nickname.strip(),
        "author_url": "https://www.douyin.com/user/" + sec_uid,
        "title": title.strip()[:160] + ("…" if len(title.strip()) > 160 else ""),
        "title_is_excerpt": len(title.strip()) > 160 or "升级后可展示全部信息" in title,
        "published_at": published.isoformat(),
        "published_at_display": published.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "published_timezone": "Asia/Shanghai",
        "published_precision": "second",
        "observed_at": observed_at.isoformat(),
        "metrics": metrics,
        "metric_definitions": {
            name: f"抖音 statistics.{key} 原值，非播放量" for name, key in STAT_FIELDS.items()
        },
        "discovery_label": "抖音精选 · 全部分类 · 正常会话",
        "discovery_url": "https://www.douyin.com/jingxuan",
        "endpoint": FEED_URL,
        "request_method": "POST",
        "raw_business": raw,
        "verification_note": (
            "正常页面SDK确认的精选全部分类接口，使用本机会话自动请求。"
            "发布时间来自create_time秒级时间，互动为statistics原值。"
            "精选推荐可能个性化且含旧内容，不代表全平台排行或已确认爆款。"
            "播放量未单独验证，保持缺失。"
        ),
    }


async def fetch_douyin(*, verify=False, transport=None, cookies=None, uifid=None):
    if cookies is None:
        try:
            cookies = client_cookies("douyin", scope="douyin-content")
        except (RuntimeError, ValueError, OSError):
            raise CollectionError("session_required", "Platform session is unavailable.") from None
    if uifid is None and isinstance(cookies, httpx.Cookies):
        request = httpx.Request("POST", FEED_URL)
        cookies.set_cookie_header(request)
        for part in request.headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name.lower() == "uifid":
                uifid = value
                break
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.douyin.com/jingxuan"}
    if uifid:
        headers["uifid"] = uifid
    async with httpx.AsyncClient(
        cookies=cookies, headers=headers, follow_redirects=False, timeout=20, transport=transport
    ) as client:
        try:
            response = await client.post(FEED_URL, params=FEED_PARAMS)
        except httpx.RequestError as exc:
            raise CollectionError("network_error", type(exc).__name__) from None
    if response.status_code in {301, 302, 303, 307, 308, 401, 403}:
        raise CollectionError("session_required", "Normal session was not accepted.")
    if response.status_code != 200:
        raise CollectionError("http_error", f"Source HTTP {response.status_code}.")
    try:
        payload = response.json()
    except ValueError:
        raise CollectionError("session_required", "Source did not return business JSON.") from None
    if not isinstance(payload, dict) or type(payload.get("status_code")) is not int:
        raise CollectionError("invalid_shape", "Source business status is missing.")
    if payload["status_code"] != 0:
        raise CollectionError("upstream_rejected", "Source business request was rejected.")
    if not isinstance(payload.get("aweme_list"), list):
        raise CollectionError("invalid_shape", "Source content list is missing.")
    observed = datetime.now(UTC)
    items = [parse_douyin_content(row, observed) for row in payload["aweme_list"]]
    accepted = [row for row in items if row is not None]
    if items and not accepted:
        raise CollectionError("parse_failed", "No complete real content passed validation.")
    if verify and not accepted:
        raise CollectionError("verification_incomplete", "Empty source cannot verify access.")
    return accepted, len(items) - len(accepted)
