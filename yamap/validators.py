"""Нормализация phone / website / email."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Organization


_PHONE_DIGITS_RE = re.compile(r"\D+")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def normalize_phone(raw: str) -> str:
    """Нормализовать строку с телефонами.

    'Тел: +7 (495) 123-45-67' → '+7 (495) 123-45-67'
    Несколько номеров через '; ' остаются разделёнными.
    Российские номера приводятся к +7 (если начинаются с 8).
    """
    if not raw:
        return ""

    parts = re.split(r"[;,/]+|\sили\s", raw)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        digits = _PHONE_DIGITS_RE.sub("", part)
        if len(digits) < 7:
            continue
        # Приводим российские к +7
        if len(digits) == 11 and digits.startswith("8"):
            digits = "7" + digits[1:]
        # Канонический ключ для дедупа — только цифры
        if digits in seen:
            continue
        seen.add(digits)
        # Возвращаем оригинальное форматирование, если есть, иначе формат +N
        cleaned = part.strip(" \t.;,-")
        if not cleaned:
            cleaned = f"+{digits}"
        out.append(cleaned)
    return "; ".join(out)


def normalize_website(raw: str) -> str:
    """Нормализовать URL: добавить схему, убрать UTM-мусор."""
    if not raw:
        return ""
    url = raw.strip()
    if url.startswith("//"):
        url = "https:" + url
    elif not re.match(r"^https?://", url, flags=re.IGNORECASE):
        # 'example.com/path' → 'https://example.com/path'
        if "." in url and " " not in url:
            url = "https://" + url
    # Убираем UTM-параметры
    if "?" in url:
        base, _, query = url.partition("?")
        kept = [
            kv for kv in query.split("&")
            if kv and not kv.lower().startswith(("utm_", "yclid=", "gclid=", "fbclid="))
        ]
        url = base + ("?" + "&".join(kept) if kept else "")
    return url


def normalize_email(raw: str) -> str:
    """Извлечь email-адреса из строки и привести к нижнему регистру."""
    if not raw:
        return ""
    found = _EMAIL_RE.findall(raw)
    out: list[str] = []
    seen: set[str] = set()
    for e in found:
        e_low = e.lower()
        if e_low not in seen:
            seen.add(e_low)
            out.append(e_low)
    return "; ".join(out)


def normalize_org(org: "Organization") -> None:
    """Применить нормализацию ко всем строковым полям организации (in-place)."""
    if org.phone:
        org.phone = normalize_phone(org.phone)
    if org.website:
        org.website = normalize_website(org.website)
    if org.email:
        org.email = normalize_email(org.email)
