import time
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app import api, web
from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.keitaro import KeitaroClient, KeitaroError
from app.security import SecurityMiddleware

app = FastAPI(
    title="Keitaro wrapper",
    description="Создание кампаний и редактор офферов в потоках Keitaro",
    version="0.1.0",
)
app.add_middleware(SecurityMiddleware)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(web.router)
app.include_router(api.router)


_deep_cache: dict[str, object] = {}


@app.get("/healthz", include_in_schema=False)
def healthz(
    response: Response,
    settings: Annotated[Settings, Depends(get_settings)],
    deep: bool = False,
) -> dict[str, str]:
    """Проверка живости. С deep=1 — ещё база и Keitaro (только ok/fail, без данных).

    Эндпоинт публичный, поэтому глубокая проверка кэшируется на минуту:
    из него нельзя сделать генератор запросов к Keitaro.
    """
    if not deep:
        return {"status": "ok"}
    cached = _deep_cache.get("result")
    if cached and time.monotonic() - _deep_cache["at"] < 60:  # type: ignore[operator]
        result = cached
    else:
        result = {"status": "ok", "db": _check_db(), "keitaro": _check_keitaro(settings)}
        if "fail" in result.values():
            result["status"] = "degraded"
        _deep_cache.update(result=result, at=time.monotonic())
    if result["status"] != "ok":  # type: ignore[index]
        response.status_code = 503
    return result  # type: ignore[return-value]


def _check_db() -> str:
    try:
        with get_sessionmaker()() as session:
            session.execute(text("select 1"))
        return "ok"
    except Exception:
        return "fail"


def _check_keitaro(settings: Settings) -> str:
    if settings.keitaro_fake:
        return "demo"
    client = KeitaroClient(settings.keitaro_url, settings.keitaro_api_key, timeout=10, retries=0)
    try:
        client.list_domains()
        return "ok"
    except KeitaroError:
        return "fail"
    finally:
        client.close()
