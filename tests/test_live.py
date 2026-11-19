"""Полный цикл на настоящем Keitaro: создание, правка, публикация, сверка, удаление.

Запуск: pytest -m live (нужны KEITARO_API_KEY и CAMPAIGN_PREFIX в .env).
Тест создаёт кампанию со своим префиксом и в конце удаляет её.
Офферы задаются переменными LIVE_OFFER (виден ключу) и LIVE_HIDDEN_OFFER (закрыт, 403).
"""

import os
import time

import pytest

from app.config import Settings
from app.keitaro import KeitaroClient
from app.services import editor
from app.services.creator import create_campaign
from app.services.errors import ConflictError
from app.services.sync import fetch_campaign

pytestmark = pytest.mark.live

OFFER = int(os.environ.get("LIVE_OFFER", "18813"))
HIDDEN = int(os.environ.get("LIVE_HIDDEN_OFFER", "13333"))


@pytest.fixture
def live_settings() -> Settings:
    settings = Settings()
    if not settings.keitaro_api_key or settings.keitaro_fake:
        pytest.skip("нужен KEITARO_API_KEY и выключенный KEITARO_FAKE")
    assert settings.campaign_prefix.startswith("test_"), "живой тест пишет только в test_*"
    return settings


@pytest.fixture
def live_kt(live_settings):
    client = KeitaroClient(
        live_settings.keitaro_url,
        live_settings.keitaro_api_key,
        timeout=live_settings.keitaro_timeout,
    )
    yield client
    client.close()


def kt_offers(kt, stream_kt_id):
    return {o.offer_id: o.share for o in kt.get_stream(stream_kt_id).offers if o.state == "active"}


def test_full_cycle(db, live_kt, live_settings):
    kt = live_kt
    campaign = create_campaign(
        db, kt, live_settings, name=f"live_{int(time.time())}", countries="AU, MX", offer_id=OFFER
    )
    try:
        # кампания и потоки такие, как требует ТЗ
        kt_campaign = kt.get_campaign(campaign.kt_id)
        assert kt_campaign.name.startswith(live_settings.campaign_prefix)
        assert kt_campaign.type == "position"
        geo, offers = kt.get_campaign_streams(campaign.kt_id)
        assert geo.schema_ == "redirect" and geo.action_payload == live_settings.redirect_url
        assert [(f.name, f.mode, sorted(f.payload)) for f in geo.filters] == [
            ("country", "accept", ["AU", "MX"])
        ]
        assert offers.filters == [] and kt_offers(kt, offers.id) == {OFFER: 100}

        stream = next(s for s in campaign.streams if s.has_offers)
        hidden_row = editor.add_offer(db, kt, stream, HIDDEN)

        # Add + Push
        editor.push(db, kt, stream)
        assert kt_offers(kt, offers.id) == {OFFER: 50, HIDDEN: 50}

        # Pin 70% + Push
        visible_row = next(r for r in stream.offers if r.offer_id == OFFER)
        editor.set_pin(db, stream, visible_row.id, pinned=True, share=70)
        editor.push(db, kt, stream)
        assert kt_offers(kt, offers.id) == {OFFER: 70, HIDDEN: 30}

        # Remove + Push: из Keitaro оффер пропадает, у нас остаётся в архиве
        editor.set_pin(db, stream, visible_row.id, pinned=False)
        editor.remove_offer(db, stream, hidden_row.id)
        editor.push(db, kt, stream)
        assert kt_offers(kt, offers.id) == {OFFER: 100}
        assert hidden_row.is_removed and not stream.is_dirty

        # Правка в админке мимо нас: Push не затирает её, Fetch подтягивает
        kt.update_stream(
            offers.id,
            {
                "offers": [
                    {"offer_id": OFFER, "share": 40, "state": "active"},
                    {"offer_id": HIDDEN, "share": 60, "state": "active"},
                ]
            },
        )
        editor.bring_back(db, stream, hidden_row.id)
        with pytest.raises(ConflictError):
            editor.push(db, kt, stream)
        db.rollback()
        _, report = fetch_campaign(db, kt, campaign.kt_id)
        assert {r.offer_id: r.draft_share for r in stream.offers} == {OFFER: 40, HIDDEN: 60}
        assert report.changed == 1 and not stream.is_dirty
    finally:
        kt.delete_campaign(campaign.kt_id)
    assert all(c.id != campaign.kt_id for c in kt.list_campaigns() if c.state != "deleted")
