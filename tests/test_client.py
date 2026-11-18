import httpx
import pytest

from app.keitaro import KeitaroClient, KeitaroError


def make_client(handler, **kwargs) -> KeitaroClient:
    return KeitaroClient(
        "https://kt.example/", "secret", backoff=0, transport=httpx.MockTransport(handler), **kwargs
    )


def test_sends_api_key_and_parses_models():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["key"] = request.headers["Api-Key"]
        return httpx.Response(
            200,
            json=[
                {
                    "id": 5,
                    "campaign_id": 1,
                    "name": "Flow",
                    "schema": "landings",
                    "offers": [{"id": 1, "offer_id": 10, "share": 100, "state": "active"}],
                    "unknown_field": "ok",
                }
            ],
        )

    streams = make_client(handler).get_campaign_streams(1)
    assert seen == {"url": "https://kt.example/admin_api/v1/campaigns/1/streams", "key": "secret"}
    assert streams[0].schema_ == "landings"
    assert streams[0].offers[0].offer_id == 10


def test_retries_get_on_gateway_errors():
    statuses = iter([503, 502, 200])

    def handler(request):
        return httpx.Response(next(statuses), json=[])

    assert make_client(handler).list_offers() == []


def test_does_not_retry_post():
    calls = []

    def handler(request):
        calls.append(request.method)
        return httpx.Response(503, text="busy")

    with pytest.raises(KeitaroError, match="503"):
        make_client(handler).create_campaign({"name": "x", "alias": "y"})
    assert calls == ["POST"]  # повтор мог бы создать вторую кампанию


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(401, json={"error": "Unauthorized"}), "API-ключ"),
        (httpx.Response(404), "не найден"),
        (httpx.Response(422, json={"error": "Alias is taken"}), "Alias is taken"),
        (httpx.Response(500, text="<html>oops</html>"), "oops"),
    ],
)
def test_error_messages(response, message):
    with pytest.raises(KeitaroError, match=message) as exc:
        make_client(lambda r: response).get_campaign(1)
    assert exc.value.status_code == response.status_code


def test_network_error_after_retries():
    def handler(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(KeitaroError, match="подключиться"):
        make_client(handler, retries=1).list_domains()
