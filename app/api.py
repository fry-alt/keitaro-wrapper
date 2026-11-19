"""JSON API: поиск офферов для автодополнения и создание кампании без интерфейса."""

from contextlib import suppress
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_kt
from app.keitaro import KeitaroAPI, KeitaroError
from app.models import Campaign
from app.services.creator import create_campaign
from app.services.errors import NotFoundError, UserError
from app.services.offers import placeholder_name, refresh_if_stale, search_offers

router = APIRouter(prefix="/api", tags=["api"])

Db = Annotated[Session, Depends(get_db)]
Kt = Annotated[KeitaroAPI, Depends(get_kt)]
Conf = Annotated[Settings, Depends(get_settings)]


class OfferOut(BaseModel):
    id: int
    name: str
    label: str


class CampaignIn(BaseModel):
    name: str = Field(examples=["campaign1"])
    countries: list[str] = Field(examples=[["AU"]])
    offer_id: int = Field(examples=[3749])


class StreamOfferOut(BaseModel):
    offer_id: int
    name: str
    share: int
    pinned: bool
    status: str


class StreamOut(BaseModel):
    id: int
    kt_id: int
    name: str
    position: int
    has_offers: bool
    unpublished: bool
    offers: list[StreamOfferOut]


class CampaignOut(BaseModel):
    id: int
    kt_id: int
    name: str
    url: str | None
    editor_url: str
    streams: list[StreamOut]


def campaign_out(campaign: Campaign) -> CampaignOut:
    return CampaignOut(
        id=campaign.id,
        kt_id=campaign.kt_id,
        name=campaign.name,
        url=campaign.url,
        editor_url=f"/campaigns/{campaign.id}",
        streams=[
            StreamOut(
                id=s.id,
                kt_id=s.kt_id,
                name=s.name,
                position=s.position,
                has_offers=s.has_offers,
                unpublished=s.is_dirty,
                offers=[
                    StreamOfferOut(
                        offer_id=r.offer_id,
                        name=r.offer.name,
                        share=r.draft_share,
                        pinned=r.pinned,
                        status="removed" if r.is_removed else "new" if r.is_new else "active",
                    )
                    for r in s.offers
                ],
            )
            for s in campaign.streams
        ],
    )


@router.get("/offers", response_model=list[OfferOut])
def offers(db: Db, kt: Kt, settings: Conf, q: str = ""):
    # если Keitaro недоступен, ищем по тому, что уже скачано
    with suppress(KeitaroError):
        refresh_if_stale(db, kt, settings.offers_cache_ttl)
    found = [
        OfferOut(id=o.id, name=o.name, label=f"[{o.id}] {o.name}") for o in search_offers(db, q)
    ]
    # часть офферов ключу не видна, но в поток ставится: даём выбрать их по ID.
    # Существует ли такой оффер, проверим при добавлении
    query = q.strip()
    if query.isdigit() and all(o.id != int(query) for o in found):
        found.insert(
            0, OfferOut(id=int(query), name=placeholder_name(int(query)), label=f"[{query}] по ID")
        )
    return found


@router.post("/campaigns", response_model=CampaignOut, status_code=201)
def create(body: CampaignIn, db: Db, kt: Kt, settings: Conf):
    try:
        campaign = create_campaign(
            db, kt, settings, name=body.name, countries=body.countries, offer_id=body.offer_id
        )
    except NotFoundError as exc:
        raise HTTPException(404, exc.message) from exc
    except UserError as exc:
        raise HTTPException(422, exc.message) from exc
    except KeitaroError as exc:
        raise HTTPException(502, exc.message) from exc
    return campaign_out(campaign)


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: int, db: Db):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        raise HTTPException(404, "Кампания не найдена")
    return campaign_out(campaign)
