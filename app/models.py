"""Таблицы приложения.

Главная идея: у оффера в потоке два состояния.
- published_* — что сейчас лежит в Keitaro (по данным последнего Fetch или Push);
- draft_* — что пользователь видит и правит у нас.
Поток «жёлтый» (не опубликован), если черновик хоть у одного оффера отличается
от опубликованного. Push делает published = draft, Cancel делает draft = published.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    kt_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    alias: Mapped[str] = mapped_column(String(255), default="")
    state: Mapped[str] = mapped_column(String(32), default="active")
    domain: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    streams: Mapped[list["Stream"]] = relationship(
        back_populates="campaign",
        cascade="all, delete-orphan",
        order_by="Stream.position",
    )

    @property
    def url(self) -> str | None:
        """Ссылка кампании, по которой приходит трафик."""
        if not self.domain or not self.alias:
            return None
        return f"{self.domain.rstrip('/')}/{self.alias}"


class Stream(Base):
    __tablename__ = "streams"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"))
    kt_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    type: Mapped[str] = mapped_column(String(32), default="regular")
    position: Mapped[int] = mapped_column(Integer, default=0)
    schema: Mapped[str] = mapped_column(String(32), default="")
    state: Mapped[str] = mapped_column(String(32), default="active")
    # Поток с офферами показываем подробно, остальные — только названием
    has_offers: Mapped[bool] = mapped_column(Boolean, default=False)
    # updated_at потока в Keitaro на момент последней синхронизации
    kt_updated_at: Mapped[str | None] = mapped_column(String(64))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    campaign: Mapped[Campaign] = relationship(back_populates="streams")
    offers: Mapped[list["StreamOffer"]] = relationship(
        back_populates="stream",
        cascade="all, delete-orphan",
        order_by="StreamOffer.position",
    )

    @property
    def active_offers(self) -> list["StreamOffer"]:
        return [o for o in self.offers if o.draft_active]

    @property
    def share_total(self) -> int:
        return sum(o.draft_share for o in self.active_offers)

    @property
    def is_dirty(self) -> bool:
        return any(o.is_dirty for o in self.offers)


class Offer(Base):
    """Локальная копия каталога офферов Keitaro: по ней работает автодополнение."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(32), default="active")
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class StreamOffer(Base):
    """Оффер внутри потока: черновик у нас и то, что опубликовано в Keitaro."""

    __tablename__ = "stream_offers"
    __table_args__ = (UniqueConstraint("stream_id", "offer_id", name="uq_stream_offer"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("streams.id", ondelete="CASCADE"))
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    # порядок добавления: от него зависит, кому достанется лишний процент
    position: Mapped[int] = mapped_column(Integer, default=0)

    draft_active: Mapped[bool] = mapped_column(Boolean, default=True)
    draft_share: Mapped[int] = mapped_column(Integer, default=0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)

    # None — оффер ещё ни разу не публиковался в Keitaro
    published_active: Mapped[bool | None] = mapped_column(Boolean)
    published_share: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    stream: Mapped[Stream] = relationship(back_populates="offers")
    offer: Mapped[Offer] = relationship(lazy="joined")

    @property
    def is_new(self) -> bool:
        return self.published_active is None

    @property
    def is_removed(self) -> bool:
        return not self.draft_active

    @property
    def is_dirty(self) -> bool:
        if self.is_new:
            return self.draft_active
        if self.draft_active != self.published_active:
            return True
        return self.draft_active and self.draft_share != self.published_share
