"""Тесты yamap.validators — нормализация phone/website/email."""

from __future__ import annotations

from yamap.models import Organization
from yamap.validators import (
    normalize_email,
    normalize_org,
    normalize_phone,
    normalize_website,
)


class TestNormalizePhone:
    def test_empty(self):
        assert normalize_phone("") == ""

    def test_too_short(self):
        assert normalize_phone("123") == ""
        assert normalize_phone("12345") == ""

    def test_keeps_original_formatting(self):
        # Не ломаем оригинальное форматирование
        assert normalize_phone("+7 (495) 123-45-67") == "+7 (495) 123-45-67"

    def test_dedup_8_and_plus7_same_number(self):
        # 8(495)... и +7(495)... — одно и то же число
        result = normalize_phone("8 (495) 123-45-67; +7 (495) 123-45-67")
        # Оба маппятся в один ключ → выводится только первый
        assert result.count(";") == 0

    def test_multiple_phones_preserved(self):
        result = normalize_phone("+7-495-555-66-77; +7-495-555-66-88")
        assert result.count(";") == 1

    def test_separator_slash(self):
        result = normalize_phone("+7-495-555-66-77 / +7-495-555-66-88")
        assert "55-66-77" in result and "55-66-88" in result

    def test_separator_ili(self):
        result = normalize_phone("+7-495-555-66-77 или +7-495-555-66-88")
        # Оба номера остаются
        assert "55-66-77" in result and "55-66-88" in result

    def test_strips_label(self):
        # 'Тел: ...' не теряет цифры; полная строка сохраняется
        result = normalize_phone("Тел: +7-495-555-66-77")
        assert "555-66-77" in result


class TestNormalizeWebsite:
    def test_empty(self):
        assert normalize_website("") == ""

    def test_adds_https(self):
        assert normalize_website("example.com") == "https://example.com"

    def test_protocol_relative(self):
        assert normalize_website("//example.com") == "https://example.com"

    def test_keeps_existing_https(self):
        assert normalize_website("https://example.com") == "https://example.com"

    def test_keeps_http(self):
        # Не апгрейдим http→https принудительно
        assert normalize_website("http://example.com") == "http://example.com"

    def test_strip_utm(self):
        result = normalize_website("https://x.com?utm_source=y&utm_medium=z&id=42")
        assert "utm_source" not in result
        assert "utm_medium" not in result
        assert "id=42" in result

    def test_strip_gclid_fbclid(self):
        result = normalize_website("https://x.com?gclid=abc&fbclid=def")
        assert result == "https://x.com"

    def test_strip_yclid(self):
        result = normalize_website("https://x.com?yclid=xyz&q=1")
        assert "yclid" not in result
        assert "q=1" in result

    def test_no_query_string_unchanged(self):
        assert normalize_website("https://x.com/path") == "https://x.com/path"

    def test_invalid_no_dot_unchanged(self):
        # Строки без точки не превращаются в URL
        assert normalize_website("not a url") == "not a url"


class TestNormalizeEmail:
    def test_empty(self):
        assert normalize_email("") == ""

    def test_lowercase(self):
        assert normalize_email("SUPPORT@Example.COM") == "support@example.com"

    def test_dedup_case_insensitive(self):
        assert normalize_email("A@b.c; a@B.C") == "a@b.c"

    def test_extracts_from_text(self):
        result = normalize_email("Email: support@example.com и info@example.com")
        assert "support@example.com" in result
        assert "info@example.com" in result

    def test_no_email_returns_empty(self):
        assert normalize_email("no email here") == ""

    def test_multiple_unique(self):
        result = normalize_email("a@b.c; d@e.f; a@b.c")
        assert result.count(";") == 1


class TestNormalizeOrg:
    def test_normalizes_all_fields_in_place(self):
        o = Organization(
            phone="8 (495) 555-66-77",
            website="example.com?utm_source=y",
            email="SUPPORT@Example.com",
        )
        normalize_org(o)
        assert o.email == "support@example.com"
        assert "utm" not in o.website
        assert o.website.startswith("https://")
        assert "555-66-77" in o.phone

    def test_does_not_break_empty_fields(self):
        o = Organization()
        normalize_org(o)  # должно не упасть
        assert o.phone == ""
        assert o.website == ""
        assert o.email == ""

    def test_does_not_touch_other_fields(self):
        o = Organization(name="Кофе", address="ул. Ленина", category="Еда")
        normalize_org(o)
        assert o.name == "Кофе"
        assert o.address == "ул. Ленина"
        assert o.category == "Еда"
