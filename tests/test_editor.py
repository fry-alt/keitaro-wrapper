"""Сценарии редактора из видео: каждый тест — одно действие пользователя."""

import pytest

from app.services import editor
from app.services.errors import ConflictError, NotFoundError, UserError
from app.services.sync import fetch_campaign

A, B, C, D, E = 3749, 3717, 11111, 11112, 11113


@pytest.fixture
def setup(db, kt):
    kt_campaign = kt.add_campaign("test_ivanov_c1", [(A, 33), (B, 33), (C, 34)])
    campaign, _ = fetch_campaign(db, kt, kt_campaign.id)
    stream = next(s for s in campaign.streams if s.has_offers)
    return kt_campaign, campaign, stream


def shares(stream):
    return {r.offer_id: r.draft_share for r in stream.offers}


def kt_shares(kt, campaign_id):
    return {o.offer_id: o.share for o in kt.offer_stream(campaign_id).offers}


def row(stream, offer_id):
    return next(r for r in stream.offers if r.offer_id == offer_id)


def test_fetch_imports_streams(setup):
    _, campaign, stream = setup
    assert [s.name for s in campaign.streams] == ["Flow 1", "Flow 2"]
    geo = campaign.streams[0]
    assert not geo.has_offers and geo.offers == []  # поток без офферов не разбираем
    assert shares(stream) == {A: 33, B: 33, C: 34}  # доли как в Keitaro, без пересчёта
    assert not stream.is_dirty


def test_add_offer_rebalances_and_waits_for_push(db, kt, setup):
    kt_campaign, _, stream = setup
    editor.add_offer(db, kt, stream, D)
    assert shares(stream) == {A: 25, B: 25, C: 25, D: 25}
    assert stream.is_dirty  # жёлтый
    assert kt_shares(kt, kt_campaign.id) == {A: 33, B: 33, C: 34}  # Keitaro не тронут


def test_push_publishes(db, kt, setup):
    kt_campaign, _, stream = setup
    editor.add_offer(db, kt, stream, D)
    editor.push(db, kt, stream)
    assert kt_shares(kt, kt_campaign.id) == {A: 25, B: 25, C: 25, D: 25}
    assert not stream.is_dirty


def test_cancel_drops_unpublished_offer(db, kt, setup):
    _, _, stream = setup
    editor.add_offer(db, kt, stream, D)
    editor.cancel(db, stream)
    assert shares(stream) == {A: 33, B: 33, C: 34}
    assert not stream.is_dirty


def test_remove_keeps_offer_in_archive(db, kt, setup):
    kt_campaign, _, stream = setup
    editor.remove_offer(db, stream, row(stream, C).id)
    assert row(stream, C).is_removed and row(stream, C).draft_share == 0
    assert shares(stream)[A] + shares(stream)[B] == 100
    assert stream.is_dirty

    editor.push(db, kt, stream)
    assert C not in kt_shares(kt, kt_campaign.id)
    assert row(stream, C).is_removed  # у нас остался серым
    assert not stream.is_dirty


def test_bring_back(db, kt, setup):
    kt_campaign, _, stream = setup
    editor.remove_offer(db, stream, row(stream, C).id)
    editor.push(db, kt, stream)

    editor.bring_back(db, stream, row(stream, C).id)
    assert stream.is_dirty and not row(stream, C).is_removed
    editor.push(db, kt, stream)
    assert kt_shares(kt, kt_campaign.id) == {A: 34, B: 33, C: 33}


def test_offer_removed_in_keitaro_becomes_removed_on_fetch(db, kt, setup):
    kt_campaign, _, stream = setup
    stream_kt_id = stream.kt_id
    kt.kt_set_offers(stream_kt_id, [(A, 100)])  # удалили руками в админке Keitaro

    _, report = fetch_campaign(db, kt, kt_campaign.id)
    assert report.removed == 2
    assert row(stream, B).is_removed and row(stream, C).is_removed
    assert shares(stream) == {A: 100, B: 0, C: 0}
    assert not stream.is_dirty

    editor.bring_back(db, stream, row(stream, B).id)  # и их можно вернуть
    editor.push(db, kt, stream)
    assert kt_shares(kt, kt_campaign.id) == {A: 50, B: 50}


def test_offer_returned_in_keitaro_becomes_active_on_fetch(db, kt, setup):
    kt_campaign, _, stream = setup
    editor.remove_offer(db, stream, row(stream, C).id)
    editor.push(db, kt, stream)
    kt.kt_set_offers(stream.kt_id, [(A, 40), (B, 30), (C, 30)])

    fetch_campaign(db, kt, kt_campaign.id)
    assert shares(stream) == {A: 40, B: 30, C: 30}
    assert not row(stream, C).is_removed


def test_pinned_share_survives_changes(db, kt, setup):
    _, _, stream = setup
    editor.set_pin(db, stream, row(stream, B).id, pinned=True, share=25)
    assert shares(stream) == {A: 38, B: 25, C: 37}

    editor.add_offer(db, kt, stream, D)
    assert shares(stream) == {A: 25, B: 25, C: 25, D: 25}

    editor.remove_offer(db, stream, row(stream, A).id)
    assert shares(stream) == {A: 0, B: 25, C: 38, D: 37}

    editor.set_pin(db, stream, row(stream, B).id, pinned=False)
    assert shares(stream) == {A: 0, B: 34, C: 33, D: 33}


def test_pin_over_100_is_rejected_and_nothing_changes(db, kt, setup):
    _, _, stream = setup
    editor.set_pin(db, stream, row(stream, A).id, pinned=True, share=60)
    with pytest.raises(UserError, match="110%"):
        editor.set_pin(db, stream, row(stream, B).id, pinned=True, share=50)
    db.expire_all()
    assert not row(stream, B).pinned
    assert shares(stream) == {A: 60, B: 20, C: 20}


def test_push_blocked_when_total_is_not_100(db, kt, setup):
    _, _, stream = setup
    editor.remove_offer(db, stream, row(stream, C).id)
    editor.set_pin(db, stream, row(stream, A).id, pinned=True, share=30)
    editor.set_pin(db, stream, row(stream, B).id, pinned=True, share=30)
    with pytest.raises(UserError, match="60%"):
        editor.push(db, kt, stream)


def test_push_detects_changes_made_in_keitaro(db, kt, setup):
    kt_campaign, _, stream = setup
    editor.add_offer(db, kt, stream, D)
    kt.kt_set_offers(stream.kt_id, [(A, 50), (B, 50)])  # кто-то правит в Keitaro параллельно
    with pytest.raises(ConflictError):
        editor.push(db, kt, stream)
    assert kt_shares(kt, kt_campaign.id) == {A: 50, B: 50}  # чужие правки не затёрты


def test_push_without_changes(db, kt, setup):
    with pytest.raises(UserError, match="Нет изменений"):
        editor.push(db, kt, setup[2])


def test_duplicates_are_rejected(db, kt, setup):
    _, _, stream = setup
    with pytest.raises(UserError, match="уже есть"):
        editor.add_offer(db, kt, stream, A)
    editor.remove_offer(db, stream, row(stream, A).id)
    with pytest.raises(UserError, match="Bring back"):
        editor.add_offer(db, kt, stream, A)


def test_removing_unpublished_offer_deletes_it(db, kt, setup):
    _, _, stream = setup
    editor.add_offer(db, kt, stream, D)
    editor.remove_offer(db, stream, row(stream, D).id)
    assert D not in shares(stream)
    assert shares(stream) == {A: 34, B: 33, C: 33}


def test_fetch_discards_drafts(db, kt, setup):
    kt_campaign, _, stream = setup
    editor.add_offer(db, kt, stream, D)
    editor.remove_offer(db, stream, row(stream, A).id)
    _, report = fetch_campaign(db, kt, kt_campaign.id)
    assert report.discarded_drafts == 4  # D добавлен, A удалён, у B и C изменились доли
    assert shares(stream) == {A: 33, B: 33, C: 34}
    assert not stream.is_dirty


def test_unknown_offer(db, kt, setup):
    with pytest.raises(NotFoundError):
        editor.add_offer(db, kt, setup[2], 999999)


def test_stream_without_offers_is_read_only(db, kt, setup):
    _, campaign, _ = setup
    with pytest.raises(UserError, match="нет офферов"):
        editor.add_offer(db, kt, campaign.streams[0], D)


def test_stream_deleted_in_keitaro_disappears(db, kt, setup):
    kt_campaign, campaign, stream = setup
    kt.streams[stream.kt_id].state = "deleted"
    fetch_campaign(db, kt, kt_campaign.id)
    assert [s.name for s in campaign.streams] == ["Flow 1"]
