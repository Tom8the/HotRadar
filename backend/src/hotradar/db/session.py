from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from hotradar.config import get_settings


@lru_cache
def get_engine():
    return create_engine(
        get_settings().database_url,
        pool_pre_ping=True,
        pool_size=6,
        max_overflow=4,
        connect_args={"connect_timeout": 5},
    )


def session_factory():
    return sessionmaker(get_engine(), class_=Session, expire_on_commit=False)
