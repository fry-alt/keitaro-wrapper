import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.config import Settings, get_settings
from app.db import Base, get_db
from app.deps import get_kt
from app.keitaro.fake import FakeKeitaro
from app.main import app


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


@pytest.fixture
def client(session_factory, kt, settings):
    """HTTP-клиент приложения с базой в памяти, Keitaro в памяти и тестовыми настройками."""

    def override_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_kt] = lambda: kt
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()
