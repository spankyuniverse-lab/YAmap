"""Тесты справочника географии (Казахстан) и URL-билдера."""

import urllib.parse

import pytest

from yandex_parser.geography import (
    ALMATY,
    ASTANA,
    KAZAKHSTAN,
    GeoEntry,
    build_search_url,
    list_cities,
    list_regions,
    resolve,
)


class TestResolve:
    """Разрешение человеческих названий в GeoEntry."""

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("Алматы", ALMATY),
            ("алматы", ALMATY),
            ("АЛМАТЫ", ALMATY),
            ("  Алматы  ", ALMATY),
            ("Алма-Ата", ALMATY),
            ("Alma-Ata", ALMATY),
            ("Almaty", ALMATY),
            ("Астана", ASTANA),
            ("astana", ASTANA),
            ("Нур-Султан", ASTANA),
            ("Nur-Sultan", ASTANA),
            ("Казахстан", KAZAKHSTAN),
            ("Қазақстан", KAZAKHSTAN),
            ("Kazakhstan", KAZAKHSTAN),
            ("Qazaqstan", KAZAKHSTAN),
            ("KZ", KAZAKHSTAN),
        ],
    )
    def test_known_names(self, name, expected):
        assert resolve(name) is expected

    def test_yo_normalization(self):
        from yandex_parser.geography import _norm

        assert _norm("Ёжик") == "ежик"
        assert _norm("  АЛМАТЫ  ") == "алматы"

    def test_unknown_returns_none(self):
        assert resolve("Тимбукту") is None
        assert resolve("Москва") is None  # В справочнике КЗ нет Москвы
        assert resolve("") is None
        assert resolve("  ") is None


class TestGeoEntry:
    """Структура GeoEntry."""

    def test_almaty_fields(self):
        assert ALMATY.geo_id == 162
        assert ALMATY.slug == "almaty"
        assert ALMATY.kind == "city"

    def test_astana_fields(self):
        assert ASTANA.geo_id == 163
        assert ASTANA.slug == "astana"
        assert ASTANA.kind == "city"

    def test_kazakhstan_fields(self):
        assert KAZAKHSTAN.geo_id == 159
        assert KAZAKHSTAN.slug == "kazakhstan"
        assert KAZAKHSTAN.kind == "country"

    def test_entries_are_frozen(self):
        with pytest.raises(Exception):
            ALMATY.geo_id = 0  # type: ignore[misc]


class TestListings:
    """list_regions / list_cities."""

    def test_cities_unique(self):
        cities = list_cities()
        ids = [e.geo_id for e in cities]
        assert len(ids) == len(set(ids))
        assert all(e.kind == "city" for e in cities)

    def test_almaty_and_astana_in_cities(self):
        cities = list_cities()
        assert ALMATY in cities
        assert ASTANA in cities

    def test_regions_empty(self):
        # Справочник пока не содержит субъектов/областей
        assert list_regions() == []


class TestBuildSearchURL:
    """URL-билдер (домен yandex.kz)."""

    def test_plain_query_no_place(self):
        url = build_search_url("кафе")
        assert url.startswith("https://yandex.kz/maps/?")
        assert "text=" in url
        assert urllib.parse.quote("кафе") in url

    def test_with_known_city_name(self):
        url = build_search_url("кафе", "Алматы")
        assert url.startswith("https://yandex.kz/maps/162/almaty/?")
        assert "text=" in url

    def test_with_astana(self):
        url = build_search_url("аптеки", "Астана")
        assert "/maps/163/astana/" in url

    def test_with_kazakhstan_country(self):
        url = build_search_url("банки", "Казахстан")
        assert "/maps/159/kazakhstan/" in url

    def test_with_geo_entry_direct(self):
        url = build_search_url("рестораны", ALMATY)
        assert "/maps/162/almaty/" in url

    def test_unknown_city_falls_back_to_text(self):
        url = build_search_url("кафе", "Шымкент")
        # Шымкент нет в справочнике → попадает в текст запроса
        assert "yandex.kz/maps/?" in url
        assert urllib.parse.quote_plus("Шымкент кафе") in url

    def test_with_ll_and_z(self):
        url = build_search_url("кафе", "Алматы", ll=(76.945, 43.238), z=15)
        assert "ll=76.945%2C43.238" in url
        assert "z=15" in url

    def test_with_spn(self):
        url = build_search_url("кафе", "Алматы", spn=(0.5, 0.3))
        assert "spn=0.5%2C0.3" in url

    def test_z_out_of_range(self):
        with pytest.raises(ValueError):
            build_search_url("кафе", z=22)
        with pytest.raises(ValueError):
            build_search_url("кафе", z=-1)

    def test_none_place_no_geo(self):
        url = build_search_url("кафе", None)
        assert url.startswith("https://yandex.kz/maps/?")

    def test_empty_place(self):
        url = build_search_url("кафе", "")
        assert url.startswith("https://yandex.kz/maps/?")

    def test_domain_is_kz(self):
        """Все URL используют домен .kz, не .ru."""
        for url in [
            build_search_url("кафе"),
            build_search_url("кафе", "Алматы"),
            build_search_url("кафе", ASTANA),
            build_search_url("кафе", "неизвестный город"),
        ]:
            assert "yandex.kz" in url
            assert "yandex.ru" not in url
