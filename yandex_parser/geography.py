"""Справочник географии Яндекса и построение URL.

Яндекс.Карты используют URL вида:
    https://yandex.ru/maps/<geo_id>/<slug>/?text=<запрос>

где ``geo_id`` — числовой код из единого справочника Яндекса (он же ``lr``
в поиске). Иерархия: страна → федеральный округ → субъект РФ
(область/край/республика) → город → район.

Важное различие:
  * ``1`` / ``moscow-and-moscow-oblast`` — Москва + МО (субъект)
  * ``213`` / ``moscow`` — только город Москва
  * ``10174`` / ``saint-petersburg-and-leningrad-oblast`` — СПб + ЛО
  * ``2`` / ``saint-petersburg`` — только СПб

При поиске с ``<geo_id>/<slug>/`` Яндекс ограничивает выдачу границами
указанного объекта (город не «залезает» в область и наоборот).

Модуль содержит только проверенные записи (встреченные в официальных
URL Яндекс.Карт). Остальные города поиск найдёт по текстовому запросу.
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
# Справочник (только проверенные записи с yandex.com/maps)
# ---------------------------------------------------------------------------

# Страны
RUSSIA = GeoEntry(225, "russia", "Россия", "country")

# Субъекты РФ (область + её центр)
MOSCOW_OBLAST = GeoEntry(1, "moscow-and-moscow-oblast",
                         "Москва и Московская область", "region")
SPB_OBLAST = GeoEntry(10174, "saint-petersburg-and-leningrad-oblast",
                      "Санкт-Петербург и Ленинградская область", "region")

# Города
MOSCOW = GeoEntry(213, "moscow", "Москва", "city")
SPB = GeoEntry(2, "saint-petersburg", "Санкт-Петербург", "city")


# Ключи — приводятся к lower()/замене ё→е, поэтому «Москва», «МОСКВА», «москва» — одно и то же.
GEO_ENTRIES: dict[str, GeoEntry] = {
    # Россия
    "россия": RUSSIA,
    "russia": RUSSIA,
    "рф": RUSSIA,
    # Москва (город)
    "москва": MOSCOW,
    "moscow": MOSCOW,
    "мск": MOSCOW,
    # Москва + область (субъект)
    "москва и область": MOSCOW_OBLAST,
    "москва и московская область": MOSCOW_OBLAST,
    "московская область": MOSCOW_OBLAST,
    "подмосковье": MOSCOW_OBLAST,
    # Санкт-Петербург (город)
    "санкт-петербург": SPB,
    "санкт петербург": SPB,
    "saint-petersburg": SPB,
    "saint petersburg": SPB,
    "спб": SPB,
    "питер": SPB,
    # СПб + область (субъект)
    "санкт-петербург и область": SPB_OBLAST,
    "санкт-петербург и ленинградская область": SPB_OBLAST,
    "ленинградская область": SPB_OBLAST,
    "ло": SPB_OBLAST,
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
    """Уникальные записи-регионы (субъекты РФ)."""
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
    """Собрать URL поиска Яндекс.Карт.

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
        ``https://yandex.ru/maps/<geo_id>/<slug>/?text=...`` либо
        ``https://yandex.ru/maps/?text=...`` если регион не разрешён.

    Examples
    --------
    >>> build_search_url("кафе", "Москва")
    'https://yandex.ru/maps/213/moscow/?text=%D0%BA%D0%B0%D1%84%D0%B5'
    >>> build_search_url("аптека", "Московская область")
    'https://yandex.ru/maps/1/moscow-and-moscow-oblast/?text=%D0%B0%D0%BF%D1%82%D0%B5%D0%BA%D0%B0'
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
        return f"https://yandex.ru/maps/{entry.geo_id}/{entry.slug}/?{qs}"
    return f"https://yandex.ru/maps/?{qs}"


__all__ = [
    "GeoEntry",
    "GEO_ENTRIES",
    "RUSSIA",
    "MOSCOW",
    "MOSCOW_OBLAST",
    "SPB",
    "SPB_OBLAST",
    "resolve",
    "list_regions",
    "list_cities",
    "build_search_url",
]
