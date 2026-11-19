"""HTML-страницы и HTMX-действия редактора."""

import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_kt
from app.keitaro import KeitaroAPI, KeitaroError
from app.models import Campaign, Offer, Stream
from app.security import COOKIE, SESSION_TTL, is_https, make_token, password_ok, safe_next
from app.services import editor
from app.services.countries import countries
from app.services.creator import create_campaign
from app.services.errors import NotFoundError, UserError
from app.services.sync import fetch_campaign

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Kt = Annotated[KeitaroAPI, Depends(get_kt)]
Conf = Annotated[Settings, Depends(get_settings)]
Errors = (UserError, KeitaroError)


def render(request: Request, name: str, settings: Settings, status_code: int = 200, **context):
    return templates.TemplateResponse(
        request, name, {"settings": settings, **context}, status_code=status_code
    )


def see_other(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


@router.get("/", include_in_schema=False)
def index():
    return see_other("/campaigns")


# --- вход ---


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request, settings: Conf, next: str = "/campaigns"):
    if not settings.app_password:
        return see_other(safe_next(next))
    return render(request, "login.html", settings, next=safe_next(next))


@router.post("/login", include_in_schema=False)
def login_submit(
    request: Request,
    settings: Conf,
    password: Annotated[str, Form()] = "",
    next: Annotated[str, Form()] = "/campaigns",
):
    if not password_ok(settings, password):
        time.sleep(1)  # замедляем перебор паролей
        return render(
            request,
            "login.html",
            settings,
            status_code=401,
            next=safe_next(next),
            error="Неверный пароль",
        )
    response = see_other(safe_next(next))
    response.set_cookie(
        COOKIE,
        make_token(settings),
        max_age=SESSION_TTL,
        httponly=True,
        secure=is_https(request),
        samesite="lax",
    )
    return response


@router.post("/logout", include_in_schema=False)
def logout():
    response = see_other("/login")
    response.delete_cookie(COOKIE)
    return response


# --- список кампаний ---


def campaigns_context(db: Session, kt: KeitaroAPI, settings: Settings, q: str | None) -> dict:
    local = db.scalars(select(Campaign).order_by(Campaign.id.desc())).all()
    query = settings.campaign_prefix if q is None else q.strip()
    remote, error = [], None
    try:
        remote = sorted(
            (c for c in kt.list_campaigns() if query.lower() in c.name.lower()),
            key=lambda c: c.id,
            reverse=True,
        )
    except KeitaroError as exc:
        error = exc.message
    return {
        "local": local,
        "remote": remote[:100],
        "remote_total": len(remote),
        "known": {c.kt_id: c.id for c in local},
        "q": query,
        "error": error,
    }


@router.get("/campaigns", response_class=HTMLResponse)
def campaigns_page(request: Request, db: Db, kt: Kt, settings: Conf, q: str | None = None):
    return render(request, "campaigns.html", settings, **campaigns_context(db, kt, settings, q))


@router.post("/campaigns/import")
def import_campaign(
    request: Request, db: Db, kt: Kt, settings: Conf, kt_id: Annotated[int, Form()]
):
    try:
        campaign, _ = fetch_campaign(db, kt, kt_id)
    except Errors as exc:
        context = campaigns_context(db, kt, settings, None) | {"error": exc.message}
        return render(request, "campaigns.html", settings, status_code=422, **context)
    return see_other(f"/campaigns/{campaign.id}")


# --- создание ---


@router.get("/campaigns/new", response_class=HTMLResponse)
def new_campaign_page(request: Request, settings: Conf):
    return render(request, "new_campaign.html", settings, countries=countries(), form={})


@router.post("/campaigns/new")
def create_campaign_submit(
    request: Request,
    db: Db,
    kt: Kt,
    settings: Conf,
    name: Annotated[str, Form()] = "",
    country: Annotated[list[str] | None, Form()] = None,
    offer_id: Annotated[str, Form()] = "",
):
    try:
        if not offer_id.strip().isdigit():
            raise UserError("Выберите оффер")
        campaign = create_campaign(
            db, kt, settings, name=name, countries=country or [], offer_id=int(offer_id)
        )
    except Errors as exc:
        offer = db.get(Offer, int(offer_id)) if offer_id.strip().isdigit() else None
        form = {"name": name, "countries": country or [], "offer": offer}
        return render(
            request,
            "new_campaign.html",
            settings,
            status_code=422,
            countries=countries(),
            form=form,
            error=exc.message,
        )
    return see_other(f"/campaigns/{campaign.id}?created=1")


# --- редактор ---


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_page(request: Request, db: Db, settings: Conf, campaign_id: int, created: int = 0):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return render(request, "not_found.html", settings, status_code=404)
    return render(request, "campaign.html", settings, campaign=campaign, created=bool(created))


@router.post("/campaigns/{campaign_id}/fetch", response_class=HTMLResponse)
def fetch_streams(request: Request, db: Db, kt: Kt, settings: Conf, campaign_id: int):
    campaign = db.get(Campaign, campaign_id)
    if campaign is None:
        return HTMLResponse("Кампания не найдена", status_code=404)
    notice = error = None
    try:
        campaign, report = fetch_campaign(db, kt, campaign.kt_id)
        notice = report.summary()
    except Errors as exc:
        db.rollback()
        error = exc.message
    return render(request, "_streams.html", settings, campaign=campaign, notice=notice, error=error)


def stream_action(
    request: Request,
    db: Session,
    settings: Settings,
    stream_id: int,
    action: Callable[[Stream], str | None],
) -> Response:
    """Выполняет действие над потоком и возвращает перерисованный блок потока."""
    try:
        stream = editor.get_stream(db, stream_id)
    except NotFoundError as exc:
        return HTMLResponse(exc.message, status_code=404)
    notice = error = None
    try:
        if not settings.can_edit(stream.campaign.name):
            raise UserError(
                f"Кампания без префикса {settings.campaign_prefix}: "
                "её можно смотреть, но не править"
            )
        notice = action(stream)
    except Errors as exc:
        db.rollback()
        error = exc.message
    return render(request, "_stream.html", settings, stream=stream, notice=notice, error=error)


def parse_share(raw: str) -> int | None:
    raw = raw.strip().rstrip("%")
    if not raw:
        return None
    if not raw.isdigit() or int(raw) > 100:
        raise UserError("Доля — целое число от 0 до 100")
    return int(raw)


@router.post("/streams/{stream_id}/offers", response_class=HTMLResponse)
def add_offer(
    request: Request,
    db: Db,
    kt: Kt,
    settings: Conf,
    stream_id: int,
    offer_id: Annotated[str, Form()] = "",
):
    def action(stream: Stream) -> None:
        if not offer_id.strip().isdigit():
            raise UserError("Выберите оффер из списка")
        editor.add_offer(db, kt, stream, int(offer_id))

    return stream_action(request, db, settings, stream_id, action)


@router.post("/streams/{stream_id}/offers/{row_id}/remove", response_class=HTMLResponse)
def remove_offer(request: Request, db: Db, settings: Conf, stream_id: int, row_id: int):
    return stream_action(
        request, db, settings, stream_id, lambda s: editor.remove_offer(db, s, row_id)
    )


@router.post("/streams/{stream_id}/offers/{row_id}/restore", response_class=HTMLResponse)
def restore_offer(request: Request, db: Db, settings: Conf, stream_id: int, row_id: int):
    return stream_action(
        request, db, settings, stream_id, lambda s: editor.bring_back(db, s, row_id)
    )


@router.post("/streams/{stream_id}/offers/{row_id}/pin", response_class=HTMLResponse)
def pin_offer(
    request: Request,
    db: Db,
    settings: Conf,
    stream_id: int,
    row_id: int,
    share: Annotated[str, Form()] = "",
):
    return stream_action(
        request,
        db,
        settings,
        stream_id,
        lambda s: editor.set_pin(db, s, row_id, pinned=True, share=parse_share(share)),
    )


@router.post("/streams/{stream_id}/offers/{row_id}/unpin", response_class=HTMLResponse)
def unpin_offer(request: Request, db: Db, settings: Conf, stream_id: int, row_id: int):
    return stream_action(
        request, db, settings, stream_id, lambda s: editor.set_pin(db, s, row_id, pinned=False)
    )


@router.post("/streams/{stream_id}/push", response_class=HTMLResponse)
def push_stream(request: Request, db: Db, kt: Kt, settings: Conf, stream_id: int):
    def action(stream: Stream) -> str:
        editor.push(db, kt, stream)
        return "Опубликовано в Keitaro"

    return stream_action(request, db, settings, stream_id, action)


@router.post("/streams/{stream_id}/cancel", response_class=HTMLResponse)
def cancel_stream(request: Request, db: Db, settings: Conf, stream_id: int):
    def action(stream: Stream) -> str:
        editor.cancel(db, stream)
        return "Неопубликованные изменения отменены"

    return stream_action(request, db, settings, stream_id, action)
