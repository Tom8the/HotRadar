from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from hotradar import events
from hotradar.api.main import app
from hotradar.collection import persist_batch
from hotradar.collectors.platforms import parse_douyin, parse_weibo
from hotradar.db.models import (
    CollectionRun,
    Event,
    EventChange,
    EventMember,
    EventSummary,
    SnapshotItem,
    new_id,
)
from sqlalchemy import func, select

NOW = datetime(2026, 10, 14, 7, 0, tzinfo=UTC)


def batch(s, platform, title, observed=NOW, key=None, metric=None, ad=False):
    if platform == "weibo":
        payload = {
            "ok": 1,
            "data": {
                "realtime": [{"word": title, "rank": 0, "num": metric, "is_ad": 1 if ad else 0}]
            },
        }
        parsed = parse_weibo(payload, observed)
    else:
        payload = {
            "status_code": 0,
            "data": {
                "word_list": [
                    {
                        "word": title,
                        "sentence_id": key or "123456789",
                        "position": 1,
                        "hot_value": metric,
                    }
                ]
            },
        }
        parsed = parse_douyin(payload, observed)
    run = CollectionRun(
        id=new_id(), board_id=platform + "-hot", job_key=new_id(), parser_version="test"
    )
    s.add(run)
    s.flush()
    return persist_batch(s, run, parsed)


def populate(factory):
    with factory.begin() as s:
        batch(s, "douyin", "中朝新鸭绿江大桥将开通", metric=0)
        batch(s, "weibo", "中朝新鸭绿江大桥", observed=NOW + timedelta(minutes=1))
        batch(s, "weibo", "肖战官宣个人巡回演唱会", observed=NOW + timedelta(minutes=2))
        result = events.process_pending(s)
        assert result == {"snapshots": 3, "new_members": 3}


def test_conservative_similarity_distinct_entities_actions_numbers():
    assert events.similarity("#中朝新鸭绿江大桥#", "中朝新鸭绿江大桥将开通")[0] >= 0.92
    for a, b in [
        ("霸王茶姬", "霸王茶姬格局"),
        ("金晨自曝学校倒闭", "金晨问李一桐把钱借给谁了"),
        ("2027国考报名正式开始公告", "2028国考报名正式开始公告"),
        ("明星辟谣某项长标题新闻公告", "明星确认某项长标题新闻公告"),
    ]:
        assert events.similarity(a, b)[0] < 0.92


def test_ingestion_idempotence_summary_provenance_and_null_zero(event_db):
    populate(event_db)
    with event_db.begin() as s:
        assert events.process_pending(s) == {"snapshots": 0, "new_members": 0}
        catalog = events.event_catalog(s, NOW.date())
        cross = next(e for e in catalog if len(e["platforms"]) == 2)
        assert len(catalog) == 2 and cross["total_members"] == 2
        assert cross["summary"]["record_id"] is not None
        assert cross["summary"]["model"] is None
        assert cross["summary"]["coverage"]["platform_count"] == 2
        assert cross["summary"]["coverage"]["observation_count"] == 2
        count = s.scalar(select(func.count(EventSummary.id)))
        events.save_summaries(s, {e["id"] for e in catalog}, NOW.date())
        assert count == s.scalar(select(func.count(EventSummary.id)))
        assert set(cross["summary"]["input_ids"]) == {e["id"] for e in cross["evidence"]}
        assert {e["metrics"][e["metric_name"]]["value"] for e in cross["evidence"]} == {0, None}
        assert all(
            s.get(SnapshotItem, line["evidence_id"]).title in line["text"]
            for line in cross["summary"]["lines"]
        )
        assert events.event_catalog(s, NOW.date() - timedelta(days=1)) == []
        raw_titles = [r.title for r in s.scalars(select(SnapshotItem))]
        assert "中朝新鸭绿江大桥将开通" in raw_titles


def test_ads_same_platform_and_48_hour_window_do_not_auto_merge(event_db):
    with event_db.begin() as s:
        batch(s, "weibo", "独立广告测试主题", ad=True)
        batch(s, "weibo", "中朝新鸭绿江大桥将开通")
        batch(s, "weibo", "中朝新鸭绿江大桥")
        batch(s, "douyin", "中朝新鸭绿江大桥将开通", NOW + timedelta(hours=49))
        events.process_pending(s)
        assert s.scalar(select(func.count(EventMember.item_id))) == 3
        assert s.scalar(select(func.count(Event.id))) == 3


def test_split_protected_on_new_observation_and_restore(event_db):
    populate(event_db)
    with event_db.begin() as s:
        cross = next(e for e in events.event_catalog(s, NOW.date()) if len(e["platforms"]) == 2)
        item = cross["evidence"][0]["item_id"]
        change = events.split(
            s, cross["id"], [item], {cross["id"]: cross["revision"]}, "不同事件拆分"
        )
        change_id = change.id
        assert len(events.event_catalog(s, NOW.date())) == 3
    with event_db.begin() as s:
        batch(s, "douyin", "中朝新鸭绿江大桥将开通", NOW + timedelta(minutes=15))
        events.process_pending(s)
        assert len(events.event_catalog(s, NOW.date())) == 3
        restored = events.restore(s, change_id, "核对后恢复")
        assert restored.kind == "restore"
        assert s.get(EventChange, change_id).reverted_by == restored.id
        assert len(events.event_catalog(s, NOW.date())) == 2
    with event_db.begin() as s:
        with pytest.raises(events.EventConflict):
            events.restore(s, change_id, "再次恢复")


def test_merge_undo_concurrency_and_no_snapshot_changes(event_db):
    populate(event_db)
    with event_db.begin() as s:
        before = {
            r.id: (r.title, r.original_url, r.metrics) for r in s.scalars(select(SnapshotItem))
        }
        rows = events.event_catalog(s, NOW.date())
        a, b = rows
        revisions = {r["id"]: r["revision"] for r in rows}
        change = events.merge(s, a["id"], [b["id"]], revisions, "测试人工关联")
        assert len(events.event_catalog(s, NOW.date())) == 1
        with pytest.raises(events.EventConflict):
            events.merge(s, a["id"], [b["id"]], revisions, "旧版本再次提交")
        events.restore(s, change.id, "恢复人工测试前关系")
        assert len(events.event_catalog(s, NOW.date())) == 2
        after = {
            r.id: (r.title, r.original_url, r.metrics) for r in s.scalars(select(SnapshotItem))
        }
        assert before == after


def test_later_edit_blocks_unsafe_restore(event_db):
    populate(event_db)
    with event_db.begin() as s:
        rows = events.event_catalog(s, NOW.date())
        a, b = rows
        change = events.merge(
            s, a["id"], [b["id"]], {r["id"]: r["revision"] for r in rows}, "合并测试"
        )
        e = s.get(Event, a["id"])
        e.tags = ["后续标记"]
        e.revision += 1
        with pytest.raises(events.EventConflict):
            events.restore(s, change.id, "不能覆盖后续修改")


def test_manual_relationship_rejects_new_automatic_alias(event_db):
    populate(event_db)
    with event_db.begin() as s:
        cross = next(e for e in events.event_catalog(s, NOW.date()) if len(e["platforms"]) == 2)
        events.split(
            s,
            cross["id"],
            [cross["evidence"][0]["item_id"]],
            {cross["id"]: cross["revision"]},
            "拆开后保护",
        )
        batch(s, "douyin", "中朝新鸭绿江大桥", NOW + timedelta(minutes=20), key="9999999")
        events.process_pending(s)
        rows = events.event_catalog(s, NOW.date())
        assert len(rows) == 4 and all(len(e["platforms"]) == 1 for e in rows)
        assert all(e["total_members"] == 1 for e in rows)


def test_backfill_uses_observed_title_not_future_item_metadata(event_db):
    with event_db.begin() as s:
        first = batch(s, "douyin", "中朝新鸭绿江大桥将开通")
        batch(s, "weibo", "中朝新鸭绿江大桥", NOW + timedelta(minutes=1))
        batch(s, "douyin", "标题后来变化为无关主题", NOW + timedelta(minutes=15))
        events.process_pending(s)
        rows = events.event_catalog(s, NOW.date())
        assert len(rows) == 1 and len(rows[0]["platforms"]) == 2
        member = next(e for e in rows[0]["evidence"] if e["platform"] == "weibo")
        reference = s.get(SnapshotItem, member["basis"]["matched_evidence_id"])
        assert reference.snapshot_id == first.id


def test_empty_latest_board_keeps_last_observation_not_live_rank(event_db):
    populate(event_db)
    with event_db.begin() as s:
        run = CollectionRun(
            id=new_id(), board_id="weibo-hot", job_key=new_id(), parser_version="test"
        )
        s.add(run)
        s.flush()
        persist_batch(
            s, run, parse_weibo({"ok": 1, "data": {"realtime": []}}, NOW + timedelta(minutes=30))
        )
        events.process_pending(s)
        cross = next(e for e in events.event_catalog(s, NOW.date()) if len(e["platforms"]) == 2)
        old = next(e for e in cross["evidence"] if e["platform"] == "weibo")
        assert old["rank"] == 1 and not old["on_latest_board"]
        assert old["observed_at"] != (NOW + timedelta(minutes=30)).isoformat()
        batch(s, "weibo", "中朝新鸭绿江大桥", NOW + timedelta(minutes=45), ad=True)
        events.process_pending(s)
        cross = next(e for e in events.event_catalog(s, NOW.date()) if len(e["platforms"]) == 2)
        assert cross["observation_count"] == 2
        assert all("广告" not in e["flags"] for e in cross["evidence"])


def test_readonly_api_origin_and_correction_validation(event_db):
    populate(event_db)
    with TestClient(app) as client:
        with event_db() as s:
            before = s.scalar(select(func.count(EventSummary.id)))
        for _ in range(2):
            result = client.get("/api/v1/events?date=2026-10-14").json()
            assert len(result["events"]) == 2 and result["pending_snapshots"] == 0
        with event_db() as s:
            assert before == s.scalar(select(func.count(EventSummary.id)))
        e = result["events"][0]
        response = client.get(f"/api/v1/events/{e['id']}?date=2026-10-14")
        assert response.status_code == 200 and len(response.json()["all_evidence"]) == 2
        body = {
            "event_id": e["id"],
            "revision": e["revision"],
            "followed": True,
            "tags": ["观察", "观察"],
        }
        assert (
            client.post(
                "/api/v1/events/metadata", json=body, headers={"origin": "https://foreign.example"}
            ).status_code
            == 403
        )
        assert client.post("/api/v1/events/metadata", json=body).status_code == 200
        assert client.post("/api/v1/events/metadata", json=body).status_code == 409
        saved = client.get("/api/v1/events?date=2026-10-14").json()["events"][0]
        assert saved["followed"] and saved["tags"] == ["观察"]
        assert (
            client.post(
                "/api/v1/events/split",
                json={
                    "event_id": e["id"],
                    "item_ids": [new_id()],
                    "revisions": {e["id"]: saved["revision"]},
                    "reason": "   ",
                },
            ).status_code
            == 422
        )
        assert client.get("/api/v1/events?date=invalid").status_code == 422
        assert client.get(f"/api/v1/events/{e['id']}?date=2026-10-13").status_code == 404
