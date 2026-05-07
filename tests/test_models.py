"""Тесты yamap.models — Organization, normalize_for_dedup, dedup_key."""

from __future__ import annotations

from yamap.models import (
    FIELDNAMES,
    HEADERS_RU,
    Organization,
    dedup_key,
    normalize_for_dedup,
)


class TestOrganization:
    def test_default_fields_all_empty_strings(self):
        org = Organization()
        for field_name in FIELDNAMES:
            assert getattr(org, field_name) == ""

    def test_fieldnames_count(self):
        # 14 публичных полей
        assert len(FIELDNAMES) == 14

    def test_headers_ru_covers_all_fields(self):
        for f in FIELDNAMES:
            assert f in HEADERS_RU, f"Missing Russian header for {f}"


class TestNormalizeForDedup:
    def test_lowercase_and_strip(self):
        assert normalize_for_dedup("  Кофейня  ") == "кофейня"

    def test_address_abbreviation_street(self):
        a = normalize_for_dedup("ул. Ленина, 5")
        b = normalize_for_dedup("улица Ленина, 5")
        assert a == b

    def test_address_abbreviation_avenue(self):
        a = normalize_for_dedup("проспект Мира, 10")
        b = normalize_for_dedup("пр-т Мира, 10")
        assert a == b

    def test_address_abbreviation_lane(self):
        assert normalize_for_dedup("переулок Малый, 3") == normalize_for_dedup("пер Малый, 3")

    def test_address_abbreviation_boulevard(self):
        assert normalize_for_dedup("бульвар Гагарина") == normalize_for_dedup("б-р Гагарина")

    def test_punctuation_normalized(self):
        a = normalize_for_dedup("ул. Ленина, 5")
        b = normalize_for_dedup("ул Ленина 5")
        assert a == b

    def test_multiple_spaces_collapsed(self):
        assert normalize_for_dedup("a   b   c") == "a b c"

    def test_empty_string(self):
        assert normalize_for_dedup("") == ""


class TestDedupKey:
    def test_combines_name_and_address(self):
        org = Organization(name="Кофейня", address="ул. Ленина, 5")
        key = dedup_key(org)
        assert "кофейня" in key
        assert "|" in key

    def test_same_org_different_address_format(self):
        a = dedup_key(Organization(name="Кофейня", address="ул. Ленина, 5"))
        b = dedup_key(Organization(name="Кофейня", address="улица Ленина, 5"))
        assert a == b

    def test_different_orgs_different_keys(self):
        a = dedup_key(Organization(name="A", address="addr1"))
        b = dedup_key(Organization(name="B", address="addr1"))
        assert a != b

    def test_empty_org_key(self):
        # Пустая организация — ключ "|"
        assert dedup_key(Organization()) == "|"
