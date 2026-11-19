"""Настройки приложения. Читаются из переменных окружения и файла .env."""

from functools import lru_cache

from pydantic import Field, field_validator
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

    # Безопасность. Пароль входа: пустой — вход не нужен (только для локального запуска)
    app_password: str = ""
    # Ключ подписи cookie. Пустой — выводится из пароля
    secret_key: str = ""
    # Править можно только кампании с CAMPAIGN_PREFIX. True — любые (осторожно: трекер общий)
    edit_any_campaign: bool = False

    # Через сколько секунд кэш офферов считается устаревшим
    offers_cache_ttl: int = 600

    @field_validator("database_url")
    @classmethod
    def _use_psycopg(cls, url: str) -> str:
        # Neon, Heroku и другие отдают postgres://… — SQLAlchemy нужен явный драйвер
        for scheme in ("postgres://", "postgresql://"):
            if url.startswith(scheme):
                return "postgresql+psycopg://" + url.removeprefix(scheme)
        return url

    def can_edit(self, campaign_name: str) -> bool:
        return self.edit_any_campaign or campaign_name.startswith(self.campaign_prefix)

    @property
    def keitaro_admin_url(self) -> str:
        return self.keitaro_url.rstrip("/") + "/admin/"


@lru_cache
def get_settings() -> Settings:
    return Settings()
