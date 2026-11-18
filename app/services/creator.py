"""Создание кампании по ТЗ: два потока.

Поток 1: всех из выбранных стран редиректит на Google.
Поток 2: остальных отправляет на выбранный оффер.
У кампании выставлены домен, группа и источник из настроек.
"""

import secrets
import string
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.config import Settings
from app.keitaro import KeitaroAPI, KeitaroError
from app.models import Campaign
from app.services.countries import parse_countries
from app.services.errors import UserError
from app.services.offers import get_active_offer
from app.services.sync import fetch_campaign

ALIAS_ALPHABET = string.ascii_letters + string.digits
MAX_NAME_LENGTH = 200


@dataclass(frozen=True)
class CampaignRefs:
    domain_id: int
    group_id: int
    traffic_source_id: int


def normalize_name(raw: str, prefix: str) -> str:
    """Имя всегда начинается с префикса test_фамилия_: если его нет, добавляем."""
    name = raw.strip()
    if not name or name == prefix:
        raise UserError("Введите название кампании")
    if not name.startswith(prefix):
        name = prefix + name
    if len(name) > MAX_NAME_LENGTH:
        raise UserError(f"Название длиннее {MAX_NAME_LENGTH} символов")
    return name


def generate_alias(length: int = 8) -> str:
    return "".join(secrets.choice(ALIAS_ALPHABET) for _ in range(length))


def _bare_domain(name: str) -> str:
    return name.lower().split("://")[-1].strip("/")


def resolve_refs(kt: KeitaroAPI, settings: Settings) -> CampaignRefs:
    """Находит ID домена, группы и источника по названиям из настроек."""
    domain = next(
        (
            d
            for d in kt.list_domains()
            if _bare_domain(d.name) == _bare_domain(settings.default_domain)
        ),
        None,
    )
    group = next(
        (
            g
            for g in kt.list_groups("campaigns")
            if g.name.lower() == settings.default_group.lower()
        ),
        None,
    )
    source = next(
        (
            t
            for t in kt.list_traffic_sources()
            if t.name.lower() == settings.default_traffic_source.lower()
        ),
        None,
    )
    missing = [
        label
        for label, value, found in [
            ("домен", settings.default_domain, domain),
            ("группа", settings.default_group, group),
            ("источник", settings.default_traffic_source, source),
        ]
        if found is None
    ]
    if missing:
        raise UserError("Не найдено в Keitaro: " + ", ".join(missing))
    return CampaignRefs(domain_id=domain.id, group_id=group.id, traffic_source_id=source.id)


def campaign_payload(name: str, alias: str, refs: CampaignRefs) -> dict:
    return {
        "name": name,
        "alias": alias,
        "type": "position",  # потоки проверяются по порядку: сначала гео, потом оффер
        "state": "active",
        "cost_type": "CPC",
        "cost_value": 0,
        "cost_currency": "USD",
        "cost_auto": True,
        "domain_id": refs.domain_id,
        "group_id": refs.group_id,
        "traffic_source_id": refs.traffic_source_id,
    }


def geo_stream_payload(campaign_id: int, countries: list[str], redirect_url: str) -> dict:
    return {
        "campaign_id": campaign_id,
        "type": "regular",
        "name": f"{', '.join(countries)} → Google",
        "position": 1,
        "state": "active",
        "schema": "redirect",
        "action_type": "http",
        "action_payload": redirect_url,
        "collect_clicks": True,
        "filter_or": False,
        "filters": [{"name": "country", "mode": "accept", "payload": countries}],
    }


def offer_stream_payload(campaign_id: int, offer_id: int) -> dict:
    return {
        "campaign_id": campaign_id,
        "type": "regular",
        "name": "Offers",
        "position": 2,
        "state": "active",
        "schema": "landings",
        "action_type": "http",
        "collect_clicks": True,
        "offers": [{"offer_id": offer_id, "share": 100, "state": "active"}],
    }


def create_campaign(
    db: Session,
    kt: KeitaroAPI,
    settings: Settings,
    *,
    name: str,
    countries: str | list[str],
    offer_id: int,
) -> Campaign:
    # сначала проверяем всё, что можно, чтобы не создавать в Keitaro полуфабрикат
    full_name = normalize_name(name, settings.campaign_prefix)
    country_codes = parse_countries(countries)
    get_active_offer(db, kt, offer_id)
    refs = resolve_refs(kt, settings)

    kt_campaign = kt.create_campaign(campaign_payload(full_name, generate_alias(), refs))
    try:
        kt.create_stream(geo_stream_payload(kt_campaign.id, country_codes, settings.redirect_url))
        kt.create_stream(offer_stream_payload(kt_campaign.id, offer_id))
    except KeitaroError as exc:
        # откат: кампания без потоков никому не нужна
        try:
            kt.delete_campaign(kt_campaign.id)
            cleanup = "Кампания удалена, чтобы не оставлять мусор в Keitaro"
        except KeitaroError:
            cleanup = f"Удалите кампанию #{kt_campaign.id} в Keitaro вручную"
        raise UserError(f"Не удалось создать потоки: {exc.message}. {cleanup}") from exc

    campaign, _ = fetch_campaign(db, kt, kt_campaign.id)
    return campaign
