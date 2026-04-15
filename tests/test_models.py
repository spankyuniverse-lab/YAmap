"""Тесты: Organization, дедупликация, валидация."""

import pytest

from yandex_parser.models import (
    FIELDNAMES,
    HEADERS_RU,
    Organization,
    _dedup_key,
    _filter_valid,
    _is_valid_org,
    _normalize_for_dedup,
    print_stats,
)


# ---------------------------------------------------------------------------
# _normalize_for_dedup
# ---------------------------------------------------------------------------


class TestNormalizeForDedup:
    def test_lowercase(self):
        assert _normalize_for_dedup("МОСКВА") == "москва"

    def test_strip(self):
        assert _normalize_for_dedup("  ул. Ленина  ") == "ул ленина"

    def test_street_abbreviations(self):
        assert _normalize_for_dedup("улица Ленина") == "ул ленина"
        assert _normalize_for_dedup("проспект Мира") == "пр-т мира"
        assert _normalize_for_dedup("переулок Тихий") == "пер тихий"
        assert _normalize_for_dedup("бульвар Славы") == "б-р славы"
        assert _normalize_for_dedup("набережная Фонтанки") == "наб фонтанки"
        assert _normalize_for_dedup("площадь Победы") == "пл победы"
        assert _normalize_for_dedup("микрорайон Северный") == "мкр северный"

    def test_dots_commas_removed(self):
        assert _normalize_for_dedup("ул. Ленина, 5") == "ул ленина 5"

    def test_multiple_spaces(self):
        assert _normalize_for_dedup("ул   Ленина   5") == "ул ленина 5"

    def test_same_after_normalization(self):
        a = _normalize_for_dedup("ул. Ленина, 5")
        b = _normalize_for_dedup("улица Ленина 5")
        assert a == b

    def test_empty(self):
        assert _normalize_for_dedup("") == ""


# ---------------------------------------------------------------------------
# _dedup_key
# ---------------------------------------------------------------------------


class TestDedupKey:
    def test_basic(self):
        org = Organization(name="Кафе Рога", address="ул. Ленина, 5")
        key = _dedup_key(org)
        assert "|" in key
        assert "кафе рога" in key
        assert "ул ленина 5" in key

    def test_same_org_different_format(self):
        a = Organization(name="Кафе Рога", address="ул. Ленина, 5")
        b = Organization(name="кафе рога", address="улица Ленина 5")
        assert _dedup_key(a) == _dedup_key(b)

    def test_different_orgs(self):
        a = Organization(name="Кафе Рога", address="ул. Ленина, 5")
        b = Organization(name="Кафе Копыта", address="ул. Ленина, 5")
        assert _dedup_key(a) != _dedup_key(b)

    def test_empty(self):
        org = Organization()
        key = _dedup_key(org)
        assert key == "|"


# ---------------------------------------------------------------------------
# _is_valid_org / _filter_valid
# ---------------------------------------------------------------------------


class TestValidation:
    def test_valid_org(self):
        org = Organization(name="Кафе Рога")
        assert _is_valid_org(org) is True

    def test_empty_name(self):
        org = Organization(name="")
        assert _is_valid_org(org) is False

    def test_whitespace_name(self):
        org = Organization(name="   ")
        assert _is_valid_org(org) is False

    def test_none_name(self):
        org = Organization(name="none")
        assert _is_valid_org(org) is False

    def test_null_name(self):
        org = Organization(name="null")
        assert _is_valid_org(org) is False

    def test_undefined_name(self):
        org = Organization(name="undefined")
        assert _is_valid_org(org) is False

    def test_dash_name(self):
        org = Organization(name="—")
        assert _is_valid_org(org) is False
        assert _is_valid_org(Organization(name="-")) is False

    def test_filter_valid(self):
        orgs = [
            Organization(name="Кафе Рога"),
            Organization(name=""),
            Organization(name="none"),
            Organization(name="Бар Луна"),
        ]
        valid = _filter_valid(orgs)
        assert len(valid) == 2
        assert valid[0].name == "Кафе Рога"
        assert valid[1].name == "Бар Луна"

    def test_filter_valid_empty(self):
        assert _filter_valid([]) == []


# ---------------------------------------------------------------------------
# Organization dataclass
# ---------------------------------------------------------------------------


class TestOrganization:
    def test_defaults(self):
        org = Organization()
        assert org.name == ""
        assert org.phone == ""

    def test_fieldnames(self):
        assert "name" in FIELDNAMES
        assert "phone" in FIELDNAMES
        assert "yandex_url" in FIELDNAMES
        assert len(FIELDNAMES) == 14

    def test_headers_ru(self):
        assert HEADERS_RU["name"] == "Название"
        assert HEADERS_RU["phone"] == "Телефон"
        assert set(HEADERS_RU.keys()) == set(FIELDNAMES)


# ---------------------------------------------------------------------------
# print_stats (smoke test)
# ---------------------------------------------------------------------------


class TestPrintStats:
    def test_empty(self, capsys):
        print_stats([])
        out = capsys.readouterr().out
        assert "0 организаций" in out

    def test_with_data(self, capsys):
        orgs = [
            Organization(name="A", phone="+7123", rating="4.5"),
            Organization(name="B", website="example.com"),
        ]
        print_stats(orgs, label="Тест")
        out = capsys.readouterr().out
        assert "Тест" in out
        assert "2" in out
