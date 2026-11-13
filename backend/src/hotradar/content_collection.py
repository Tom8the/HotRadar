"""Automatic content capture only from a locally verified normal platform session."""

import hashlib
import json
import re
from datetime import UTC, datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, text

from hotradar.collectors.platforms import optional_count
from hotradar.collectors.types import CollectionError
from hotradar.db.models import ContentCollectionRun, ContentObservation, ContentSourceSetting
from hotradar.db.session import session_factory
from hotradar.douyin_content import VERSION as DOUYIN_VERSION
from hotradar.douyin_content import fetch_douyin
from hotradar.session_store import client_cookies, session_status

VERSION = "weibo-session-content-v1"
FEED_URL = "https://weibo.com/ajax/feed/hottimeline"
DETAIL_URL = "https://weibo.com/ajax/statuses/show"
FEED_PARAMS = {
    "since_id": "0",
    "refresh": "0",
    "group_id": "102803",
    "containerid": "102803",
    "extparam": "discover|new_feed",
    "max_id": "0",
    "count": "10",
}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def parse_weibo_content(row, observed_at):
    if not isinstance(row, dict) or row.get("isAd") or row.get("ad_info"):
        return None
    user = row.get("user")
    if not isinstance(user, dict):
        return None
    uid = str(user.get("idstr") or user.get("id") or "")
    mid = str(row.get("mblogid") or "")
    author = user.get("screen_name")
    if (
        not uid.isdigit()
        or not re.fullmatch(r"[A-Za-z0-9]{5,20}", mid)
        or not isinstance(author, str)
        or not author.strip()
    ):
        return None
    try:
        published = datetime.strptime(row["created_at"], "%a %b %d %H:%M:%S %z %Y")
    except (KeyError, TypeError, ValueError):
        return None
    if observed_at.tzinfo is None or published > observed_at:
        return None
    title = row.get("text_raw")
    if not isinstance(title, str) or not title.strip():
        if not isinstance(row.get("text"), str):
            return None
        parser = PlainText()
        parser.feed(row["text"])
        title = "".join(parser.parts)
    title = title.strip()
    if not title:
        return None
    metrics = {
        "likes": optional_count(row.get("attitudes_count")),
        "comments": optional_count(row.get("comments_count")),
        "reposts": optional_count(row.get("reposts_count")),
    }
    if all(value is None for value in metrics.values()):
        return None
    raw = {
        key: row.get(key)
        for key in (
            "idstr",
            "mid",
            "mblogid",
            "created_at",
            "text",
            "text_raw",
            "isLongText",
            "isAd",
            "attitudes_count",
            "comments_count",
            "reposts_count",
        )
    }
    raw["user"] = {key: user.get(key) for key in ("id", "idstr", "screen_name", "profile_url")}
    return {
        "source_id": "weibo",
        "external_id": mid,
        "item_kind": "post",
        "original_url": f"https://weibo.com/{uid}/{mid}",
        "author": author.strip(),
        "author_url": f"https://weibo.com/u/{uid}",
        "title": title[:160] + ("…" if len(title) > 160 else ""),
        "title_is_excerpt": bool(row.get("isLongText")) or len(title) > 160,
        "published_at": published.astimezone(UTC).isoformat(),
        "published_at_display": published.astimezone(ZoneInfo("Asia/Shanghai")).strftime(
            "%Y-%m-%d %H:%M:%S"
        ),
        "published_timezone": "Asia/Shanghai",
        "published_precision": "second",
        "observed_at": observed_at.isoformat(),
        "metrics": metrics,
        "metric_definitions": {
            "likes": "微博 attitudes_count 原值，非播放量",
            "comments": "微博 comments_count 原值",
            "reposts": "微博 reposts_count 原值",
        },
        "discovery_label": "微博热门推荐 · 正常会话",
        "discovery_url": "https://weibo.com/",
        "endpoint": FEED_URL,
        "raw_business": raw,
        "verification_note": (
            "正常网页会话接口自动采样；发布时间来自 created_at，保留秒级时区。"
            "热门推荐为平台返回的候选，不是全网排行或已确认爆款。长文可能只返回摘录。"
        ),
    }


async def request_json(client, url, params):
    try:
        response = await client.get(url, params=params)
    except httpx.RequestError as exc:
        raise CollectionError("network_error", type(exc).__name__) from None
    if response.status_code in {301, 302, 303, 307, 308, 401, 403}:
        raise CollectionError("session_required", "Normal platform session is not accepted.")
    if response.status_code != 200:
        raise CollectionError("http_error", f"Source HTTP {response.status_code}.")
    try:
        payload = response.json()
    except ValueError:
        raise CollectionError("session_required", "Source did not return content JSON.") from None
    if not isinstance(payload, dict) or payload.get("ok") != 1:
        raise CollectionError("upstream_rejected", "Source did not accept the content request.")
    return payload, datetime.now(UTC)


async def fetch_weibo(*, verify=False, transport=None, cookies=None):
    if cookies is None:
        try:
            cookies = client_cookies("weibo", scope="weibo-content")
        except (RuntimeError, ValueError, OSError):
            raise CollectionError(
                "session_required", "Local platform session is missing or unavailable."
            ) from None
    async with httpx.AsyncClient(
        cookies=cookies,
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://weibo.com/"},
        follow_redirects=False,
        timeout=20,
        transport=transport,
    ) as client:
        payload, observed = await request_json(client, FEED_URL, FEED_PARAMS)
        if not isinstance(payload.get("statuses"), list):
            raise CollectionError("invalid_shape", "Content statuses list is missing.")
        parsed = [parse_weibo_content(row, observed) for row in payload["statuses"]]
        items = [row for row in parsed if row is not None]
        if payload["statuses"] and not items:
            raise CollectionError("parse_failed", "No complete content passed field validation.")
        if verify:
            if not items:
                raise CollectionError(
                    "verification_incomplete", "An empty feed cannot verify content access."
                )
            detail, detail_observed = await request_json(
                client, DETAIL_URL, {"id": items[0]["external_id"]}
            )
            checked = parse_weibo_content(detail, detail_observed)
            if checked is None or any(
                checked[key] != items[0][key]
                for key in ("external_id", "author_url", "author", "published_at")
            ):
                raise CollectionError(
                    "verification_incomplete", "Feed and detail identity/time do not agree."
                )
        return items, len(parsed) - len(items)


def persist_api_item(session, data, run_id, version=VERSION):
    data = {**data, "run_id": run_id}
    digest = hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    row = ContentObservation(
        source_id=data["source_id"],
        external_id=data["external_id"],
        observed_at=datetime.fromisoformat(data["observed_at"]),
        sha256=digest,
        method="platform-session-api",
        version=version,
        data=data,
    )
    session.add(row)
    session.flush()
    return row.id


async def collect_weibo(job_key, *, verify=False, activate=False, fetcher=fetch_weibo):
    return await collect_content(
        "weibo", job_key, verify=verify, activate=activate, fetcher=fetcher, version=VERSION
    )


async def collect_douyin(job_key, *, verify=False, activate=False, fetcher=fetch_douyin):
    return await collect_content(
        "douyin", job_key, verify=verify, activate=activate, fetcher=fetcher, version=DOUYIN_VERSION
    )


async def collect_content(source_id, job_key, *, verify, activate, fetcher, version):
    # Hold a transaction advisory lock across request and commit; same key never refetches.
    with session_factory().begin() as session:
        lock = session.scalar(
            text("SELECT pg_try_advisory_xact_lock(hashtext(:key))"),
            {"key": f"content-worker:{source_id}"},
        )
        if not lock:
            return {"status": "locked", "source_id": source_id}
        existing = session.scalar(
            select(ContentCollectionRun).where(ContentCollectionRun.job_key == job_key)
        )
        if existing:
            if existing.source_id != source_id:
                return {"status": "key_conflict", "source_id": source_id}
            return {"status": existing.status, "id": existing.id, "reused": True}
        config = session.get(ContentSourceSetting, source_id)
        if config is None or (not verify and (not config.enabled or config.verified_at is None)):
            return {"status": "disabled", "source_id": source_id}
        run = ContentCollectionRun(source_id=source_id, job_key=job_key, version=version)
        session.add(run)
        session.flush()
        try:
            items, rejected = await fetcher(verify=verify)
            with session.begin_nested():
                observation_ids = [persist_api_item(session, row, run.id, version) for row in items]
            run.observation_ids = observation_ids
            run.item_count, run.rejected_count = len(items), rejected
            run.status = "partial" if rejected else "success" if items else "empty"
            if verify:
                config.verified_at = datetime.now(UTC)
                config.verification = {
                    **config.verification,
                    "method": "normal-web-session" if source_id == "weibo" else "normal-sdk-post",
                    "feed_and_detail_verified": source_id == "weibo",
                    "sdk_endpoint_verified": source_id == "douyin",
                    "version": version,
                    "run_id": run.id,
                }
                if activate:
                    config.enabled = True
        except CollectionError as exc:
            run.status, run.error_code = "failed", exc.code
            if exc.code in {"session_required", "upstream_rejected"}:
                config.enabled = False
        except Exception:  # noqa: BLE001 -- keep task history without credential-bearing errors
            run.status, run.error_code = "failed", "internal_error"
        run.finished_at = datetime.now(UTC)
        return {
            "source_id": source_id,
            "status": run.status,
            "id": run.id,
            "item_count": run.item_count,
            "rejected_count": run.rejected_count,
            "error_code": run.error_code,
            "enabled": config.enabled,
        }


async def run_due():
    results = []
    for source_id in ("weibo", "douyin"):
        result = await run_source_due(source_id)
        if result:
            results.append(result)
    return results or None


async def run_source_due(source_id):
    with session_factory()() as session:
        config = session.get(ContentSourceSetting, source_id)
        if config is None or not config.enabled or config.verified_at is None:
            return None
        last = session.scalar(
            select(ContentCollectionRun)
            .where(ContentCollectionRun.source_id == source_id)
            .order_by(ContentCollectionRun.started_at.desc())
            .limit(1)
        )
        now = datetime.now(UTC)
        if last and (now - last.started_at).total_seconds() < config.interval_seconds:
            return None
        bucket = int(now.timestamp()) // config.interval_seconds
    collector = collect_weibo if source_id == "weibo" else collect_douyin
    return await collector(f"content-scheduled:{source_id}:{bucket}")


def source_status(session, sources=("douyin", "weibo")):
    result = []
    for source in sources:
        config = session.get(ContentSourceSetting, source)
        run = session.scalar(
            select(ContentCollectionRun)
            .where(ContentCollectionRun.source_id == source)
            .order_by(ContentCollectionRun.started_at.desc())
            .limit(1)
        )
        result.append(
            {
                "source_id": source,
                "enabled": bool(config and config.enabled and config.verified_at is not None),
                "interval_seconds": config.interval_seconds if config else 1800,
                "verified_at": config.verified_at if config else None,
                "session_saved_locally": session_status(f"{source}-content")["state"] == "saved",
                "session_encrypted": True,
                "automatic_supported": True,
                "last_run": {
                    "id": run.id,
                    "status": run.status,
                    "started_at": run.started_at,
                    "finished_at": run.finished_at,
                    "item_count": run.item_count,
                    "rejected_count": run.rejected_count,
                    "error_code": run.error_code,
                }
                if run
                else None,
            }
        )
    return result
