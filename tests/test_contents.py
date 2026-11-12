import copy
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from hotradar.api import contents as content_api
from hotradar.api.main import app
from hotradar.contents import BrowserSample, catalog, import_sample
from hotradar.db.models import BoardSnapshot, ContentObservation, EventChange
from pydantic import ValidationError
from sqlalchemy import func, select


def sample():
    # Explicit test fixture, stored only in the per-test schema.
    return dict(
        source_id="weibo",
        external_id="TestPost",
        item_kind="post",
        original_url="https://weibo.com/12345/TestPost",
        title="test fixture",
        author="test author",
        author_url="https://weibo.com/u/12345",
        published_at_display="2026-10-13 12:00",
        published_timezone="Asia/Shanghai",
        published_precision="minute",
        observed_at=datetime(2026, 10, 14, 2, 0, tzinfo=UTC),
        metrics={"likes": 0, "comments": None, "reposts": 3},
        discovery_label="test discovery",
        discovery_url="https://weibo.com/",
        evidence_file="docs/content-sources/screens/test-fixture.jpg",
        evidence_sha256="a" * 64,
        verification_note="test fixture only",
    )


@pytest.mark.parametrize(
    "change",
    [
        {"original_url": "https://s.weibo.com/weibo?q=test"},
        {"author": ""},
        {"published_at_display": "10-13 12:00"},
        {"metrics": {"likes": "7.3万", "comments": 2, "reposts": 3}},
        {"metrics": {"likes": True, "comments": 2, "reposts": 3}},
        {"metrics": {"likes": None, "comments": None, "reposts": None}},
        {"metrics": {"likes": -1, "comments": 2, "reposts": 3}},
        {"source_id": "bilibili"},
        {"evidence_file": "../../.env"},
        {"author_url": "https://weibo.com/u/12345?cookie=secret"},
        {"cookie": "untrusted-secret"},
        {"observed_at": datetime(2026, 10, 14, 2, 0)},
        {"observed_at": datetime.now(UTC) + timedelta(days=1)},
    ],
)
def test_unverified_or_unsafe_fields_rejected(change):
    with pytest.raises(ValidationError):
        BrowserSample.model_validate({**sample(), **change})


def test_douyin_topic_not_content():
    record = sample()
    record.update(
        source_id="douyin",
        external_id="7696454624705776937",
        item_kind="video",
        original_url="https://www.douyin.com/hot/2694377",
        author_url="https://www.douyin.com/user/test",
        discovery_url="https://www.douyin.com/shipin",
        metrics={"likes": 1, "comments": None, "favorites": None, "shares": None},
    )
    with pytest.raises(ValidationError):
        BrowserSample.model_validate(record)


def test_immutable_idempotent_history_and_readonly_api(event_db, monkeypatch):
    record = BrowserSample.model_validate(sample())
    with event_db.begin() as session:
        first, created = import_sample(session, record)
        first_id = first.id
        assert created
        same, created = import_sample(session, record)
        assert not created and same.id == first_id
        changed = copy.deepcopy(sample())
        changed.update(observed_at=datetime(2026, 10, 14, 3, 0, tzinfo=UTC))
        changed["metrics"]["likes"] = 5
        second, created = import_sample(session, BrowserSample.model_validate(changed))
        assert created
        second_id = second.id
    monkeypatch.setattr(content_api, "session_factory", lambda: event_db)
    client = TestClient(app)
    response = client.get("/api/v1/contents?date=2026-10-14")
    assert response.status_code == 200
    data = response.json()
    assert data["automatic_collection"] is False
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["id"] == second_id and item["metrics"]["likes"]["value"] == 5
    assert item["metrics"]["comments"]["value"] is None and item["play_count"] is None
    assert client.get("/api/v1/contents?date=2026-10-13").json()["items"] == []
    assert client.get("/api/v1/contents?date=bad").status_code == 422
    with event_db() as session:
        assert session.get(ContentObservation, first_id).data["metrics"]["likes"] == 0
        assert session.scalar(select(func.count()).select_from(ContentObservation)) == 2
        assert session.scalar(select(func.count()).select_from(BoardSnapshot)) == 0
        assert session.scalar(select(func.count()).select_from(EventChange)) == 0
        assert catalog(session, date(2026, 10, 13))["items"] == []


def test_content_read_failure_is_safe_and_no_write_route(monkeypatch):
    def unavailable():
        raise RuntimeError("secret connection details")

    monkeypatch.setattr(content_api, "session_factory", unavailable)
    client = TestClient(app)
    response = client.get("/api/v1/contents?date=2026-10-14")
    assert response.status_code == 503
    assert "secret" not in response.text
    assert client.post("/api/v1/contents", json={"cookie": "secret"}).status_code == 405
