import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    database_url: URL
    interval: int = 900
    timeout: float = 20
    timezone: str = "Asia/Shanghai"
    douyin_wuxi_proxy: str | None = None


def get_settings() -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    password = os.environ.get("HOTRADAR_DB_PASSWORD")
    if not password:
        raise RuntimeError("Local database password is missing; run configure-local.ps1.")
    interval = int(os.environ.get("HOTRADAR_COLLECTION_INTERVAL", "900"))
    if interval < 60:
        raise ValueError("Collection interval must be at least 60 seconds.")
    return Settings(
        database_url=URL.create(
            "postgresql+psycopg",
            username=os.environ.get("HOTRADAR_DB_USER", "hotradar"),
            password=password,
            host=os.environ.get("HOTRADAR_DB_HOST", "127.0.0.1"),
            port=int(os.environ.get("HOTRADAR_DB_PORT", "18814")),
            database=os.environ.get("HOTRADAR_DB_NAME", "hotradar"),
        ),
        interval=interval,
        timeout=float(os.environ.get("HOTRADAR_REQUEST_TIMEOUT", "20")),
        timezone=os.environ.get("HOTRADAR_TIMEZONE", "Asia/Shanghai"),
        douyin_wuxi_proxy=os.environ.get("HOTRADAR_DOUYIN_WUXI_PROXY") or None,
    )
