"""Редактор офферов в потоке: add / remove / bring back / pin / push / cancel.

Все правки меняют только черновик (draft_*). В Keitaro они уходят по Push.
"""

from sqlalchemy.orm import Session

from app.keitaro import KeitaroAPI
from app.models import Stream, StreamOffer, utcnow
from app.services.errors import ConflictError, NotFoundError, UserError
from app.services.offers import resolve_offer
from app.services.shares import ShareError, Slot, allocate, total_problem

ACTIVE = "active"


def get_stream(db: Session, stream_id: int) -> Stream:
    stream = db.get(Stream, stream_id)
    if stream is None:
        raise NotFoundError("Поток не найден. Обновите страницу")
    return stream


def _get_row(stream: Stream, row_id: int) -> StreamOffer:
    for row in stream.offers:
        if row.id == row_id:
            return row
    raise NotFoundError("Оффер не найден в потоке. Обновите страницу")


def rebalance(stream: Stream) -> None:
    """Пересчитывает доли активных офферов. У удалённых доля 0."""
    active = stream.active_offers
    shares = allocate(
        [Slot(key=i, pinned=r.pinned, share=r.draft_share) for i, r in enumerate(active)]
    )
    for i, row in enumerate(active):
        row.draft_share = shares[i]
    for row in stream.offers:
        if not row.draft_active:
            row.draft_share = 0


def _commit_or_rollback(db: Session, stream: Stream) -> None:
    try:
        rebalance(stream)
    except ShareError as exc:
        db.rollback()
        raise UserError(str(exc)) from exc
    db.commit()


def add_offer(db: Session, kt: KeitaroAPI, stream: Stream, offer_id: int) -> StreamOffer:
    if not stream.has_offers:
        raise UserError("В этом потоке нет офферов, его нельзя редактировать здесь")
    for row in stream.offers:
        if row.offer_id == offer_id:
            if row.is_removed:
                raise UserError(
                    "Этот оффер уже есть в потоке среди удалённых — верните его кнопкой Bring back"
                )
            raise UserError("Этот оффер уже есть в потоке")
    resolve_offer(db, kt, offer_id)
    row = StreamOffer(
        offer_id=offer_id,
        position=max((r.position for r in stream.offers), default=0) + 1,
        draft_active=True,
        draft_share=0,
        published_active=None,
        published_share=None,
    )
    stream.offers.append(row)
    _commit_or_rollback(db, stream)
    return row


def remove_offer(db: Session, stream: Stream, row_id: int) -> None:
    row = _get_row(stream, row_id)
    if row.is_removed:
        return
    if row.is_new:
        # в Keitaro его не было: просто убираем привязку
        stream.offers.remove(row)
    else:
        row.draft_active = False
        row.draft_share = 0
        row.pinned = False
    _commit_or_rollback(db, stream)


def bring_back(db: Session, stream: Stream, row_id: int) -> None:
    row = _get_row(stream, row_id)
    if not row.is_removed:
        return
    row.draft_active = True
    _commit_or_rollback(db, stream)


def set_pin(
    db: Session, stream: Stream, row_id: int, pinned: bool, share: int | None = None
) -> None:
    row = _get_row(stream, row_id)
    if row.is_removed:
        raise UserError("Нельзя закрепить долю удалённого оффера")
    row.pinned = pinned
    if pinned and share is not None:
        row.draft_share = share
    _commit_or_rollback(db, stream)


def cancel(db: Session, stream: Stream) -> None:
    """Возвращает поток к опубликованному состоянию."""
    for row in list(stream.offers):
        if row.is_new:
            stream.offers.remove(row)
        else:
            row.draft_active = bool(row.published_active)
            row.draft_share = row.published_share or 0
    db.commit()


def push(db: Session, kt: KeitaroAPI, stream: Stream) -> None:
    """Публикует черновик потока в Keitaro."""
    if not stream.is_dirty:
        raise UserError("Нет изменений для публикации")
    active = stream.active_offers
    problem = total_problem([r.draft_share for r in active])
    if problem:
        raise UserError(problem)

    # Защита от затирания чужих правок: Keitaro должен совпадать с тем, что мы видели
    remote = kt.get_stream(stream.kt_id)
    remote_offers = {o.offer_id: o.share for o in remote.offers if o.state == ACTIVE}
    known_offers = {r.offer_id: r.published_share for r in stream.offers if r.published_active}
    if remote_offers != known_offers:
        raise ConflictError(
            "Поток изменили в Keitaro после последней синхронизации. "
            "Нажмите Fetch, чтобы подтянуть актуальное состояние, и повторите правки"
        )

    sent = {r.offer_id: r.draft_share for r in active}
    updated = kt.update_stream(
        stream.kt_id,
        {
            "offers": [{"offer_id": o, "share": s, "state": ACTIVE} for o, s in sent.items()],
        },
    )
    saved = {o.offer_id: o.share for o in updated.offers if o.state == ACTIVE}
    if saved != sent:
        raise ConflictError(
            "Keitaro сохранил поток не так, как мы отправили. Нажмите Fetch и проверьте поток"
        )

    for row in stream.offers:
        row.published_active = row.draft_active
        row.published_share = row.draft_share
    stream.kt_updated_at = updated.updated_at
    stream.synced_at = utcnow()
    db.commit()
