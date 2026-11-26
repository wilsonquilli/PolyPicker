from functools import lru_cache

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from polypicker.config import get_settings


@lru_cache
def owner_engine() -> Engine:
    """Read/write engine for jobs (ETL, training, prediction). Never used by the API."""
    return create_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def api_engine() -> Engine:
    """SELECT-only engine for the public API (FR-11.2)."""
    return create_engine(get_settings().api_database_url, pool_pre_ping=True,
                         pool_size=get_settings().api_pool_size, max_overflow=get_settings().api_max_overflow)


def owner_session() -> Session:
    return sessionmaker(owner_engine(), expire_on_commit=False)()
