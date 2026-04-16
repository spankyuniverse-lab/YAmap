"""Справочник географии Яндекса и построение URL (Казахстан).

Яндекс.Карты используют два вида URL регионов:

* «classic» — короткий числовой geo_id:
    ``https://yandex.kz/maps/<geo_id>/<slug>/?text=<запрос>``
* «geo» — большой внутренний id:
    ``https://yandex.kz/maps/geo/<slug>/<big_geo_id>/?text=<запрос>``

Для большинства городов Казахстана работает classic-форма (Алматы=162,
Астана=163, Караганда=164, Актобе=20273, ...). Для Шымкента, Кызылорды,
Туркестана и Талдыкоргана Яндекс использует только geo-форму.

Если при поиске открыта страница региона, выдача не выходит за его
границы — этим мы и пользуемся, чтобы ``--region Алматы`` действительно
означал «только Алматы».

Справочник покрывает 19 крупнейших городов Казахстана + саму страну.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class GeoEntry:
    """Запись справочника географии.

    ``url_style``:
        ``"classic"`` — ``/maps/<geo_id>/<slug>/``
        ``"geo"`` — ``/maps/geo/<slug>/<geo_id>/``
    """

    geo_id: int
    slug: str
    name: str
    kind: str  # "country" | "region" | "city"
    url_style: str = "classic"  # "classic" | "geo"


# ---------------------------------------------------------------------------
# Справочник: страна + 19 крупнейших городов Казахстана
# ---------------------------------------------------------------------------

KAZAKHSTAN = GeoEntry(159, "kazakhstan", "Қазақстан / Казахстан", "country")

# --- classic URL: /maps/<geo_id>/<slug>/ ---
ALMATY = GeoEntry(162, "almaty", "Алматы", "city")
ASTANA = GeoEntry(163, "astana", "Астана", "city")
KARAGANDA = GeoEntry(164, "karaganda", "Караганда", "city")
SEMEY = GeoEntry(165, "semey", "Семей", "city")
PAVLODAR = GeoEntry(190, "pavlodar", "Павлодар", "city")
ATYRAU = GeoEntry(10291, "atyrau", "Атырау", "city")
KOSTANAY = GeoEntry(10295, "kostanai", "Костанай", "city")
PETROPAVLOVSK = GeoEntry(10298, "petropavlovsk", "Петропавловск", "city")
URALSK = GeoEntry(10305, "uralsk", "Уральск", "city")
OSKEMEN = GeoEntry(10306, "ust-kamenogorsk", "Усть-Каменогорск", "city")
AKTOBE = GeoEntry(20273, "aktobe", "Актобе", "city")
KOKSHETAU = GeoEntry(20809, "kokshetau", "Кокшетау", "city")
TARAZ = GeoEntry(21094, "taraz", "Тараз", "city")
AKTAU = GeoEntry(29575, "aktau", "Актау", "city")
TEMIRTAU = GeoEntry(35393, "temirtau", "Темиртау", "city")

# --- geo URL: /maps/geo/<slug>/<big_id>/ ---
SHYMKENT = GeoEntry(1842519191, "shymkent", "Шымкент", "city", url_style="geo")
KYZYLORDA = GeoEntry(53168216, "qyzylorda", "Кызылорда", "city", url_style="geo")
TURKISTAN = GeoEntry(53168220, "turkistan", "Туркестан", "city", url_style="geo")
TALDYKORGAN = GeoEntry(
    53168289, "taldyqorghan", "Талдыкорган", "city", url_style="geo"
)


# Ключи нормализуются (lower/strip/ё→е), поэтому регистр и пробелы не важны.
GEO_ENTRIES: dict[str, GeoEntry] = {
    # --- Страна ---
    "казахстан": KAZAKHSTAN,
    "қазақстан": KAZAKHSTAN,
    "kazakhstan": KAZAKHSTAN,
    "qazaqstan": KAZAKHSTAN,
    "кз": KAZAKHSTAN,
    "kz": KAZAKHSTAN,
    # --- Алматы ---
    "алматы": ALMATY,
    "алма-ата": ALMATY,
    "алма ата": ALMATY,
    "almaty": ALMATY,
    "alma-ata": ALMATY,
    # --- Астана (бывш. Нур-Султан / Целиноград / Акмола) ---
    "астана": ASTANA,
    "astana": ASTANA,
    "нур-султан": ASTANA,
    "нур султан": ASTANA,
    "nur-sultan": ASTANA,
    "nur sultan": ASTANA,
    # --- Шымкент ---
    "шымкент": SHYMKENT,
    "чимкент": SHYMKENT,
    "shymkent": SHYMKENT,
    "chimkent": SHYMKENT,
    # --- Караганда ---
    "караганда": KARAGANDA,
    "қарағанды": KARAGANDA,
    "karaganda": KARAGANDA,
    "qaraghandy": KARAGANDA,
    # --- Актобе ---
    "актобе": AKTOBE,
    "ақтөбе": AKTOBE,
    "актюбинск": AKTOBE,
    "aktobe": AKTOBE,
    "aqtobe": AKTOBE,
    # --- Тараз ---
    "тараз": TARAZ,
    "джамбул": TARAZ,
    "жамбыл": TARAZ,
    "taraz": TARAZ,
    "jambyl": TARAZ,
    # --- Павлодар ---
    "павлодар": PAVLODAR,
    "pavlodar": PAVLODAR,
    # --- Усть-Каменогорск (Өскемен) ---
    "усть-каменогорск": OSKEMEN,
    "усть каменогорск": OSKEMEN,
    "өскемен": OSKEMEN,
    "оскемен": OSKEMEN,
    "ust-kamenogorsk": OSKEMEN,
    "ust kamenogorsk": OSKEMEN,
    "oskemen": OSKEMEN,
    # --- Семей (Семипалатинск) ---
    "семей": SEMEY,
    "семипалатинск": SEMEY,
    "semey": SEMEY,
    "semipalatinsk": SEMEY,
    # --- Атырау (Гурьев) ---
    "атырау": ATYRAU,
    "гурьев": ATYRAU,
    "atyrau": ATYRAU,
    # --- Кызылорда ---
    "кызылорда": KYZYLORDA,
    "қызылорда": KYZYLORDA,
    "кзыл-орда": KYZYLORDA,
    "kyzylorda": KYZYLORDA,
    "qyzylorda": KYZYLORDA,
    # --- Костанай (Кустанай) ---
    "костанай": KOSTANAY,
    "қостанай": KOSTANAY,
    "кустанай": KOSTANAY,
    "kostanai": KOSTANAY,
    "kostanay": KOSTANAY,
    "qostanai": KOSTANAY,
    # --- Уральск (Орал) ---
    "уральск": URALSK,
    "орал": URALSK,
    "uralsk": URALSK,
    "oral": URALSK,
    # --- Петропавловск ---
    "петропавловск": PETROPAVLOVSK,
    "петропавл": PETROPAVLOVSK,
    "petropavlovsk": PETROPAVLOVSK,
    "petropavl": PETROPAVLOVSK,
    # --- Актау (Шевченко) ---
    "актау": AKTAU,
    "ақтау": AKTAU,
    "шевченко": AKTAU,
    "aktau": AKTAU,
    "aqtau": AKTAU,
    # --- Темиртау ---
    "темиртау": TEMIRTAU,
    "теміртау": TEMIRTAU,
    "temirtau": TEMIRTAU,
    # --- Талдыкорган ---
    "талдыкорган": TALDYKORGAN,
    "талдықорған": TALDYKORGAN,
    "taldykorgan": TALDYKORGAN,
    "taldyqorghan": TALDYKORGAN,
    # --- Туркестан ---
    "туркестан": TURKISTAN,
    "түркістан": TURKISTAN,
    "turkistan": TURKISTAN,
    "turkestan": TURKISTAN,
    # --- Кокшетау (Кокчетав) ---
    "кокшетау": KOKSHETAU,
    "көкшетау": KOKSHETAU,
    "кокчетав": KOKSHETAU,
    "kokshetau": KOKSHETAU,
    "kokchetav": KOKSHETAU,
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


def _region_path(entry: GeoEntry) -> str:
    """Путь регионального сегмента URL (с ведущим слэшем, без query-string)."""
    if entry.url_style == "geo":
        return f"/maps/geo/{entry.slug}/{entry.geo_id}/"
    return f"/maps/{entry.geo_id}/{entry.slug}/"


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
        ``https://yandex.kz/maps/<geo_id>/<slug>/?text=...`` (classic),
        ``https://yandex.kz/maps/geo/<slug>/<geo_id>/?text=...`` (geo)
        либо ``https://yandex.kz/maps/?text=...`` если регион не разрешён.

    Examples
    --------
    >>> build_search_url("кафе", "Алматы")
    'https://yandex.kz/maps/162/almaty/?text=%D0%BA%D0%B0%D1%84%D0%B5'
    >>> build_search_url("аптека", "Шымкент")
    'https://yandex.kz/maps/geo/shymkent/1842519191/?text=%D0%B0%D0%BF%D1%82%D0%B5%D0%BA%D0%B0'
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
        return f"https://yandex.kz{_region_path(entry)}?{qs}"
    return f"https://yandex.kz/maps/?{qs}"


__all__ = [
    "GeoEntry",
    "GEO_ENTRIES",
    "KAZAKHSTAN",
    # classic-style cities
    "ALMATY",
    "ASTANA",
    "KARAGANDA",
    "SEMEY",
    "PAVLODAR",
    "ATYRAU",
    "KOSTANAY",
    "PETROPAVLOVSK",
    "URALSK",
    "OSKEMEN",
    "AKTOBE",
    "KOKSHETAU",
    "TARAZ",
    "AKTAU",
    "TEMIRTAU",
    # geo-style cities
    "SHYMKENT",
    "KYZYLORDA",
    "TURKISTAN",
    "TALDYKORGAN",
    # API
    "resolve",
    "list_regions",
    "list_cities",
    "build_search_url",
]
