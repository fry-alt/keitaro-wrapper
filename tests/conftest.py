import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.config import Settings
from app.db import Base
from app.keitaro.fake import FakeKeitaro


@pytest.fixture
def engine():
    # одна база в памяти на тест; StaticPool — чтобы все сессии видели одни данные
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(session_factory) -> Session:
    with session_factory() as session:
        yield session


@pytest.fixture
def kt() -> FakeKeitaro:
    return FakeKeitaro().seed_defaults()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        _env_file=None,
        keitaro_url="https://kt.example",
        keitaro_api_key="test-key",
        campaign_prefix="test_ivanov_",
        database_url="sqlite://",
    )
