"""Зависимости FastAPI: клиент Keitaro."""

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from app.config import Settings, get_settings
from app.keitaro import KeitaroAPI, KeitaroClient
from app.keitaro.fake import FakeKeitaro


@lru_cache
def demo_keitaro() -> FakeKeitaro:
    """Один Keitaro в памяти на процесс, чтобы демо-данные жили между запросами."""
    kt = FakeKeitaro().seed_defaults()
    kt.add_campaign("test_demo_campaign", [(3749, 33), (3717, 33), (11111, 34)])
    return kt


def get_kt(settings: Annotated[Settings, Depends(get_settings)]) -> Iterator[KeitaroAPI]:
    if settings.keitaro_fake:
        yield demo_keitaro()
        return
    client = KeitaroClient(
        settings.keitaro_url, settings.keitaro_api_key, timeout=settings.keitaro_timeout
    )
    try:
        yield client
    finally:
        client.close()
