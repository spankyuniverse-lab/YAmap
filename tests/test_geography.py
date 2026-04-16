"""Тесты справочника географии и URL-билдера."""

import urllib.parse

import pytest

from yandex_parser.geography import (
    GeoEntry,
    MOSCOW,
    MOSCOW_OBLAST,
    RUSSIA,
    SPB,
    SPB_OBLAST,
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
            ("Москва", MOSCOW),
            ("москва", MOSCOW),
            ("МОСКВА", MOSCOW),
            ("  Москва  ", MOSCOW),
            ("Мск", MOSCOW),
            ("Moscow", MOSCOW),
            ("Санкт-Петербург", SPB),
            ("СПб", SPB),
            ("Питер", SPB),
            ("saint-petersburg", SPB),
            ("Россия", RUSSIA),
            ("РФ", RUSSIA),
        ],
    )
    def test_known_names(self, name, expected):
        assert resolve(name) is expected

    @pytest.mark.parametrize(
        "name, expected",
        [
            ("Московская область", MOSCOW_OBLAST),
            ("Москва и область", MOSCOW_OBLAST),
            ("Москва и Московская область", MOSCOW_OBLAST),
            ("Подмосковье", MOSCOW_OBLAST),
            ("Ленинградская область", SPB_OBLAST),
            ("ЛО", SPB_OBLAST),
            ("Санкт-Петербург и область", SPB_OBLAST),
        ],
    )
    def test_regions(self, name, expected):
        assert resolve(name) is expected

    def test_yo_normalization(self):
        # _norm заменяет ё → е; проверяем что алгоритм работает через
        # прямое обращение (в словаре все ключи уже без ё).
        from yandex_parser.geography import _norm

        assert _norm("Ёжик") == "ежик"
        assert _norm("  МОСКВА  ") == "москва"

    def test_unknown_returns_none(self):
        assert resolve("Тимбукту") is None
        assert resolve("") is None
        assert resolve("  ") is None


class TestGeoEntry:
    """Структура GeoEntry."""

    def test_moscow_fields(self):
        assert MOSCOW.geo_id == 213
        assert MOSCOW.slug == "moscow"
        assert MOSCOW.kind == "city"

    def test_moscow_oblast_fields(self):
        assert MOSCOW_OBLAST.geo_id == 1
        assert MOSCOW_OBLAST.slug == "moscow-and-moscow-oblast"
        assert MOSCOW_OBLAST.kind == "region"

    def test_spb_fields(self):
        assert SPB.geo_id == 2
        assert SPB.slug == "saint-petersburg"

    def test_spb_oblast_fields(self):
        assert SPB_OBLAST.geo_id == 10174
        assert "leningrad" in SPB_OBLAST.slug

    def test_russia_fields(self):
        assert RUSSIA.geo_id == 225
        assert RUSSIA.kind == "country"

    def test_entries_are_frozen(self):
        with pytest.raises(Exception):
            MOSCOW.geo_id = 0  # type: ignore[misc]


class TestListings:
    """list_regions / list_cities."""

    def test_regions_unique(self):
        regions = list_regions()
        ids = [e.geo_id for e in regions]
        assert len(ids) == len(set(ids))
        assert all(e.kind == "region" for e in regions)

    def test_cities_unique(self):
        cities = list_cities()
        ids = [e.geo_id for e in cities]
        assert len(ids) == len(set(ids))
        assert all(e.kind == "city" for e in cities)

    def test_moscow_in_cities(self):
        assert MOSCOW in list_cities()

    def test_moscow_oblast_in_regions(self):
        assert MOSCOW_OBLAST in list_regions()


class TestBuildSearchURL:
    """URL-билдер."""

    def test_plain_query_no_place(self):
        url = build_search_url("кафе")
        assert url.startswith("https://yandex.ru/maps/?")
        assert "text=" in url
        # URL encoded 'кафе'
        assert urllib.parse.quote("кафе") in url

    def test_with_known_city_name(self):
        url = build_search_url("кафе", "Москва")
        assert url.startswith("https://yandex.ru/maps/213/moscow/?")
        assert "text=" in url

    def test_with_known_region_name(self):
        url = build_search_url("аптеки", "Московская область")
        assert "/maps/1/moscow-and-moscow-oblast/" in url

    def test_with_geo_entry_direct(self):
        url = build_search_url("рестораны", SPB)
        assert "/maps/2/saint-petersburg/" in url

    def test_unknown_city_falls_back_to_text(self):
        url = build_search_url("кафе", "Тимбукту")
        # Неизвестный город не даёт geo_id, но попадает в текст запроса.
        # urlencode() кодирует пробелы как '+'.
        assert "/maps/?" in url
        assert urllib.parse.quote_plus("Тимбукту кафе") in url

    def test_with_ll_and_z(self):
        url = build_search_url("кафе", "Москва", ll=(37.62, 55.75), z=15)
        assert "ll=37.62%2C55.75" in url
        assert "z=15" in url

    def test_with_spn(self):
        url = build_search_url("кафе", "Москва", spn=(0.5, 0.3))
        assert "spn=0.5%2C0.3" in url

    def test_z_out_of_range(self):
        with pytest.raises(ValueError):
            build_search_url("кафе", z=22)
        with pytest.raises(ValueError):
            build_search_url("кафе", z=-1)

    def test_none_place_no_geo(self):
        url = build_search_url("кафе", None)
        assert url.startswith("https://yandex.ru/maps/?")

    def test_empty_place(self):
        url = build_search_url("кафе", "")
        assert url.startswith("https://yandex.ru/maps/?")
