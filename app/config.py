"""Настройки приложения. Читаются из переменных окружения и файла .env."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Keitaro
    keitaro_url: str = Field("https://tlgk.host", description="Адрес трекера без /admin")
    keitaro_api_key: str = Field("", description="Ключ Admin API")
    keitaro_timeout: float = 20.0
    # Демо-режим: вместо настоящего Keitaro — Keitaro в памяти с тестовыми данными
    keitaro_fake: bool = False

    # База: SQLite по умолчанию, PostgreSQL через docker-compose
    database_url: str = "sqlite:///./data/app.db"

    # Создание кампаний
    campaign_prefix: str = Field("test_", description="Префикс имени, например test_ivanov_")
    default_domain: str = "kylovoa.com"
    default_group: str = "FORTESTS"
    default_traffic_source: str = "FORTESTS"
    redirect_url: str = "https://google.com"

    # Через сколько секунд кэш офферов считается устаревшим
    offers_cache_ttl: int = 600

    @property
    def keitaro_admin_url(self) -> str:
        return self.keitaro_url.rstrip("/") + "/admin/"


@lru_cache
def get_settings() -> Settings:
    return Settings()
