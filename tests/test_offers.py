from datetime import timedelta

from app.models import Offer, utcnow
from app.services.offers import refresh_if_stale, refresh_offers, search_offers


def test_search_by_name_and_id(db, kt):
    refresh_offers(db, kt)
    assert [o.id for o in search_offers(db, "miaflow")] == [3749, 3717]
    assert [o.id for o in search_offers(db, "3717")] == [3717]
    # точное совпадение ID первым, дальше частичные
    assert search_offers(db, "11111")[0].id == 11111
    assert len(search_offers(db, "")) == 5


def test_offer_gone_from_keitaro_is_archived(db, kt):
    refresh_offers(db, kt)
    del kt.offers[3717]
    refresh_offers(db, kt)
    assert db.get(Offer, 3717).state == "archived"
    assert 3717 not in [o.id for o in search_offers(db, "miaflow")]


def test_refresh_if_stale(db, kt):
    assert refresh_if_stale(db, kt, ttl_seconds=600) is True  # пустой каталог
    assert refresh_if_stale(db, kt, ttl_seconds=600) is False
    for offer in db.query(Offer):
        offer.synced_at = utcnow() - timedelta(hours=1)
    db.commit()
    assert refresh_if_stale(db, kt, ttl_seconds=600) is True
