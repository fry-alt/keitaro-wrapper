"""HTTP-клиент Keitaro Admin API (документация: https://admin-api.docs.keitaro.io/)."""

import time
from typing import Any, Protocol

import httpx

from app.keitaro.schemas import (
    KtCampaign,
    KtDomain,
    KtGroup,
    KtOffer,
    KtStream,
    KtTrafficSource,
)

API_PREFIX = "/admin_api/v1"
# Повторяем только идемпотентные запросы: повтор POST мог бы создать кампанию дважды
RETRY_METHODS = {"GET", "PUT", "DELETE"}
RETRY_STATUSES = {502, 503, 504}


class KeitaroError(Exception):
    """Ошибка обращения к Keitaro с текстом, который можно показать пользователю."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class KeitaroAPI(Protocol):
    """Что приложению нужно от Keitaro. Реализации: KeitaroClient и FakeKeitaro."""

    def list_campaigns(self) -> list[KtCampaign]: ...
    def get_campaign(self, campaign_id: int) -> KtCampaign: ...
    def create_campaign(self, payload: dict[str, Any]) -> KtCampaign: ...
    def delete_campaign(self, campaign_id: int) -> None: ...
    def get_campaign_streams(self, campaign_id: int) -> list[KtStream]: ...
    def get_stream(self, stream_id: int) -> KtStream: ...
    def create_stream(self, payload: dict[str, Any]) -> KtStream: ...
    def update_stream(self, stream_id: int, payload: dict[str, Any]) -> KtStream: ...
    def list_offers(self) -> list[KtOffer]: ...
    def list_domains(self) -> list[KtDomain]: ...
    def list_groups(self, group_type: str = "campaigns") -> list[KtGroup]: ...
    def list_traffic_sources(self) -> list[KtTrafficSource]: ...


class KeitaroClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 20.0,
        retries: int = 2,
        backoff: float = 0.5,
        transport: httpx.BaseTransport | None = None,
    ):
        self.retries = retries
        self.backoff = backoff
        self._http = httpx.Client(
            base_url=base_url.rstrip("/") + API_PREFIX,
            headers={"Api-Key": api_key, "Accept": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    # --- низкий уровень ---

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        attempts = 1 + (self.retries if method in RETRY_METHODS else 0)
        for attempt in range(1, attempts + 1):
            last_try = attempt == attempts
            try:
                response = self._http.request(method, path, **kwargs)
            except httpx.TimeoutException as exc:
                if last_try:
                    raise KeitaroError("Keitaro не ответил вовремя, попробуйте ещё раз") from exc
            except httpx.TransportError as exc:
                if last_try:
                    raise KeitaroError(f"Не удалось подключиться к Keitaro: {exc}") from exc
            else:
                if response.status_code not in RETRY_STATUSES or last_try:
                    return self._parse(response)
            time.sleep(self.backoff * attempt)
        raise AssertionError("unreachable")

    @staticmethod
    def _parse(response: httpx.Response) -> Any:
        if response.status_code >= 400:
            raise KeitaroError(_error_text(response), response.status_code)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise KeitaroError("Keitaro вернул ответ не в формате JSON") from exc

    # --- кампании ---

    def list_campaigns(self) -> list[KtCampaign]:
        return [KtCampaign.model_validate(c) for c in self._request("GET", "/campaigns")]

    def get_campaign(self, campaign_id: int) -> KtCampaign:
        return KtCampaign.model_validate(self._request("GET", f"/campaigns/{campaign_id}"))

    def create_campaign(self, payload: dict[str, Any]) -> KtCampaign:
        return KtCampaign.model_validate(self._request("POST", "/campaigns", json=payload))

    def delete_campaign(self, campaign_id: int) -> None:
        self._request("DELETE", f"/campaigns/{campaign_id}")

    # --- потоки ---

    def get_campaign_streams(self, campaign_id: int) -> list[KtStream]:
        data = self._request("GET", f"/campaigns/{campaign_id}/streams")
        return [KtStream.model_validate(s) for s in data]

    def get_stream(self, stream_id: int) -> KtStream:
        return KtStream.model_validate(self._request("GET", f"/streams/{stream_id}"))

    def create_stream(self, payload: dict[str, Any]) -> KtStream:
        return KtStream.model_validate(self._request("POST", "/streams", json=payload))

    def update_stream(self, stream_id: int, payload: dict[str, Any]) -> KtStream:
        return KtStream.model_validate(self._request("PUT", f"/streams/{stream_id}", json=payload))

    # --- справочники ---

    def list_offers(self) -> list[KtOffer]:
        return [KtOffer.model_validate(o) for o in self._request("GET", "/offers")]

    def list_domains(self) -> list[KtDomain]:
        return [KtDomain.model_validate(d) for d in self._request("GET", "/domains")]

    def list_groups(self, group_type: str = "campaigns") -> list[KtGroup]:
        data = self._request("GET", "/groups", params={"type": group_type})
        return [KtGroup.model_validate(g) for g in data]

    def list_traffic_sources(self) -> list[KtTrafficSource]:
        return [KtTrafficSource.model_validate(t) for t in self._request("GET", "/traffic_sources")]


def _error_text(response: httpx.Response) -> str:
    """Достаёт из ответа Keitaro понятный текст ошибки."""
    if response.status_code == 401:
        return "Keitaro отклонил API-ключ (401). Проверьте KEITARO_API_KEY"
    if response.status_code == 404:
        return "Объект не найден в Keitaro (404)"
    detail = ""
    try:
        body = response.json()
    except ValueError:
        detail = response.text.strip()[:300]
    else:
        if isinstance(body, dict):
            detail = str(body.get("error") or body.get("message") or body)
        else:
            detail = str(body)
    return f"Keitaro ответил ошибкой {response.status_code}: {detail[:300]}"
