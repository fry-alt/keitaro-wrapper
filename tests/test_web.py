"""Проверки через HTTP: те же запросы, что шлёт интерфейс."""

import re

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.db import get_db
from app.deps import get_kt
from app.main import app


@pytest.fixture
def client(session_factory, kt, settings):
    def override_db():
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_kt] = lambda: kt
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def opened(client, kt):
    """Кампания из Keitaro, открытая в обёртке. Возвращает (url редактора, id офферного потока)."""
    kt_campaign = kt.add_campaign("test_ivanov_web", [(3749, 50), (3717, 50)])
    response = client.post(
        "/campaigns/import", data={"kt_id": kt_campaign.id}, follow_redirects=False
    )
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    stream_ids = re.findall(r'id="stream-(\d+)"', page)
    return response.headers["location"], int(stream_ids[1]), kt_campaign


def test_campaign_list_shows_keitaro_campaigns(client, kt):
    kt.add_campaign("test_ivanov_listed", [(3749, 100)])
    kt.add_campaign("someone_else", [(3749, 100)])
    page = client.get("/campaigns").text
    assert "test_ivanov_listed" in page
    assert "someone_else" not in page  # по умолчанию фильтр по нашему префиксу


def test_editor_page(client, opened):
    page = client.get(opened[0]).text
    assert "Flow 2" in page and "Miaflow" in page
    assert "Поток без офферов" in page  # Flow 1


def test_add_push_flow(client, kt, opened):
    _, stream_id, kt_campaign = opened
    html = client.post(f"/streams/{stream_id}/offers", data={"offer_id": "11112"}).text
    assert "не опубликовано" in html
    assert html.count(">34%<") == 1 and html.count(">33%<") == 2

    html = client.post(f"/streams/{stream_id}/push").text
    assert "Опубликовано в Keitaro" in html and "не опубликовано" not in html
    assert sorted(o.share for o in kt.offer_stream(kt_campaign.id).offers) == [33, 33, 34]


def test_errors_are_shown_inside_stream(client, opened):
    _, stream_id, _ = opened
    html = client.post(f"/streams/{stream_id}/offers", data={"offer_id": "3749"}).text
    assert "уже есть" in html
    html = client.post(f"/streams/{stream_id}/offers", data={"offer_id": ""}).text
    assert "Выберите оффер" in html


def test_pin_with_bad_share(client, opened):
    _, stream_id, _ = opened
    page = client.get(opened[0]).text
    row_id = re.search(rf"/streams/{stream_id}/offers/(\d+)/pin", page).group(1)
    html = client.post(f"/streams/{stream_id}/offers/{row_id}/pin", data={"share": "abc"}).text
    assert "целое число" in html
    html = client.post(f"/streams/{stream_id}/offers/{row_id}/pin", data={"share": "70"}).text
    assert ">70%<" in html and ">30%<" in html


def test_fetch_reports_summary(client, opened):
    url, _, _ = opened
    html = client.post(url + "/fetch").text
    assert "Синхронизировано с Keitaro" in html


def test_create_campaign_form(client, kt):
    response = client.post(
        "/campaigns/new",
        data={"name": "from_form", "country": ["AU", "MX"], "offer_id": "3749"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get(response.headers["location"]).text
    assert "Кампания создана" in page and "test_ivanov_from_form" in page


def test_create_campaign_form_error_keeps_values(client):
    response = client.post("/campaigns/new", data={"name": "x", "country": ["AU"], "offer_id": ""})
    assert response.status_code == 422
    assert "Выберите оффер" in response.text
    assert 'value="AU" selected' in response.text


def test_api_offers(client):
    data = client.get("/api/offers", params={"q": "miaflow"}).json()
    assert [o["id"] for o in data] == [3749, 3717]
    assert data[0]["label"].startswith("[3749]")


def test_api_offers_by_id(client):
    # ID, которого нет в каталоге, всё равно можно выбрать: проверка будет при добавлении
    data = client.get("/api/offers", params={"q": "18814"}).json()
    assert data[0]["id"] == 18814 and "по ID" in data[0]["label"]
    assert client.get("/api/offers", params={"q": "3749"}).json()[0]["label"].startswith("[3749] M")


def test_api_create_campaign(client):
    response = client.post(
        "/api/campaigns", json={"name": "api1", "countries": ["AU"], "offer_id": 3749}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "test_ivanov_api1"
    assert [s["has_offers"] for s in body["streams"]] == [False, True]

    bad = client.post("/api/campaigns", json={"name": "x", "countries": ["ZZ"], "offer_id": 3749})
    assert bad.status_code == 422 and "ZZ" in bad.json()["detail"]


def test_unknown_campaign_page(client):
    assert client.get("/campaigns/999").status_code == 404
