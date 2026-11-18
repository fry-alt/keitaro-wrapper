from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import api, web

app = FastAPI(
    title="Keitaro wrapper",
    description="Создание кампаний и редактор офферов в потоках Keitaro",
    version="0.1.0",
)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(web.router)
app.include_router(api.router)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    return {"status": "ok"}
