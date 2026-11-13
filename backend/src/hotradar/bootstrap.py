from sqlalchemy import select

from hotradar.collectors.douyin_city import NATIONAL_BOARD, NATIONAL_VERSION
from hotradar.config import get_settings
from hotradar.db.models import Board, Source
from hotradar.db.session import session_factory

SEEDS = [
    (
        "baidu",
        "百度",
        "https://top.baidu.com/board?tab=realtime&platform=pc",
        "baidu-hot",
        "热搜榜",
        "search_heat",
        "热搜指数",
        "baidu-hot-v1",
    ),
    (
        "douyin",
        "抖音",
        "https://www.douyin.com/",
        "douyin-hot",
        "热点榜 · 话题",
        "topic_heat",
        "话题热度",
        "douyin-hot-v1",
    ),
    (
        "weibo",
        "微博",
        "https://weibo.com/",
        "weibo-hot",
        "热搜榜",
        "search_heat",
        "热搜指数",
        "weibo-hot-v1",
    ),
    (
        "douyin",
        "抖音",
        "https://www.douyin.com/",
        NATIONAL_BOARD,
        "创作参考 · 全国创作热点",
        "topic_heat",
        "创作热点热度",
        NATIONAL_VERSION,
    ),
    (
        "douyin",
        "抖音",
        "https://www.douyin.com/",
        "douyin-wuxi",
        "无锡创作热点 · 创作者中心",
        "city_heat",
        "同城话题热度",
        "douyin-wuxi-v1",
    ),
    (
        "weibo",
        "微博",
        "https://weibo.com/",
        "weibo-wuxi",
        "无锡榜 · 同城热搜",
        "city_heat",
        "同城热度",
        "weibo-wuxi-v1",
    ),
    (
        "douyin",
        "抖音",
        "https://www.douyin.com/",
        "douyin-wuxi-app",
        "无锡榜 · 抖音原生城市榜",
        "city_heat",
        "同城热度",
        "douyin-wuxi-app-v1",
    ),
]


def seed_sources():
    settings = get_settings()
    with session_factory().begin() as session:
        for source_id, name, url, _board_id, _board_name, _metric, _label, _parser in SEEDS:
            if session.get(Source, source_id) is None:
                session.add(
                    Source(
                        id=source_id,
                        name=name,
                        platform=source_id,
                        provider="platform-web",
                        home_url=url,
                        access_method="anonymous-web-page"
                        if source_id == "baidu"
                        else "anonymous-web-endpoint",
                    )
                )
        session.flush()
        for source_id, _name, _url, board_id, board_name, metric, label, parser in SEEDS:
            if session.get(Board, board_id) is None:
                session.add(
                    Board(
                        id=board_id,
                        source_id=source_id,
                        name=board_name,
                        metric_name=metric,
                        metric_label=label,
                        item_kind="topic",
                        interval_seconds=settings.interval,
                        enabled=board_id in {"douyin-hot", "weibo-hot", "baidu-hot"},
                        parser_version=parser,
                    )
                )
        # Preserve existing settings for the explicitly requested topic sources.
        for board in session.scalars(select(Board)):
            if board.source_id not in {"weibo", "douyin", "baidu"}:
                board.enabled = False
        from hotradar.db.models import ContentSourceSetting

        for source_id in ("weibo", "douyin"):
            if session.get(ContentSourceSetting, source_id) is None:
                session.add(ContentSourceSetting(source_id=source_id))


def main():
    seed_sources()
    print("Registered Douyin, Weibo and Baidu boards.")


if __name__ == "__main__":
    main()
