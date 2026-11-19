"""Keitaro в памяти: для тестов и для запуска без настоящего трекера (KEITARO_FAKE=1).

Ведёт себя как настоящее API в тех местах, на которые опирается приложение:
PUT потока с полем offers заменяет список офферов целиком, у потока меняется
updated_at, удалённые кампании и потоки не попадают в выдачу.
"""

import itertools
from typing import Any

from app.keitaro.client import KeitaroError
from app.keitaro.schemas import (
    KtCampaign,
    KtDomain,
    KtGroup,
    KtOffer,
    KtStream,
    KtStreamOffer,
    KtTrafficSource,
)


class FakeKeitaro:
    def __init__(self) -> None:
        self._ids = itertools.count(1001)
        self._version = itertools.count(1)
        self.campaigns: dict[int, KtCampaign] = {}
        self.streams: dict[int, KtStream] = {}
        self.offers: dict[int, KtOffer] = {}
        # офферы, которые существуют, но пользователю ключа не видны: GET отвечает 403,
        # а поставить их в поток можно (так устроены права в настоящем трекере)
        self.hidden_offers: set[int] = set()
        self.domains: list[KtDomain] = []
        self.groups: list[KtGroup] = []
        self.traffic_sources: list[KtTrafficSource] = []
        # имя метода -> ошибка, которую он бросит (для проверки обработки сбоев)
        self.fail_on: dict[str, KeitaroError] = {}
        self.calls: list[str] = []

    # --- наполнение для тестов ---

    def seed_defaults(self) -> "FakeKeitaro":
        self.domains = [KtDomain(id=1, name="kylovoa.com")]
        self.groups = [KtGroup(id=7, name="FORTESTS", type="campaigns")]
        self.traffic_sources = [KtTrafficSource(id=9, name="FORTESTS")]
        for offer_id, name in [
            (3717, "Miaflow [BEAUTY-RO-BE_0008] [pl - prize ro]"),
            (3749, "Miaflow [BEAUTY-RO-BE_0009] [pl - spin ro]"),
            (11111, "FitoMishki [Weight Loss-CO-DT_0309] [pl es]"),
            (11112, "Oxys [BEAUTY-CL-BE_0155] [pl es]"),
            (11113, "Keto Light [Diet-MX-DT_0042] [pl es]"),
        ]:
            self.add_offer(offer_id, name)
        return self

    def add_offer(self, offer_id: int, name: str) -> KtOffer:
        self.offers[offer_id] = KtOffer(id=offer_id, name=name, state="active")
        return self.offers[offer_id]

    def add_campaign(self, name: str, offers: list[tuple[int, int]]) -> KtCampaign:
        """Кампания как в ТЗ: поток-редирект на Google и поток с офферами (offer_id, share)."""
        campaign = self.create_campaign({"name": name, "alias": f"a{next(self._ids)}"})
        self.create_stream(
            {
                "campaign_id": campaign.id,
                "name": "Flow 1",
                "position": 1,
                "schema": "redirect",
                "action_type": "http",
                "action_payload": "https://google.com",
                "filters": [{"name": "country", "mode": "accept", "payload": ["AU"]}],
            }
        )
        self.create_stream(
            {
                "campaign_id": campaign.id,
                "name": "Flow 2",
                "position": 2,
                "schema": "landings",
                "offers": [{"offer_id": o, "share": s, "state": "active"} for o, s in offers],
            }
        )
        return campaign

    def kt_set_offers(self, stream_id: int, offers: list[tuple[int, int]]) -> None:
        """Имитирует правку потока руками в админке Keitaro."""
        self.update_stream(
            stream_id,
            {
                "offers": [{"offer_id": o, "share": s, "state": "active"} for o, s in offers],
            },
        )

    def offer_stream(self, campaign_id: int) -> KtStream:
        return next(s for s in self.get_campaign_streams(campaign_id) if s.offers)

    # --- служебное ---

    def _call(self, name: str) -> None:
        self.calls.append(name)
        if name in self.fail_on:
            raise self.fail_on[name]

    @staticmethod
    def _copy[T: KtCampaign | KtStream](obj: T) -> T:
        return obj.model_copy(deep=True)

    def _build_offers(self, offers: list[dict[str, Any]]) -> list[KtStreamOffer]:
        built = []
        for item in offers:
            if item["offer_id"] not in self.offers and item["offer_id"] not in self.hidden_offers:
                raise KeitaroError(f"Offer {item['offer_id']} not found", 422)
            built.append(
                KtStreamOffer(
                    id=next(self._ids),
                    offer_id=item["offer_id"],
                    share=item.get("share", 0),
                    state=item.get("state", "active"),
                )
            )
        return built

    # --- API ---

    def list_campaigns(self) -> list[KtCampaign]:
        self._call("list_campaigns")
        return [self._copy(c) for c in self.campaigns.values() if c.state != "deleted"]

    def get_campaign(self, campaign_id: int) -> KtCampaign:
        self._call("get_campaign")
        campaign = self.campaigns.get(campaign_id)
        if campaign is None or campaign.state == "deleted":
            raise KeitaroError("Объект не найден в Keitaro (404)", 404)
        return self._copy(campaign)

    def create_campaign(self, payload: dict[str, Any]) -> KtCampaign:
        self._call("create_campaign")
        if not payload.get("name") or not payload.get("alias"):
            raise KeitaroError("name and alias are required", 422)
        if any(c.alias == payload["alias"] for c in self.campaigns.values()):
            raise KeitaroError("Alias already exists", 422)
        # как настоящий Keitaro, отдаём полный адрес домена кампании
        domain = next((d.name for d in self.domains if d.id == payload.get("domain_id")), None)
        campaign = KtCampaign.model_validate(
            {
                "state": "active",
                **payload,
                "id": next(self._ids),
                "domain": f"https://{domain}/" if domain else None,
            }
        )
        self.campaigns[campaign.id] = campaign
        return self._copy(campaign)

    def delete_campaign(self, campaign_id: int) -> None:
        self._call("delete_campaign")
        if campaign_id in self.campaigns:
            self.campaigns[campaign_id].state = "deleted"

    def get_campaign_streams(self, campaign_id: int) -> list[KtStream]:
        self._call("get_campaign_streams")
        streams = [
            s
            for s in self.streams.values()
            if s.campaign_id == campaign_id and s.state != "deleted"
        ]
        return [self._copy(s) for s in sorted(streams, key=lambda s: s.position)]

    def get_stream(self, stream_id: int) -> KtStream:
        self._call("get_stream")
        if stream_id not in self.streams:
            raise KeitaroError("Объект не найден в Keitaro (404)", 404)
        return self._copy(self.streams[stream_id])

    def create_stream(self, payload: dict[str, Any]) -> KtStream:
        self._call("create_stream")
        if payload.get("campaign_id") not in self.campaigns:
            raise KeitaroError("Campaign not found", 422)
        data = {"type": "regular", "state": "active", **payload}
        data["offers"] = self._build_offers(payload.get("offers", []))
        stream = KtStream.model_validate(
            {
                **data,
                "id": next(self._ids),
                "updated_at": f"v{next(self._version)}",
            }
        )
        self.streams[stream.id] = stream
        return self._copy(stream)

    def update_stream(self, stream_id: int, payload: dict[str, Any]) -> KtStream:
        self._call("update_stream")
        if stream_id not in self.streams:
            raise KeitaroError("Объект не найден в Keitaro (404)", 404)
        data = self.streams[stream_id].model_dump(by_alias=True)
        data.update({k: v for k, v in payload.items() if k not in ("id", "offers")})
        if "offers" in payload:
            data["offers"] = [o.model_dump() for o in self._build_offers(payload["offers"])]
        data["updated_at"] = f"v{next(self._version)}"
        self.streams[stream_id] = KtStream.model_validate(data)
        return self._copy(self.streams[stream_id])

    def list_offers(self) -> list[KtOffer]:
        self._call("list_offers")
        return [o.model_copy() for o in self.offers.values()]

    def get_offer(self, offer_id: int) -> KtOffer:
        self._call("get_offer")
        if offer_id in self.hidden_offers:
            raise KeitaroError("Keitaro не дал доступа (403)", 403)
        if offer_id not in self.offers:
            raise KeitaroError("Объект не найден в Keitaro (404)", 404)
        return self.offers[offer_id].model_copy()

    def list_domains(self) -> list[KtDomain]:
        self._call("list_domains")
        return list(self.domains)

    def list_groups(self, group_type: str = "campaigns") -> list[KtGroup]:
        self._call("list_groups")
        return [g for g in self.groups if g.type in (None, group_type)]

    def list_traffic_sources(self) -> list[KtTrafficSource]:
        self._call("list_traffic_sources")
        return list(self.traffic_sources)
