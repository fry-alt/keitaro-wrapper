"""Защита приложения: вход, CSRF, заголовки, запрет правок чужих кампаний."""

import re

import pytest

from app.security import COOKIE, make_token, safe_next, token_valid

PASSWORD = "correct horse battery staple"


@pytest.fixture
def locked(client, settings):
    settings.app_password = PASSWORD
    return client


def login(client, password=PASSWORD, next_="/campaigns"):
    return client.post("/login", data={"password": password, "next": next_}, follow_redirects=False)


def test_open_without_password(client):
    assert client.get("/campaigns").status_code == 200


def test_pages_require_login(locked):
    response = locked.get("/campaigns/new?x=1", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/campaigns/new%3Fx%3D1"
    assert locked.get("/api/offers").status_code == 401
    assert locked.get("/openapi.json").status_code == 401
    assert locked.post("/streams/1/push", follow_redirects=False).status_code == 303


def test_htmx_is_redirected_to_login(locked):
    response = locked.post("/streams/1/push", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["HX-Redirect"].startswith("/login")


def test_public_paths(locked):
    assert locked.get("/healthz").status_code == 200
    assert locked.get("/static/app.css").status_code == 200
    assert locked.get("/login").status_code == 200


def test_wrong_password(locked, monkeypatch):
    monkeypatch.setattr("app.web.time.sleep", lambda _: None)
    response = login(locked, "nope")
    assert response.status_code == 401 and "Неверный пароль" in response.text
    assert COOKIE not in response.cookies


def test_login_sets_safe_cookie_and_opens_app(locked):
    response = login(locked, next_="/campaigns/new")
    assert response.status_code == 303 and response.headers["location"] == "/campaigns/new"
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert locked.get("/campaigns").status_code == 200

    locked.post("/logout")
    assert locked.get("/campaigns", follow_redirects=False).status_code == 303


def test_secure_cookie_behind_https_proxy(locked):
    response = locked.post(
        "/login",
        data={"password": PASSWORD},
        headers={"X-Forwarded-Proto": "https"},
        follow_redirects=False,
    )
    assert "secure" in response.headers["set-cookie"].lower()


def test_forged_and_expired_tokens(settings):
    settings.app_password = PASSWORD
    token = make_token(settings)
    assert token_valid(settings, token)
    expires, sig = token.split(".")
    assert not token_valid(settings, f"{int(expires) + 1000}.{sig}")  # подделка срока
    assert not token_valid(settings, make_token(settings, now=0))  # истёк
    assert not token_valid(settings, "garbage")
    settings.app_password = "changed"
    assert not token_valid(settings, token)  # смена пароля разлогинивает


def test_bearer_token_for_api(locked):
    headers = {"Authorization": f"Bearer {PASSWORD}"}
    assert locked.get("/api/offers", headers=headers).status_code == 200
    assert locked.get("/api/offers", headers={"Authorization": "Bearer x"}).status_code == 401


@pytest.mark.parametrize(
    "target", ["//evil.com", "https://evil.com", "/\\evil.com", "", None, "javascript:x"]
)
def test_no_open_redirect(target):
    assert safe_next(target) == "/campaigns"
    assert safe_next("/campaigns/5?created=1") == "/campaigns/5?created=1"


def test_cross_site_post_is_rejected(client):
    evil = client.post("/campaigns/new", headers={"Origin": "https://evil.com"})
    assert evil.status_code == 403
    sec = client.post("/campaigns/new", headers={"Sec-Fetch-Site": "cross-site"})
    assert sec.status_code == 403
    same = client.post("/campaigns/new", headers={"Origin": "http://testserver"})
    assert same.status_code == 422  # дошло до формы: ошибка валидации, а не 403


def test_security_headers(client):
    headers = client.get("/campaigns").headers
    assert "script-src 'self'" in headers["content-security-policy"]
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["x-frame-options"] == "DENY"
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["cache-control"] == "no-store"


def test_vercel_refuses_to_run_without_password(client, monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    assert client.get("/campaigns").status_code == 503
    assert client.get("/healthz").status_code == 200


def test_foreign_campaign_is_read_only(client, kt):
    kt_campaign = kt.add_campaign("someone_else_campaign", [(3749, 50), (3717, 50)])
    location = client.post(
        "/campaigns/import", data={"kt_id": kt_campaign.id}, follow_redirects=False
    ).headers["location"]
    page = client.get(location).text
    assert "можно смотреть" in page and "Push to KT" not in page and "Remove" not in page

    stream_id = int(re.findall(r'id="stream-(\d+)"', page)[1])
    html = client.post(f"/streams/{stream_id}/offers", data={"offer_id": "11112"}).text
    assert "не править" in html
    assert {o.offer_id for o in kt.offer_stream(kt_campaign.id).offers} == {3749, 3717}


def test_offer_names_are_escaped(client, kt):
    kt.add_offer(66666, "<script>alert(1)</script>")
    kt_campaign = kt.add_campaign("test_ivanov_xss", [(66666, 100)])
    location = client.post(
        "/campaigns/import", data={"kt_id": kt_campaign.id}, follow_redirects=False
    ).headers["location"]
    page = client.get(location).text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_deep_health_reports_only_status(client, monkeypatch):
    import app.main as main

    main._deep_cache.clear()
    monkeypatch.setattr(main, "_check_db", lambda: "ok")
    monkeypatch.setattr(main, "_check_keitaro", lambda s: "fail")
    response = client.get("/healthz", params={"deep": 1})
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "ok", "keitaro": "fail"}
    monkeypatch.setattr(main, "_check_keitaro", lambda s: "ok")
    assert client.get("/healthz", params={"deep": 1}).status_code == 503  # кэш на минуту
    main._deep_cache.clear()
