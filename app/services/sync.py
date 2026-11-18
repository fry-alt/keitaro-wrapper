"""Fetch: подтягиваем кампанию и её потоки из Keitaro и сверяем с нашей базой.

Keitaro — источник правды для опубликованного состояния. Правила сверки офферов:
- оффер есть в Keitaro                -> активен, доля как в Keitaro;
- мы его знали, а в Keitaro его нет    -> removed (серый, с Bring back), а не удаляется;
- мы добавили, но не опубликовали      -> черновик отбрасывается.
Неопубликованные правки при Fetch теряются, поэтому интерфейс сначала спрашивает подтверждение.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.keitaro import KeitaroAPI
from app.keitaro.schemas import KtStream
from app.models import Campaign, Stream, StreamOffer, utcnow
from app.services.offers import ensure_offers

ACTIVE = "active"


@dataclass
class SyncReport:
    streams: int = 0
    added: int = 0
    removed: int = 0
    changed: int = 0
    discarded_drafts: int = 0

    def summary(self) -> str:
        parts = [f"потоков: {self.streams}"]
        if self.added:
            parts.append(f"новых офферов: {self.added}")
        if self.removed:
            parts.append(f"удалено в Keitaro: {self.removed}")
        if self.changed:
            parts.append(f"изменилось долей: {self.changed}")
        if self.discarded_drafts:
            parts.append(f"отменено неопубликованных правок: {self.discarded_drafts}")
        return "Синхронизировано с Keitaro — " + ", ".join(parts)


def domain_url(name: str) -> str:
    name = name.strip().rstrip("/")
    return name if "://" in name else f"https://{name}"


def fetch_campaign(db: Session, kt: KeitaroAPI, kt_campaign_id: int) -> tuple[Campaign, SyncReport]:
    kt_campaign = kt.get_campaign(kt_campaign_id)
    kt_streams = kt.get_campaign_streams(kt_campaign_id)
    report = SyncReport(streams=len(kt_streams))

    campaign = db.scalar(select(Campaign).where(Campaign.kt_id == kt_campaign.id))
    if campaign is None:
        campaign = Campaign(kt_id=kt_campaign.id)
        db.add(campaign)
    campaign.name = kt_campaign.name
    campaign.alias = kt_campaign.alias
    campaign.state = kt_campaign.state
    if kt_campaign.domain_id:
        names = {d.id: d.name for d in kt.list_domains()}
        if kt_campaign.domain_id in names:
            campaign.domain = domain_url(names[kt_campaign.domain_id])

    ensure_offers(db, kt, {o.offer_id for s in kt_streams for o in s.offers})

    existing = {s.kt_id: s for s in campaign.streams}
    seen: set[int] = set()
    for kt_stream in kt_streams:
        seen.add(kt_stream.id)
        stream = existing.get(kt_stream.id)
        if stream is None:
            stream = Stream(kt_id=kt_stream.id)
            campaign.streams.append(stream)
        stream.name = kt_stream.name or f"Поток #{kt_stream.id}"
        stream.type = kt_stream.type
        stream.position = kt_stream.position
        stream.schema = kt_stream.schema_ or ""
        stream.state = kt_stream.state
        stream.kt_updated_at = kt_stream.updated_at
        stream.synced_at = utcnow()
        _sync_offers(stream, kt_stream, report)
        # поток без офферов просто показываем названием; поток, из которого в Keitaro
        # удалили все офферы, остаётся «офферным», чтобы был виден архив
        stream.has_offers = bool(kt_stream.offers) or bool(stream.offers)

    for kt_id, stream in existing.items():
        if kt_id not in seen:  # поток удалили в Keitaro
            campaign.streams.remove(stream)

    campaign.synced_at = utcnow()
    db.commit()
    return campaign, report


def _sync_offers(stream: Stream, kt_stream: KtStream, report: SyncReport) -> None:
    remote = {o.offer_id: o.share for o in kt_stream.offers if o.state == ACTIVE}
    local_ids = set()

    for row in list(stream.offers):
        local_ids.add(row.offer_id)
        if row.is_dirty:
            report.discarded_drafts += 1
        if row.offer_id in remote:
            share = remote[row.offer_id]
            if row.published_active and row.published_share != share:
                report.changed += 1
            row.published_active = row.draft_active = True
            row.published_share = row.draft_share = share
        elif row.is_new:
            stream.offers.remove(row)
        else:
            if row.published_active:
                report.removed += 1
            row.published_active = row.draft_active = False
            row.published_share = row.draft_share = 0
            row.pinned = False

    next_position = max((r.position for r in stream.offers), default=0) + 1
    for offer_id, share in remote.items():
        if offer_id in local_ids:
            continue
        stream.offers.append(
            StreamOffer(
                offer_id=offer_id,
                position=next_position,
                draft_active=True,
                draft_share=share,
                published_active=True,
                published_share=share,
            )
        )
        next_position += 1
        report.added += 1
