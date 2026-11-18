"""Справочник стран ISO 3166-1 (alpha-2) с русскими названиями."""

import gettext
import re
from functools import lru_cache

import pycountry

from app.services.errors import UserError


@lru_cache
def countries() -> dict[str, str]:
    """Код -> русское название, отсортировано по названию."""
    ru = gettext.translation("iso3166-1", pycountry.LOCALES_DIR, languages=["ru"], fallback=True)
    pairs = {c.alpha_2: ru.gettext(c.name) for c in pycountry.countries}
    return dict(sorted(pairs.items(), key=lambda item: item[1]))


def parse_countries(raw: str | list[str]) -> list[str]:
    """'mx, AU RO' -> ['MX', 'AU', 'RO']. Без дублей, порядок сохраняется."""
    parts = raw if isinstance(raw, list) else re.split(r"[\s,;]+", raw)
    codes = list(dict.fromkeys(p.strip().upper() for p in parts if p and p.strip()))
    if not codes:
        raise UserError("Укажите хотя бы одну страну")
    unknown = [c for c in codes if c not in countries()]
    if unknown:
        raise UserError(f"Неизвестный код страны: {', '.join(unknown)}. Нужен ISO-код, например AU")
    return codes
