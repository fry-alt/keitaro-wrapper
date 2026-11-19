"""Защита приложения, открытого в интернет.

- Вход по паролю (APP_PASSWORD) и подписанная cookie. Без пароля приложение
  открыто: так удобно локально, но на Vercel без пароля оно не запустится.
- CSRF: изменяющие запросы с чужого сайта отклоняются (Origin / Sec-Fetch-Site),
  cookie выдаётся с SameSite=Lax.
- Заголовки безопасности и CSP: скрипты и стили только со своего домена.
"""

import hashlib
import hmac
import os
import time
from urllib.parse import quote, urlsplit

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from app.config import Settings, get_settings

COOKIE = "kw_session"
SESSION_TTL = 7 * 24 * 3600
PUBLIC_PATHS = ("/login", "/static/", "/healthz")
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
    "form-action 'self'; object-src 'none'"
)
# Swagger UI грузит скрипты с CDN и использует inline-скрипт
DOCS_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: "
    "https://fastapi.tiangolo.com; frame-ancestors 'none'; object-src 'none'"
)


def _secret(settings: Settings) -> bytes:
    if settings.secret_key:
        return settings.secret_key.encode()
    # без SECRET_KEY ключ подписи выводится из пароля: смена пароля разлогинивает всех
    material = f"kw:{settings.app_password}:{settings.keitaro_api_key}"
    return hashlib.sha256(material.encode()).digest()


def make_token(settings: Settings, now: float | None = None) -> str:
    expires = int((time.time() if now is None else now) + SESSION_TTL)
    sig = hmac.new(_secret(settings), str(expires).encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def token_valid(settings: Settings, token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    expires, sig = token.split(".", 1)
    if not expires.isdigit() or int(expires) < (time.time() if now is None else now):
        return False
    expected = hmac.new(_secret(settings), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected)


def password_ok(settings: Settings, password: str) -> bool:
    return bool(settings.app_password) and hmac.compare_digest(
        password.encode(), settings.app_password.encode()
    )


def is_https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "")
    return request.url.scheme == "https" or forwarded.split(",")[0].strip() == "https"


def safe_next(target: str | None) -> str:
    """Куда вернуть после входа: только свой путь, без открытого редиректа."""
    if not target or not target.startswith("/") or target.startswith("//") or "\\" in target:
        return "/campaigns"
    return target


def is_cross_site(request: Request) -> bool:
    if request.headers.get("sec-fetch-site") == "cross-site":
        return True
    origin = request.headers.get("origin")
    if origin and origin != "null":
        return urlsplit(origin).netloc != request.headers.get("host", request.url.netloc)
    return origin == "null"


def authenticated(request: Request, settings: Settings) -> bool:
    if not settings.app_password:
        return True
    if token_valid(settings, request.cookies.get(COOKIE)):
        return True
    # JSON API можно вызывать скриптом: Authorization: Bearer <APP_PASSWORD>
    auth = request.headers.get("authorization", "")
    return auth.startswith("Bearer ") and password_ok(settings, auth[7:])


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        # тесты подменяют настройки через dependency_overrides — уважаем это и здесь
        provider = request.app.dependency_overrides.get(get_settings, get_settings)
        settings = provider()
        response = await self._guard(request, settings)
        if response is None:
            response = await call_next(request)
        self._headers(request, response)
        return response

    async def _guard(self, request: Request, settings: Settings) -> Response | None:
        path = request.url.path
        if request.method not in SAFE_METHODS and is_cross_site(request):
            return PlainTextResponse("Запрос с чужого сайта отклонён", status_code=403)
        if path.startswith(PUBLIC_PATHS):
            return None
        if os.environ.get("VERCEL") and not settings.app_password:
            return PlainTextResponse(
                "Приложение опубликовано без пароля. Задайте APP_PASSWORD в настройках Vercel",
                status_code=503,
            )
        if authenticated(request, settings):
            return None
        if path.startswith("/api/") or path in ("/docs", "/openapi.json", "/redoc"):
            return JSONResponse({"detail": "Нужен вход"}, status_code=401)
        login = "/login?next=" + quote(
            path + (f"?{request.url.query}" if request.url.query else "")
        )
        if request.headers.get("hx-request"):
            # HTMX сам перейдёт на страницу входа
            return Response(status_code=401, headers={"HX-Redirect": login})
        return RedirectResponse(login, status_code=303)

    @staticmethod
    def _headers(request: Request, response: Response) -> None:
        path = request.url.path
        docs = path in ("/docs", "/redoc") or path.startswith("/docs/")
        response.headers.setdefault("Content-Security-Policy", DOCS_CSP if docs else CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
        )
        if is_https(request):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        if not path.startswith("/static/"):
            # страницы содержат данные кампаний: не кэшировать в общих кэшах
            response.headers.setdefault("Cache-Control", "no-store")
