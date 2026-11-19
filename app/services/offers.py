"""Локальный каталог офферов.

В Keitaro у GET /offers нет поиска, поэтому каталог целиком копируется к нам
и автодополнение ищет по своей базе. Копия обновляется, если устарела.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.keitaro import KeitaroAPI, KeitaroError
from app.models import Offer, utcnow
from app.services.errors import NotFoundError

ACTIVE = "active"
# оффер существует, но API-ключу закрыт на чтение (GET /offers/{id} -> 403). Название
# неизвестно, а поставить его в поток можно
HIDDEN = "hidden"
USABLE = (ACTIVE, HIDDEN)


def placeholder_name(offer_id: int) -> str:
    return f"Оффер #{offer_id}"


def as_utc(value: datetime) -> datetime:
    # SQLite не хранит часовой пояс и возвращает «наивное» время, хотя пишем мы UTC
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def refresh_offers(db: Session, kt: KeitaroAPI) -> int:
    """Перекачивает каталог из Keitaro. Офферы, пропавшие из Keitaro, помечаются архивными."""
    remote = {o.id: o for o in kt.list_offers()}
    now = utcnow()
    local = {o.id: o for o in db.scalars(select(Offer))}
    for offer_id, kt_offer in remote.items():
        offer = local.get(offer_id) or Offer(id=offer_id)
        offer.name = kt_offer.name
        offer.state = kt_offer.state or ACTIVE
        offer.synced_at = now
        db.add(offer)
    for offer_id, offer in local.items():
        # закрытых офферов в каталоге и не должно быть, это не признак удаления
        if offer_id not in remote and offer.state != HIDDEN:
            # строку не удаляем: на неё могут ссылаться офферы в потоках
            offer.state = "archived"
            offer.synced_at = now
    db.commit()
    return len(remote)


def refresh_if_stale(db: Session, kt: KeitaroAPI, ttl_seconds: int) -> bool:
    newest = db.scalar(select(func.max(Offer.synced_at)))
    if newest is None or utcnow() - as_utc(newest) > timedelta(seconds=ttl_seconds):
        refresh_offers(db, kt)
        return True
    return False


def search_offers(db: Session, query: str, limit: int = 20) -> list[Offer]:
    """Поиск по ID и названию. Точное совпадение ID — первым."""
    stmt = select(Offer).where(Offer.state.in_(USABLE))
    query = query.strip()
    if query:
        pattern = f"%{query.lower()}%"
        conditions = [func.lower(Offer.name).like(pattern), cast(Offer.id, String).like(pattern)]
        stmt = stmt.where(or_(*conditions))
        if query.isdigit():
            stmt = stmt.order_by((Offer.id == int(query)).desc())
    return list(db.scalars(stmt.order_by(Offer.id.desc()).limit(limit)))


def ensure_offers(db: Session, kt: KeitaroAPI, offer_ids: set[int]) -> None:
    """Гарантирует, что офферы есть в каталоге (нужно для связи с потоками)."""
    missing = offer_ids - set(db.scalars(select(Offer.id).where(Offer.id.in_(offer_ids))))
    if not missing:
        return
    refresh_offers(db, kt)
    still_missing = missing - set(db.scalars(select(Offer.id).where(Offer.id.in_(missing))))
    for offer_id in still_missing:
        # оффер стоит в потоке, но в каталоге его нет: он закрыт для ключа
        db.add(Offer(id=offer_id, name=placeholder_name(offer_id), state=HIDDEN))
    db.flush()


def resolve_offer(db: Session, kt: KeitaroAPI, offer_id: int) -> Offer:
    """Проверяет, что оффер можно поставить в поток.

    Сначала смотрим каталог, потом спрашиваем Keitaro напрямую: часть офферов
    ключу не видна (403), но существует и в поток ставится. 404 — оффера нет.
    """
    offer = db.get(Offer, offer_id)
    if offer is not None and offer.state in USABLE:
        return offer
    try:
        kt_offer = kt.get_offer(offer_id)
    except KeitaroError as exc:
        if exc.status_code == 403:
            offer = offer or Offer(id=offer_id, name=placeholder_name(offer_id))
            offer.state = HIDDEN
            offer.synced_at = utcnow()
            db.add(offer)
            db.flush()
            return offer
        if exc.status_code == 404:
            raise NotFoundError(f"Оффера #{offer_id} нет в Keitaro") from exc
        raise
    offer = offer or Offer(id=offer_id)
    offer.name = kt_offer.name
    offer.state = kt_offer.state or ACTIVE
    offer.synced_at = utcnow()
    db.add(offer)
    db.flush()
    if offer.state != ACTIVE:
        raise NotFoundError(f"Оффер #{offer_id} в Keitaro не активен ({offer.state})")
    return offer
