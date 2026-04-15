"""Модель данных: Organization, дедупликация, валидация."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, fields

log = logging.getLogger("yandex_parser")


@dataclass
class Organization:
    name: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    rating: str = ""
    reviews_count: str = ""
    category: str = ""
    working_hours: str = ""
    latitude: str = ""
    longitude: str = ""
    email: str = ""
    social_links: str = ""
    yandex_url: str = ""
    search_query: str = ""


FIELDNAMES = [f.name for f in fields(Organization)]

HEADERS_RU = {
    "name": "Название",
    "address": "Адрес",
    "phone": "Телефон",
    "website": "Сайт",
    "rating": "Рейтинг",
    "reviews_count": "Отзывы",
    "category": "Категория",
    "working_hours": "Часы работы",
    "latitude": "Широта",
    "longitude": "Долгота",
    "email": "Email",
    "social_links": "Соцсети",
    "search_query": "Поисковый запрос",
    "yandex_url": "Ссылка",
}


def _normalize_for_dedup(text: str) -> str:
    """Нормализовать строку для дедупликации.

    'ул. Ленина, 5' и 'улица Ленина, 5' → одинаковый ключ.
    """
    s = text.lower().strip()
    s = re.sub(r'\bулица\b', 'ул', s)
    s = re.sub(r'\bпроспект\b', 'пр-т', s)
    s = re.sub(r'\bпереулок\b', 'пер', s)
    s = re.sub(r'\bбульвар\b', 'б-р', s)
    s = re.sub(r'\bпроезд\b', 'пр-д', s)
    s = re.sub(r'\bнабережная\b', 'наб', s)
    s = re.sub(r'\bплощадь\b', 'пл', s)
    s = re.sub(r'\bмикрорайон\b', 'мкр', s)
    s = s.replace('.', '').replace(',', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _dedup_key(org: Organization) -> str:
    """Ключ для дедупликации организации."""
    return f"{_normalize_for_dedup(org.name)}|{_normalize_for_dedup(org.address)}"


def _is_valid_org(org: Organization) -> bool:
    """Минимальная валидация: организация должна иметь имя."""
    if not org.name or not org.name.strip():
        return False
    name = org.name.strip().lower()
    if name in ("none", "null", "undefined", "—", "-"):
        return False
    return True


def _filter_valid(orgs: list[Organization]) -> list[Organization]:
    """Отфильтровать пустые/невалидные организации."""
    valid = [o for o in orgs if _is_valid_org(o)]
    dropped = len(orgs) - len(valid)
    if dropped:
        log.info("Отфильтровано %d невалидных записей (без имени)", dropped)
    return valid


def org_to_dict(org: Organization) -> dict:
    """Сериализовать организацию в dict."""
    return asdict(org)


def print_stats(orgs: list[Organization], label: str = "Результаты") -> None:
    """Напечатать сводную статистику по собранным организациям."""
    if not orgs:
        print(f"\n{label}: 0 организаций")
        return

    total = len(orgs)
    with_phone = sum(1 for o in orgs if o.phone)
    with_website = sum(1 for o in orgs if o.website)
    with_email = sum(1 for o in orgs if o.email)
    with_social = sum(1 for o in orgs if o.social_links)
    with_coords = sum(1 for o in orgs if o.latitude)
    with_rating = [float(o.rating.replace(",", ".")) for o in orgs if o.rating]
    avg_rating = sum(with_rating) / len(with_rating) if with_rating else 0

    cat_counts: dict[str, int] = {}
    for o in orgs:
        for c in (o.category or "").split(","):
            c = c.strip()
            if c:
                cat_counts[c] = cat_counts.get(c, 0) + 1
    top_cats = sorted(cat_counts.items(), key=lambda x: -x[1])[:5]

    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}")
    print(f"  Всего организаций:  {total}")
    print(f"  С телефоном:        {with_phone} ({with_phone * 100 // total}%)")
    print(f"  С сайтом:           {with_website} ({with_website * 100 // total}%)")
    print(f"  С email:            {with_email} ({with_email * 100 // total}%)")
    print(f"  С соцсетями:        {with_social} ({with_social * 100 // total}%)")
    print(f"  С координатами:     {with_coords} ({with_coords * 100 // total}%)")
    if with_rating:
        print(f"  Средний рейтинг:    {avg_rating:.1f} (из {len(with_rating)} оценок)")
    if top_cats:
        print(f"  Топ категории:")
        for cat, cnt in top_cats:
            print(f"    {cat}: {cnt}")
    print(f"{'=' * 50}\n")
