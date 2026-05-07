"""Data model + dedup helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass, fields


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


def normalize_for_dedup(text: str) -> str:
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


def dedup_key(org: Organization) -> str:
    return f"{normalize_for_dedup(org.name)}|{normalize_for_dedup(org.address)}"
