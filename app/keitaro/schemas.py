"""Объекты Keitaro Admin API, которые нужны приложению.

Лишние поля не выбрасываем (extra="allow"): API может вернуть больше, чем описано,
а нам важно не сломаться на неизвестном поле.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class KtModel(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class KtStreamOffer(KtModel):
    id: int | None = None
    offer_id: int
    share: int = 0
    state: str = "active"


class KtFilter(KtModel):
    id: int | None = None
    name: str
    mode: str = "accept"
    payload: Any = None


class KtStream(KtModel):
    id: int
    campaign_id: int
    name: str | None = ""
    type: str = "regular"
    position: int = 0
    state: str = "active"
    # поле называется schema, но это имя занято в BaseModel, поэтому через alias
    schema_: str | None = Field(default="", alias="schema")
    action_type: str | None = None
    action_payload: Any = None
    updated_at: str | None = None
    filters: list[KtFilter] = []
    offers: list[KtStreamOffer] = []


class KtCampaign(KtModel):
    id: int
    name: str
    alias: str = ""
    state: str = "active"
    type: str | None = None
    domain_id: int | None = None
    group_id: int | None = None
    traffic_source_id: int | None = None


class KtOffer(KtModel):
    id: int
    name: str
    state: str | None = "active"


class KtDomain(KtModel):
    id: int
    name: str


class KtGroup(KtModel):
    id: int
    name: str
    type: str | None = None


class KtTrafficSource(KtModel):
    id: int
    name: str
