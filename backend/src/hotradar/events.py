"""Conservative topic association and extractive summaries; no invented news facts."""

import argparse
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from datetime import timedelta
from difflib import SequenceMatcher
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text

from hotradar.api.queries import day_bounds
from hotradar.db.models import (
    Board,
    BoardSnapshot,
    Event,
    EventChange,
    EventMember,
    EventProcessedSnapshot,
    EventSummary,
    Item,
    SnapshotItem,
    new_id,
)
from hotradar.db.session import session_factory

ALGORITHM = "title-conservative-v1"
SUMMARY_VERSION = "source-extractive-v2"
BOARDS = {"douyin-hot", "weibo-hot", "baidu-hot"}
LOCK = 418831102


class EventConflict(ValueError):
    pass


def lock_events(session):
    # Shared by ingestion and every correction: no half-applied memberships.
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK})


def normalize(title):
    return re.sub(r"[^\w\u4e00-\u9fff]", "", unicodedata.normalize("NFKC", title).casefold())


def similarity(left, right):
    a, b = normalize(left), normalize(right)
    if not a or not b:
        return 0.0, "标题不足"
    match = SequenceMatcher(None, a, b, autojunk=False)
    if re.findall(r"\d+", a) != re.findall(r"\d+", b):
        return min(round(match.ratio(), 3), 0.89), "数字不同，必须人工核对"
    if a == b and len(a) >= 6:
        return 1.0, "规范化标题一致"
    short, long = sorted((a, b), key=len)
    if len(short) >= 8 and short in long and len(short) / len(long) >= 0.6:
        return 0.96, "完整长主题包含，需核对事件动作"
    common = match.find_longest_match().size
    ratio = match.ratio()
    # Generic shared names/keywords alone cannot create an automatic association.
    if min(len(a), len(b)) >= 10 and common >= 8 and ratio >= 0.92:
        return round(ratio, 3), "长标题高度一致"
    return (min(round(ratio, 3), 0.89) if common >= 4 else 0.0), "部分文字相似，仅供人工核对"


def evidence_rows(session, day=None, event_ids=None):
    query = (
        select(EventMember, SnapshotItem, BoardSnapshot, Board, Item)
        .join(SnapshotItem, SnapshotItem.item_id == EventMember.item_id)
        .join(BoardSnapshot, BoardSnapshot.id == SnapshotItem.snapshot_id)
        .join(Board, Board.id == BoardSnapshot.board_id)
        .join(Item, Item.id == EventMember.item_id)
        .where(Board.id.in_(BOARDS), ~SnapshotItem.flags.contains(["广告"]))
        .order_by(BoardSnapshot.observed_at, SnapshotItem.id)
    )
    if day is not None:
        start, end = day_bounds(day)
        query = query.where(BoardSnapshot.observed_at >= start, BoardSnapshot.observed_at < end)
    if event_ids is not None:
        query = query.where(EventMember.event_id.in_(event_ids))
    return session.execute(query).all()


def summarize(event, rows):
    latest = {}
    for member, row, snapshot, board, item in rows:
        latest[item.id] = (member, row, snapshot, board, item)
    evidence = []
    for member, row, snapshot, board, item in latest.values():
        evidence.append(
            {
                "id": row.id,
                "item_id": item.id,
                "snapshot_id": snapshot.id,
                "board_id": board.id,
                "platform": board.source_id,
                "source_name": {
                    "douyin": "抖音", "weibo": "微博", "baidu": "百度",
                }[board.source_id],
                "title": row.title,
                "url": row.original_url,
                "rank": row.source_rank,
                "metrics": row.metrics,
                "metric_name": board.metric_name,
                "metric_label": board.metric_label,
                "observed_at": snapshot.observed_at.isoformat(),
                "source_event_at": row.source_event_at.isoformat() if row.source_event_at else None,
                "first_observed_at": item.first_observed_at.isoformat(),
                "snapshot_status": snapshot.status,
                "from_cache": snapshot.from_cache,
                "flags": row.flags,
                "basis": member.basis,
            }
        )
    evidence.sort(key=lambda e: (e["platform"], e["item_id"]))
    lines = [
        {"text": f"{e['source_name']}热榜标题：{e['title']}", "evidence_id": e["id"]}
        for e in evidence
    ]
    data = {
        "provider": "local-extractive",
        "version": SUMMARY_VERSION,
        "model": None,
        "input_ids": [e["id"] for e in evidence],
        "lines": lines,
        "lead": (
            f"本机在选定日期观测到{len({e['platform'] for e in evidence})}个平台、"
            f"{len(evidence)}条关联话题，共{len(rows)}条留存记录（含重复或缓存采样）。"
            "各来源标题如下，事件关联与内容事实仍需核对。"
        ),
        "coverage": {
            "platform_count": len({e["platform"] for e in evidence}),
            "item_count": len(evidence),
            "observation_count": len(rows),
            "first_observed_at": rows[0][2].observed_at.isoformat(),
            "last_observed_at": rows[-1][2].observed_at.isoformat(),
        },
        "boundary": "仅概括热榜标题与本机观测；未读取报道正文，不代表事实核实。",
    }
    signature = hashlib.sha256(
        json.dumps(
            {"title": event.title, "revision": event.revision, "summary": data},
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {**data, "signature": signature}, evidence


def save_summaries(session, event_ids, day):
    rows_by_event = defaultdict(list)
    for row in evidence_rows(session, day, event_ids):
        rows_by_event[row[0].event_id].append(row)
    for event_id, rows in rows_by_event.items():
        summary, _ = summarize(session.get(Event, event_id), rows)
        if (
            session.scalar(
                select(EventSummary.id).where(
                    EventSummary.event_id == event_id,
                    EventSummary.signature == summary["signature"],
                )
            )
            is None
        ):
            session.add(
                EventSummary(
                    event_id=event_id,
                    signature=summary["signature"],
                    provider=summary["provider"],
                    version=summary["version"],
                    evidence_ids=summary["input_ids"],
                    data=summary,
                )
            )
    session.flush()


def process_pending(session):
    lock_events(session)
    pending = list(
        session.scalars(
            select(BoardSnapshot)
            .where(
                BoardSnapshot.board_id.in_(BOARDS),
                ~BoardSnapshot.id.in_(select(EventProcessedSnapshot.snapshot_id)),
            )
            .order_by(BoardSnapshot.observed_at, BoardSnapshot.id)
        )
    )
    if not pending:
        return {"snapshots": 0, "new_members": 0}
    events = {e.id: e for e in session.scalars(select(Event))}
    members = {m.item_id: m for m in session.scalars(select(EventMember))}
    items = {i.id: i for i in session.scalars(select(Item).where(Item.board_id.in_(BOARDS)))}
    board_platform = dict(session.execute(select(Board.id, Board.source_id)).all())
    history = defaultdict(list)
    for row, observed in session.execute(
        select(SnapshotItem, BoardSnapshot.observed_at)
        .join(BoardSnapshot, BoardSnapshot.id == SnapshotItem.snapshot_id)
        .where(BoardSnapshot.board_id.in_(BOARDS), ~SnapshotItem.flags.contains(["广告"]))
        .order_by(BoardSnapshot.observed_at)
    ):
        history[row.item_id].append((observed, row.title, row.id))
    count = 0
    for snapshot in pending:
        affected = set()
        rows = session.scalars(
            select(SnapshotItem)
            .where(SnapshotItem.snapshot_id == snapshot.id)
            .order_by(SnapshotItem.display_order)
        )
        for row in rows:
            if "广告" in row.flags:
                continue
            if row.item_id in members:
                affected.add(members[row.item_id].event_id)
                continue
            item = items[row.item_id]
            matches = []
            for other_id, member in members.items():
                other = items[other_id]
                event = events[member.event_id]
                if (
                    event.manual_locked
                    or board_platform[other.board_id] == board_platform[item.board_id]
                ):
                    continue
                reference = next(
                    (r for r in reversed(history[other_id]) if r[0] <= snapshot.observed_at), None
                )
                if reference is None or snapshot.observed_at - reference[0] > timedelta(hours=48):
                    continue
                score, reason = similarity(row.title, reference[1])
                if score >= 0.92:
                    matches.append((score, event.id, reason, reference[2]))
            unique = {m[1] for m in matches}
            if len(unique) == 1:
                score, event_id, reason, reference_id = max(matches)
                event = events[event_id]
                event.revision += 1
                basis = {
                    "method": "automatic",
                    "version": ALGORITHM,
                    "score": score,
                    "reason": reason,
                    "review": "待核对",
                    "input_evidence_id": row.id,
                    "matched_evidence_id": reference_id,
                }
            else:
                event = Event(
                    id=new_id(),
                    title=row.title,
                    revision=1,
                    manual_locked=False,
                    followed=False,
                    tags=[],
                )
                events[event.id] = event
                session.add(event)
                session.flush()
                basis = {
                    "method": "source",
                    "version": ALGORITHM,
                    "score": None,
                    "reason": "独立来源条目",
                    "review": "未人工核对",
                    "input_evidence_id": row.id,
                }
            member = EventMember(item_id=item.id, event_id=event.id, basis=basis)
            session.add(member)
            members[item.id] = member
            affected.add(event.id)
            count += 1
        session.add(EventProcessedSnapshot(snapshot_id=snapshot.id, version=ALGORITHM))
        session.flush()
        save_summaries(
            session, affected, snapshot.observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        )
    return {"snapshots": len(pending), "new_members": count}


def process_events():
    with session_factory().begin() as session:
        return process_pending(session)


def event_catalog(session, day):
    grouped = defaultdict(list)
    latest_board = {}
    for row in evidence_rows(session, day):
        grouped[row[0].event_id].append(row)
        snapshot = row[2]
        previous = latest_board.get(snapshot.board_id)
        if previous is None or snapshot.observed_at > previous.observed_at:
            latest_board[snapshot.board_id] = snapshot
    # Empty snapshots also mean prior topics are no longer present.
    start, end = day_bounds(day)
    for s in session.scalars(
        select(BoardSnapshot)
        .where(
            BoardSnapshot.board_id.in_(BOARDS),
            BoardSnapshot.observed_at >= start,
            BoardSnapshot.observed_at < end,
        )
        .order_by(BoardSnapshot.observed_at)
    ):
        latest_board[s.board_id] = s
    events = []
    member_counts = dict(
        session.execute(
            select(EventMember.event_id, func.count()).group_by(EventMember.event_id)
        ).all()
    )
    for event in session.scalars(select(Event)):
        rows = grouped.get(event.id)
        if not rows:
            continue
        summary, evidence = summarize(event, rows)
        for e in evidence:
            e["on_latest_board"] = latest_board[e["board_id"]].id == e["snapshot_id"]
        summary["record_id"] = None
        events.append(
            {
                "id": event.id,
                "title": event.title,
                "revision": event.revision,
                "manual_locked": event.manual_locked,
                "followed": event.followed,
                "tags": event.tags,
                "platforms": sorted({e["platform"] for e in evidence}),
                "first_observed_at": rows[0][2].observed_at.isoformat(),
                "last_observed_at": rows[-1][2].observed_at.isoformat(),
                "observation_count": len(rows),
                "evidence": evidence,
                "summary": summary,
                "total_members": member_counts.get(event.id, 0),
            }
        )
    signatures = [e["summary"]["signature"] for e in events]
    cached = {
        (event_id, signature): record_id
        for event_id, signature, record_id in session.execute(
            select(EventSummary.event_id, EventSummary.signature, EventSummary.id).where(
                EventSummary.signature.in_(signatures), EventSummary.event_id.in_(grouped)
            )
        )
    }
    for event in events:
        event["summary"]["record_id"] = cached.get((event["id"], event["summary"]["signature"]))
    events.sort(key=lambda e: (len(e["platforms"]), e["last_observed_at"], e["id"]), reverse=True)
    return events


def capture(session, ids):
    state = {}
    for event_id in sorted(ids):
        event = session.get(Event, event_id)
        state[event_id] = {
            "title": event.title,
            "revision": event.revision,
            "manual_locked": event.manual_locked,
            "followed": event.followed,
            "tags": event.tags,
            "members": {
                m.item_id: m.basis
                for m in session.scalars(
                    select(EventMember)
                    .where(EventMember.event_id == event_id)
                    .order_by(EventMember.item_id)
                )
            },
        }
    return state


def require_versions(session, versions, ids):
    if set(versions) != set(ids):
        raise EventConflict("请重新读取全部待操作事件。")
    for event_id in ids:
        event = session.get(Event, event_id)
        if event is None or event.revision != versions[event_id]:
            raise EventConflict("事件已被更新，请刷新后重试。")


def audit(session, kind, reason, before, ids):
    session.flush()
    change = EventChange(
        id=new_id(), kind=kind, reason=reason, before=before, after=capture(session, ids)
    )
    session.add(change)
    session.flush()
    # Preserve reproducible summaries of every date whose association changed.
    days = {
        row[2].observed_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
        for row in evidence_rows(session, event_ids=ids)
    }
    for day in days:
        save_summaries(session, ids, day)
    return change


def merge(session, target_id, other_ids, versions, reason):
    lock_events(session)
    ids = {target_id, *other_ids}
    if len(ids) != len(other_ids) + 1 or len(ids) < 2:
        raise ValueError("至少选择两个不同事件。")
    require_versions(session, versions, ids)
    before = capture(session, ids)
    if any(not state["members"] for state in before.values()):
        raise EventConflict("空事件无法合并，请重新读取。")
    for member in session.scalars(select(EventMember).where(EventMember.event_id.in_(ids))):
        member.event_id = target_id
        member.basis = {
            "method": "manual",
            "reason": reason,
            "score": None,
            "version": ALGORITHM,
            "review": "人工关联",
        }
    target = session.get(Event, target_id)
    target.followed = any(s["followed"] for s in before.values())
    target.tags = list(dict.fromkeys(t for s in before.values() for t in s["tags"]))
    if len(target.tags) > 12:
        raise ValueError("合并后的标签超过12个，请先整理标签。")
    for event_id in ids:
        event = session.get(Event, event_id)
        event.manual_locked = True
        event.revision += 1
    return audit(session, "merge", reason, before, ids)


def split(session, event_id, item_ids, versions, reason):
    lock_events(session)
    require_versions(session, versions, {event_id})
    before = capture(session, {event_id})
    members = before[event_id]["members"]
    selected = set(item_ids)
    if not selected or not selected < set(members):
        raise ValueError("请选择部分来源条目拆出，原事件需保留至少一条。")
    chosen = session.scalar(select(Item).where(Item.id.in_(selected)).order_by(Item.id))
    new = Event(
        id=new_id(), title=chosen.title, revision=1, manual_locked=True, followed=False, tags=[]
    )
    session.add(new)
    session.flush()
    before.update(capture(session, {new.id}))
    for member in session.scalars(select(EventMember).where(EventMember.item_id.in_(selected))):
        member.event_id = new.id
        member.basis = {
            "method": "manual",
            "reason": reason,
            "score": None,
            "version": ALGORITHM,
            "review": "人工拆分",
        }
    old = session.get(Event, event_id)
    old.manual_locked = True
    old.revision += 1
    new.revision += 1
    return audit(session, "split", reason, before, {event_id, new.id})


def restore(session, change_id, reason):
    lock_events(session)
    change = session.get(EventChange, change_id)
    if change is None or change.kind not in {"merge", "split"} or change.reverted_by:
        raise EventConflict("该操作已恢复或无法恢复。")
    ids = set(change.after)
    current = capture(session, ids)
    if current != change.after:
        raise EventConflict("涉及事件已有后续修改，无法直接恢复；请根据当前来源手动修正。")
    # Membership sets must match before moving anything; no raw snapshots are modified.
    for event_id, state in change.before.items():
        event = session.get(Event, event_id)
        for key in ("title", "manual_locked", "followed", "tags"):
            setattr(event, key, state[key])
        event.revision += 1
        for item_id, basis in state["members"].items():
            member = session.get(EventMember, item_id)
            member.event_id = event_id
            member.basis = basis
    restored = audit(session, "restore", reason, current, ids)
    change.reverted_by = restored.id
    return restored


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh-summaries", action="store_true")
    args = parser.parse_args()
    result = process_events()
    if args.refresh_summaries:
        with session_factory().begin() as session:
            lock_events(session)
            ids = set(session.scalars(select(EventMember.event_id)))
            days = {
                d.astimezone(ZoneInfo("Asia/Shanghai")).date()
                for d in session.scalars(
                    select(BoardSnapshot.observed_at).where(BoardSnapshot.board_id.in_(BOARDS))
                )
            }
            for day in days:
                save_summaries(session, ids, day)
            result["summary_days_refreshed"] = len(days)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
