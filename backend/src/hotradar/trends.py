"""Read-only trends from immutable observations; absent samples never become zero."""

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from hotradar.api.queries import dashboard, day_bounds
from hotradar.db.models import (
    Board,
    BoardSnapshot,
    CollectionRun,
    Event,
    EventMember,
    Item,
    SnapshotItem,
    SourceSettingChange,
)

BOARDS = {"douyin-hot", "weibo-hot", "baidu-hot"}
ZONE = ZoneInfo("Asia/Shanghai")
VERSION = "observed-trend-v1"


def window_bounds(start: date, end: date, now: datetime):
    if end < start or (end - start).days > 30 or end > now.astimezone(ZONE).date():
        raise ValueError("选择截至今日的1至31个自然日，开始日期不能晚于结束日期。")
    left, _ = day_bounds(start)
    _, right = day_bounds(end)
    return left, min(right, now)


def pauses(changes, board, left, right):
    # Reconstruct historical settings instead of applying today's pause to older dates.
    enabled = changes[0].before["enabled"] if changes else board.enabled
    cursor = left
    result = []
    for c in changes:
        if c.changed_at <= left:
            enabled = c.after["enabled"]
            continue
        if c.changed_at >= right:
            break
        if not enabled and c.changed_at > cursor:
            result.append({"start": cursor, "end": c.changed_at, "kind": "paused"})
        cursor = c.changed_at
        enabled = c.after["enabled"]
    if not enabled and right > cursor:
        result.append({"start": cursor, "end": right, "kind": "paused"})
    return result


def subtract_spans(left, right, exclusions):
    fragments = [(left, right)] if right > left else []
    for span in exclusions:
        next_fragments = []
        for a, b in fragments:
            x, y = span["start"], span["end"]
            if y <= a or x >= b:
                next_fragments.append((a, b))
            else:
                if a < x:
                    next_fragments.append((a, x))
                if y < b:
                    next_fragments.append((y, b))
        fragments = next_fragments
    return fragments


def source_coverage(board, snapshots, runs, changes, first, prior, start, end, now):
    left, right = window_bounds(start, end, now)
    paused = pauses(changes, board, left, right)
    gaps = list(paused)
    initial_end = min(first, right) if first else right
    before_first = {"start": left, "end": initial_end, "kind": "before_first"}
    if initial_end > left:
        for a, b in subtract_spans(left, initial_end, paused):
            gaps.append({"start": a, "end": b, "kind": "before_first"})
    previous = prior
    for current in snapshots:
        if previous:
            cadence = previous.interval_seconds or 900
            cadence_changes = [
                c
                for c in changes
                if previous.observed_at < c.changed_at <= current.observed_at
                and c.before["interval_seconds"] != c.after["interval_seconds"]
            ]
            if cadence_changes:
                # A timely cadence change may legitimately postpone the next worker run.
                for c in cadence_changes:
                    if (c.changed_at - previous.observed_at).total_seconds() <= cadence * 1.5:
                        cadence = c.after["interval_seconds"]
            if (current.observed_at - previous.observed_at).total_seconds() > cadence * 1.5:
                a = max(left, previous.observed_at + timedelta(seconds=cadence))
                for x, y in subtract_spans(a, current.observed_at, [*paused, before_first]):
                    gaps.append({"start": x, "end": y, "kind": "missing"})
        previous = current
    if previous:
        cadence = previous.interval_seconds or 900
        for c in changes:
            if (
                previous.observed_at < c.changed_at < right
                and (c.changed_at - previous.observed_at).total_seconds() <= cadence * 1.5
            ):
                cadence = c.after["interval_seconds"]
        if (right - previous.observed_at).total_seconds() > cadence * 1.5:
            a = max(left, previous.observed_at + timedelta(seconds=cadence))
            for x, y in subtract_spans(a, right, [*paused, before_first]):
                gaps.append({"start": x, "end": y, "kind": "missing"})
    # A range after the first observation can be entirely unobserved, even without a prior row.
    elif first and first < right:
        for x, y in subtract_spans(max(left, first), right, paused):
            gaps.append({"start": x, "end": y, "kind": "missing"})
    by_day = defaultdict(list)
    runs_by_day = defaultdict(list)
    for s in snapshots:
        by_day[s.observed_at.astimezone(ZONE).date()].append(s)
    for r in runs:
        runs_by_day[(r.started_at or r.created_at).astimezone(ZONE).date()].append(r)
    days = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        rows, tasks = by_day[day], runs_by_day[day]
        a, b = day_bounds(day)
        spans = [g for g in gaps if g["start"] < min(b, right) and g["end"] > a]
        days.append(
            {
                "date": day,
                "snapshots": len(rows) if rows else None,
                "state": "observed" if rows else "unobserved",
                "failed_runs": sum(r.status == "failed" for r in tasks),
                "partial_snapshots": sum(s.status == "partial" for s in rows),
                "cached_snapshots": sum(s.from_cache for s in rows),
                "empty_snapshots": sum(s.item_count == 0 for s in rows),
                "first_observed_at": rows[0].observed_at if rows else None,
                "last_observed_at": rows[-1].observed_at if rows else None,
                "gap_seconds": sum(
                    (min(g["end"], b, right) - max(g["start"], a)).total_seconds() for g in spans
                ),
                "open_day": b > now,
            }
        )
    return {
        "days": days,
        "sampled_days": sum(d["state"] == "observed" for d in days),
        "requested_days": len(days),
        "snapshots": len(snapshots),
        "first_ever_observed_at": first,
        "first_observed_at": snapshots[0].observed_at if snapshots else None,
        "last_observed_at": snapshots[-1].observed_at if snapshots else None,
        "failed_runs": sum(r.status == "failed" for r in runs),
        "partial_snapshots": sum(s.status == "partial" for s in snapshots),
        "cached_snapshots": sum(s.from_cache for s in snapshots),
        "legacy_cadence_snapshots": sum(s.interval_seconds is None for s in snapshots),
        "gaps": sorted(gaps, key=lambda g: g["start"]),
        "definition": (
            "采样日数仅表示当天有实际留存，不表示全天连续覆盖。"
            "缺口按留存周期、及时周期变更、1.5倍容差及实际暂停记录识别；早期未记录周期采用900秒。"
        ),
    }


def load_context(session, start, end, now):
    left, right = window_bounds(start, end, now)
    snapshots = list(
        session.scalars(
            select(BoardSnapshot)
            .where(
                BoardSnapshot.board_id.in_(BOARDS),
                BoardSnapshot.observed_at >= left,
                BoardSnapshot.observed_at < right,
            )
            .order_by(BoardSnapshot.observed_at, BoardSnapshot.id)
        )
    )
    # Use actual attempt start time when present; queued tasks have only created_at.
    runs = list(
        session.scalars(
            select(CollectionRun)
            .where(
                CollectionRun.board_id.in_(BOARDS),
                func.coalesce(CollectionRun.started_at, CollectionRun.created_at) >= left,
                func.coalesce(CollectionRun.started_at, CollectionRun.created_at) < right,
            )
            .order_by(
                func.coalesce(CollectionRun.started_at, CollectionRun.created_at), CollectionRun.id
            )
        )
    )
    settings = list(
        session.scalars(
            select(SourceSettingChange)
            .where(
                SourceSettingChange.board_id.in_(BOARDS),
                SourceSettingChange.changed_at <= now,
            )
            .order_by(SourceSettingChange.changed_at)
        )
    )
    current = dashboard(session, now.astimezone(ZONE).date())
    sources = []
    for board in session.scalars(select(Board).where(Board.id.in_(BOARDS)).order_by(Board.id)):
        source_snapshots = [s for s in snapshots if s.board_id == board.id]
        prior = session.scalar(
            select(BoardSnapshot)
            .where(
                BoardSnapshot.board_id == board.id,
                BoardSnapshot.observed_at < left,
            )
            .order_by(BoardSnapshot.observed_at.desc())
            .limit(1)
        )
        first = session.scalar(
            select(func.min(BoardSnapshot.observed_at)).where(
                BoardSnapshot.board_id == board.id,
            )
        )
        live = next(b for b in current["boards"] if b["id"] == board.id)
        sources.append(
            {
                "id": board.id,
                "platform": board.source_id,
                "name": live["source_name"],
                "state": live["state"],
                "enabled": board.enabled,
                "stale": live["stale"],
                "last_success_at": live["last_success_at"],
                "interval_seconds": board.interval_seconds,
                "metric_name": board.metric_name,
                "metric_label": board.metric_label,
                "coverage": source_coverage(
                    board,
                    source_snapshots,
                    [r for r in runs if r.board_id == board.id],
                    [c for c in settings if c.board_id == board.id],
                    first,
                    prior,
                    start,
                    end,
                    now,
                ),
            }
        )
    return sources, snapshots, runs, left, right


def observed_rows(session, snapshots, event_id=None):
    query = (
        select(Event, EventMember, SnapshotItem)
        .join(EventMember, EventMember.event_id == Event.id)
        .join(SnapshotItem, SnapshotItem.item_id == EventMember.item_id)
        .where(
            SnapshotItem.snapshot_id.in_([s.id for s in snapshots]),
            ~SnapshotItem.flags.contains(["广告"]),
        )
    )
    if event_id is not None:
        query = query.where(Event.id == event_id)
    return session.execute(query).all()


def overview(session, start, end, now=None):
    now = now or datetime.now(UTC)
    sources, snapshots, runs, left, right = load_context(session, start, end, now)
    snapshot_map = {s.id: s for s in snapshots}
    grouped = defaultdict(list)
    for e, member, row in observed_rows(session, snapshots):
        grouped[e.id].append((e, member, row, snapshot_map[row.snapshot_id]))
    choices = []
    for rows in grouped.values():
        event = rows[0][0]
        rows.sort(key=lambda r: r[3].observed_at)
        choices.append(
            {
                "id": event.id,
                "title": event.title,
                "source_titles": sorted({r[2].title for r in rows}),
                "revision": event.revision,
                "platforms": sorted({r[3].board_id.split("-")[0] for r in rows}),
                "members": len({r[1].item_id for r in rows}),
                "followed": event.followed,
                "manual_locked": event.manual_locked,
                "observations": len(rows),
                "first_observed_at": rows[0][3].observed_at,
                "last_observed_at": rows[-1][3].observed_at,
            }
        )
    choices.sort(key=lambda e: (len(e["platforms"]), e["last_observed_at"], e["id"]), reverse=True)
    days = sorted({s.observed_at.astimezone(ZONE).date() for s in snapshots})
    return {
        "start_date": start,
        "end_date": end,
        "window_start": left,
        "window_end": right,
        "read_at": now,
        "timezone": "Asia/Shanghai",
        "version": VERSION,
        "sampled_days": len(days),
        "requested_days": (end - start).days + 1,
        "seven_days_verified": False,  # Seven daily points alone cannot certify continuity.
        "coverage_note": (
            "只显示实际采样；采样日数不是连续覆盖。未上榜、缺失指标、断采、暂停和失败均不填零。"
        ),
        "relationship_note": "使用当前事件成员关系，人工修正会影响历史视图；原始来源记录保留。",
        "sources": sources,
        "events": choices,
    }


def metric_signature(metric):
    return (
        (metric.get("unit"), metric.get("provider"), metric.get("definition")) if metric else None
    )


def event_series(session, event_id, start, end, now=None):
    now = now or datetime.now(UTC)
    event = session.get(Event, event_id)
    if event is None:
        raise LookupError("事件不存在。")
    sources, snapshots, runs, left, right = load_context(session, start, end, now)
    members = list(session.scalars(select(EventMember).where(EventMember.event_id == event_id)))
    if not members:
        raise LookupError("当前事件没有来源成员。")
    rows = {r.id: r for _, _, r in observed_rows(session, snapshots, event_id)}
    row_by_key = {(r.item_id, r.snapshot_id): r for r in rows.values()}
    series = []
    for member in members:
        # Only supported topic boards can create a trend series.

        item = session.get(Item, member.item_id)
        source = next((s for s in sources if s["id"] == item.board_id), None)
        if source is None:
            continue
        frames = [(s.observed_at, "snapshot", s) for s in snapshots if s.board_id == item.board_id]
        frames += [
            ((r.started_at or r.created_at), "run", r)
            for r in runs
            if r.board_id == item.board_id and r.status in {"failed", "running", "queued"}
        ]
        frames.sort(key=lambda f: (f[0], f[1], f[2].id))
        points = []
        previous = None
        for at, kind, frame in frames:
            row = row_by_key.get((item.id, frame.id)) if kind == "snapshot" else None
            point = {
                "observed_at": at,
                "status": "present"
                if row
                else (
                    "failed"
                    if kind == "run" and frame.status == "failed"
                    else "collecting"
                    if kind == "run"
                    else "unknown_partial"
                    if frame.status == "partial"
                    else "not_on_board"
                ),
                "snapshot_id": frame.id if kind == "snapshot" else None,
                "run_id": frame.run_id if kind == "snapshot" else frame.id,
                "evidence_id": row.id if row else None,
                "title": row.title if row else None,
                "url": row.original_url if row else None,
                "rank": row.source_rank if row else None,
                "metric": row.metrics.get(source["metric_name"]) if row else None,
                "snapshot_status": frame.status if kind == "snapshot" else None,
                "from_cache": frame.from_cache if kind == "snapshot" else None,
                "parser_version": frame.parser_version,
                "interval_seconds": (frame.interval_seconds or 900) if kind == "snapshot" else None,
                "connectable_before": False,
            }
            if previous and point["status"] == previous["status"] == "present":
                span = (at - previous["observed_at"]).total_seconds()
                crosses_gap = any(
                    g["start"] < at and g["end"] > previous["observed_at"]
                    for g in source["coverage"]["gaps"]
                )
                point["connectable_before"] = (
                    not crosses_gap
                    and 0
                    < span
                    <= min(point["interval_seconds"], previous["interval_seconds"]) * 1.5
                    and point["snapshot_status"] == previous["snapshot_status"] == "success"
                    and not (point["from_cache"] or previous["from_cache"])
                    and point["parser_version"] == previous["parser_version"]
                )
            points.append(point)
            previous = point
        present = [p for p in points if p["status"] == "present"]
        last = present[-1] if present else None
        signature = metric_signature(last["metric"]) if last else None
        for p in points:
            p["metric_compatible"] = bool(signature and metric_signature(p["metric"]) == signature)
        comparison = {
            "rank_delta": None,
            "metric_delta": None,
            "metric_percent": None,
            "from_evidence_id": None,
            "to_evidence_id": None,
            "window_seconds": None,
            "reason": "末两次采样不满足完整、非缓存、连续且同口径比较条件。",
        }
        if len(points) >= 2 and points[-1]["connectable_before"]:
            a, b = points[-2:]
            comparison.update(
                {
                    "from_evidence_id": a["evidence_id"],
                    "to_evidence_id": b["evidence_id"],
                    "window_seconds": (b["observed_at"] - a["observed_at"]).total_seconds(),
                }
            )
            if a["rank"] is not None and b["rank"] is not None:
                comparison["rank_delta"] = a["rank"] - b["rank"]
            if metric_signature(a["metric"]) == metric_signature(b["metric"]) and a["metric"]:
                old, new = a["metric"].get("value"), b["metric"].get("value")
                if old is not None and new is not None:
                    comparison["metric_delta"] = new - old
                    comparison["metric_percent"] = (
                        round((new - old) / old * 100, 2) if old != 0 else None
                    )
            comparison["reason"] = "仅比较末两次可比源内采样；指标变化不是播放增长或未来预测。"
        series.append(
            {
                "item_id": item.id,
                "board_id": item.board_id,
                "platform": source["platform"],
                "source_name": source["name"],
                "title": last["title"] if last else item.title,
                "metric_name": source["metric_name"],
                "metric_label": source["metric_label"],
                "metric_definition": last["metric"] if last else None,
                "points": points,
                "comparison": comparison,
                "stats": {
                    "observations": len(present),
                    "cached": sum(bool(p["from_cache"]) for p in present),
                    "partial": sum(p["snapshot_status"] == "partial" for p in present),
                    "best_rank": min(
                        (p["rank"] for p in present if p["rank"] is not None), default=None
                    ),
                    "last_rank": last["rank"] if last else None,
                    "last_metric": last["metric"] if last else None,
                    "first_observed_at": present[0]["observed_at"] if present else None,
                    "last_observed_at": last["observed_at"] if last else None,
                    "observed_span_seconds": (
                        last["observed_at"] - present[0]["observed_at"]
                    ).total_seconds()
                    if last
                    else None,
                    "latest_status": points[-1]["status"] if points else "unobserved",
                },
            }
        )
    series.sort(key=lambda s: (s["board_id"], s["item_id"]))
    return {
        "event": {
            "id": event.id,
            "title": event.title,
            "revision": event.revision,
            "manual_locked": event.manual_locked,
        },
        "start_date": start,
        "end_date": end,
        "window_start": left,
        "window_end": right,
        "read_at": now,
        "version": VERSION,
        "sources": sources,
        "series": series,
        "relationship_note": "依据当前事件成员关系回看原始快照，非历史关系版本回放。",
    }
