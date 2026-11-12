from uuid import uuid4

import pytest
from hotradar import collection
from hotradar.config import get_settings
from hotradar.db.models import Base, Board, Source
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def database(monkeypatch):
    schema = "test_m1_" + uuid4().hex
    control = create_engine(get_settings().database_url)
    with control.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(
        get_settings().database_url, execution_options={"schema_translate_map": {None: schema}}
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(collection, "get_engine", lambda: engine)
    monkeypatch.setattr(collection, "session_factory", lambda: factory)
    board_id = "test-weibo-" + uuid4().hex[:8]
    with factory.begin() as session:
        session.add(
            Source(
                id="weibo",
                name="测试微博",
                platform="weibo",
                provider="test",
                home_url="https://weibo.com/",
                access_method="mock",
            )
        )
        session.flush()
        session.add(
            Board(
                id=board_id,
                source_id="weibo",
                name="test",
                item_kind="topic",
                metric_name="search_heat",
                metric_label="热搜指数",
                enabled=True,
                interval_seconds=900,
                parser_version="weibo-hot-v1",
            )
        )
    try:
        yield factory, board_id
    finally:
        engine.dispose()
        # Drop only the unique schema created by this fixture, never public/production tables.
        assert schema.startswith("test_m1_") and len(schema) == 40
        with control.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


@pytest.fixture
def event_db(database, monkeypatch):
    from hotradar.api import events as event_api

    factory, _ = database
    with factory.begin() as s:
        s.add(
            Source(
                id="douyin",
                name="测试抖音",
                platform="douyin",
                provider="test",
                home_url="https://www.douyin.com/",
                access_method="mock",
            )
        )
        s.flush()
        for source, metric in [("weibo", "search_heat"), ("douyin", "topic_heat")]:
            s.add(
                Board(
                    id=source + "-hot",
                    source_id=source,
                    name="test",
                    item_kind="topic",
                    metric_name=metric,
                    metric_label="测试指数",
                    enabled=True,
                    interval_seconds=900,
                    parser_version="test",
                )
            )
    monkeypatch.setattr(event_api, "session_factory", lambda: factory)
    return factory
