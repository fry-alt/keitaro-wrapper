"""Точка входа для Vercel: все запросы переписываются сюда (см. vercel.json)."""

from app.main import app

__all__ = ["app"]
