"""Справочник географии Яндекса и построение URL (Казахстан).

Яндекс.Карты используют URL вида:
    https://yandex.kz/maps/<geo_id>/<slug>/?text=<запрос>

где ``geo_id`` — числовой код из единого справочника Яндекса (он же ``lr``
в поиске). Иерархия: страна → область → город.

Если при поиске открыта страница ``/maps/<geo_id>/<slug>/``, Яндекс
ограничивает выдачу границами указанного объекта.

Модуль содержит только проверенные записи (встреченные в официальных
URL Яндекс.Карт). Остальные города Казахстана (Шымкент, Караганда,
Актобе, Павлодар, Тараз, Костанай и т.д.) при необходимости ищутся
по тексту запроса.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GeoEntry:
    """Запись справочника географии."""

    geo_id: int
    slug: str
    name: str
    kind: str  # "country" | "region" | "city"


# ---------------------------------------------------------------------------
# Справочник (только проверенные записи с yandex.com/maps, домен .kz)
# ---------------------------------------------------------------------------

KAZAKHSTAN = GeoEntry(159, "kazakhstan", "Қазақстан / Казахстан", "country")
ALMATY = GeoEntry(162, "almaty", "Алматы", "city")
ASTANA = GeoEntry(163, "astana", "Астана", "city")


# Ключи нормализуются (lower/strip/ё→е), поэтому регистр и пробелы не важны.
GEO_ENTRIES: dict[str, GeoEntry] = {
    # Казахстан (страна)
    "казахстан": KAZAKHSTAN,
    "қазақстан": KAZAKHSTAN,
    "kazakhstan": KAZAKHSTAN,
    "qazaqstan": KAZAKHSTAN,
    "кз": KAZAKHSTAN,
    "kz": KAZAKHSTAN,
    # Алматы (город)
    "алматы": ALMATY,
    "алма-ата": ALMATY,
    "алма ата": ALMATY,
    "almaty": ALMATY,
    "alma-ata": ALMATY,
    # Астана (город, бывш. Нур-Султан/Целиноград)
    "астана": ASTANA,
    "astana": ASTANA,
    "нур-султан": ASTANA,
    "нур султан": ASTANA,
    "nur-sultan": ASTANA,
    "nur sultan": ASTANA,
}


# ---------------------------------------------------------------------------
# Публичный API
# ---------------------------------------------------------------------------


def _norm(s: str) -> str:
    """Нормализация ключа для поиска по справочнику."""
    return s.strip().lower().replace("ё", "е")


def resolve(name: str) -> Optional[GeoEntry]:
    """Найти GeoEntry по человеческому названию. None если не найдено."""
    if not name:
        return None
    return GEO_ENTRIES.get(_norm(name))


def list_regions() -> list[GeoEntry]:
    """Уникальные записи-области. Пока пусто (в справочнике только города+страна)."""
    seen: set[int] = set()
    out: list[GeoEntry] = []
    for e in GEO_ENTRIES.values():
        if e.kind == "region" and e.geo_id not in seen:
            seen.add(e.geo_id)
            out.append(e)
    return out


def list_cities() -> list[GeoEntry]:
    """Уникальные записи-города."""
    seen: set[int] = set()
    out: list[GeoEntry] = []
    for e in GEO_ENTRIES.values():
        if e.kind == "city" and e.geo_id not in seen:
            seen.add(e.geo_id)
            out.append(e)
    return out


def build_search_url(
    query: str,
    place: str | GeoEntry | None = None,
    *,
    ll: tuple[float, float] | None = None,
    z: int | None = None,
    spn: tuple[float, float] | None = None,
) -> str:
    """Собрать URL поиска Яндекс.Карт (домен .kz).

    Parameters
    ----------
    query : str
        Текст запроса (категория/название).
    place : str | GeoEntry | None
        Название города/региона или готовая GeoEntry. Если строка не
        разрешена по справочнику, будет использован URL без geo_id
        (город просто будет передан как часть текстового запроса).
    ll : (lon, lat), optional
        Центр карты. Долгота первая!
    z : int, optional
        Уровень зума 0..21.
    spn : (dlon, dlat), optional
        Размер видимой области в градусах.

    Returns
    -------
    str
        Полный URL вида
        ``https://yandex.kz/maps/<geo_id>/<slug>/?text=...`` либо
        ``https://yandex.kz/maps/?text=...`` если регион не разрешён.

    Examples
    --------
    >>> build_search_url("кафе", "Алматы")
    'https://yandex.kz/maps/162/almaty/?text=%D0%BA%D0%B0%D1%84%D0%B5'
    >>> build_search_url("аптека", "Астана")
    'https://yandex.kz/maps/163/astana/?text=%D0%B0%D0%BF%D1%82%D0%B5%D0%BA%D0%B0'
    """
    entry: Optional[GeoEntry]
    text = query
    if isinstance(place, GeoEntry):
        entry = place
    elif isinstance(place, str) and place.strip():
        entry = resolve(place)
        if entry is None:
            # Не нашли в справочнике — добавим город в текст запроса.
            text = f"{place} {query}".strip()
    else:
        entry = None

    params: list[tuple[str, str]] = [("text", text)]
    if ll is not None:
        lon, lat = ll
        params.append(("ll", f"{lon},{lat}"))
    if z is not None:
        if not 0 <= z <= 21:
            raise ValueError(f"z must be in [0, 21], got {z}")
        params.append(("z", str(z)))
    if spn is not None:
        dlon, dlat = spn
        params.append(("spn", f"{dlon},{dlat}"))

    qs = urllib.parse.urlencode(params)
    if entry is not None:
        return f"https://yandex.kz/maps/{entry.geo_id}/{entry.slug}/?{qs}"
    return f"https://yandex.kz/maps/?{qs}"


__all__ = [
    "GeoEntry",
    "GEO_ENTRIES",
    "KAZAKHSTAN",
    "ALMATY",
    "ASTANA",
    "resolve",
    "list_regions",
    "list_cities",
    "build_search_url",
]
