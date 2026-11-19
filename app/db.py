"""Подключение к базе и сессии SQLAlchemy."""

import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        # папку под файл базы создаём сами, чтобы первый запуск не падал
        db_path = url.removeprefix("sqlite:///")
        if db_path and db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        # в SQLite внешние ключи выключены по умолчанию
        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_conn, _record):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")

        return engine
    if os.environ.get("VERCEL"):
        # serverless: экземпляр живёт недолго, держать пул открытых соединений незачем
        return create_engine(url, poolclass=NullPool)
    return create_engine(url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    # движок создаём при первом обращении, а не при импорте: тестам и миграциям
    # не нужна боевая база
    return sessionmaker(bind=make_engine(get_settings().database_url), expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with get_sessionmaker()() as session:
        yield session
