"""Тесты справочника географии (Казахстан) и URL-билдера."""

import urllib.parse

import pytest

from yandex_parser.geography import (
    AKTAU,
    AKTOBE,
    ALMATY,
    ASTANA,
    ATYRAU,
    KARAGANDA,
    KAZAKHSTAN,
    KOKSHETAU,
    KOSTANAY,
    KYZYLORDA,
    OSKEMEN,
    PAVLODAR,
    PETROPAVLOVSK,
    SEMEY,
    SHYMKENT,
    TALDYKORGAN,
    TARAZ,
    TEMIRTAU,
    TURKISTAN,
    URALSK,
    GeoEntry,
    build_search_url,
    list_cities,
    list_regions,
    resolve,
)


# Все 19 крупнейших городов, которые должны быть в справочнике.
ALL_CITIES = [
    ALMATY,
    ASTANA,
    SHYMKENT,
    KARAGANDA,
    AKTOBE,
    TARAZ,
    PAVLODAR,
    OSKEMEN,
    SEMEY,
    ATYRAU,
    KYZYLORDA,
    KOSTANAY,
    URALSK,
    PETROPAVLOVSK,
    AKTAU,
    TEMIRTAU,
    TALDYKORGAN,
    TURKISTAN,
    KOKSHETAU,
]


class TestResolve:
    """Разрешение человеческих названий в GeoEntry."""

    @pytest.mark.parametrize(
        "name, expected",
        [
            # Алматы
            ("Алматы", ALMATY),
            ("алматы", ALMATY),
            ("АЛМАТЫ", ALMATY),
            ("  Алматы  ", ALMATY),
            ("Алма-Ата", ALMATY),
            ("Alma-Ata", ALMATY),
            ("Almaty", ALMATY),
            # Астана
            ("Астана", ASTANA),
            ("astana", ASTANA),
            ("Нур-Султан", ASTANA),
            ("Nur-Sultan", ASTANA),
            # Страна
            ("Казахстан", KAZAKHSTAN),
            ("Қазақстан", KAZAKHSTAN),
            ("Kazakhstan", KAZAKHSTAN),
            ("Qazaqstan", KAZAKHSTAN),
            ("KZ", KAZAKHSTAN),
            # Шымкент
            ("Шымкент", SHYMKENT),
            ("Chimkent", SHYMKENT),
            ("shymkent", SHYMKENT),
            # Караганда
            ("Караганда", KARAGANDA),
            ("қарағанды", KARAGANDA),
            ("Karaganda", KARAGANDA),
            # Актобе
            ("Актобе", AKTOBE),
            ("Актюбинск", AKTOBE),
            ("Aqtobe", AKTOBE),
            # Тараз
            ("Тараз", TARAZ),
            ("Джамбул", TARAZ),
            ("Жамбыл", TARAZ),
            # Павлодар
            ("Павлодар", PAVLODAR),
            ("pavlodar", PAVLODAR),
            # Усть-Каменогорск
            ("Усть-Каменогорск", OSKEMEN),
            ("Өскемен", OSKEMEN),
            ("Oskemen", OSKEMEN),
            # Семей
            ("Семей", SEMEY),
            ("Семипалатинск", SEMEY),
            # Атырау
            ("Атырау", ATYRAU),
            ("Гурьев", ATYRAU),
            # Кызылорда
            ("Кызылорда", KYZYLORDA),
            ("Қызылорда", KYZYLORDA),
            ("Qyzylorda", KYZYLORDA),
            # Костанай
            ("Костанай", KOSTANAY),
            ("Кустанай", KOSTANAY),
            ("kostanay", KOSTANAY),
            # Уральск
            ("Уральск", URALSK),
            ("Орал", URALSK),
            ("Oral", URALSK),
            # Петропавловск
            ("Петропавловск", PETROPAVLOVSK),
            ("Petropavl", PETROPAVLOVSK),
            # Актау
            ("Актау", AKTAU),
            ("Шевченко", AKTAU),
            ("Aqtau", AKTAU),
            # Темиртау
            ("Темиртау", TEMIRTAU),
            ("temirtau", TEMIRTAU),
            # Талдыкорган
            ("Талдыкорган", TALDYKORGAN),
            ("Taldyqorghan", TALDYKORGAN),
            # Туркестан
            ("Туркестан", TURKISTAN),
            ("Turkistan", TURKISTAN),
            # Кокшетау
            ("Кокшетау", KOKSHETAU),
            ("Кокчетав", KOKSHETAU),
            ("kokshetau", KOKSHETAU),
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
        assert ALMATY.url_style == "classic"

    def test_astana_fields(self):
        assert ASTANA.geo_id == 163
        assert ASTANA.slug == "astana"
        assert ASTANA.kind == "city"
        assert ASTANA.url_style == "classic"

    def test_kazakhstan_fields(self):
        assert KAZAKHSTAN.geo_id == 159
        assert KAZAKHSTAN.slug == "kazakhstan"
        assert KAZAKHSTAN.kind == "country"

    def test_shymkent_is_geo_style(self):
        assert SHYMKENT.geo_id == 1842519191
        assert SHYMKENT.slug == "shymkent"
        assert SHYMKENT.url_style == "geo"

    def test_geo_style_cities(self):
        """4 города используют /maps/geo/<slug>/<id>/."""
        geo_style = {c for c in ALL_CITIES if c.url_style == "geo"}
        assert geo_style == {SHYMKENT, KYZYLORDA, TURKISTAN, TALDYKORGAN}

    def test_classic_style_cities(self):
        """Остальные 15 — classic /maps/<id>/<slug>/."""
        classic = {c for c in ALL_CITIES if c.url_style == "classic"}
        assert len(classic) == 15
        assert ALMATY in classic
        assert KARAGANDA in classic

    def test_entries_are_frozen(self):
        with pytest.raises(Exception):
            ALMATY.geo_id = 0  # type: ignore[misc]


class TestListings:
    """list_regions / list_cities."""

    def test_cities_count_is_19(self):
        cities = list_cities()
        assert len(cities) == 19

    def test_cities_unique(self):
        cities = list_cities()
        ids = [e.geo_id for e in cities]
        assert len(ids) == len(set(ids))
        assert all(e.kind == "city" for e in cities)

    def test_all_top19_in_cities(self):
        cities = list_cities()
        for c in ALL_CITIES:
            assert c in cities, f"{c.name} missing from list_cities()"

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

    def test_shymkent_uses_geo_style(self):
        url = build_search_url("кафе", "Шымкент")
        # Шымкент теперь в справочнике → /maps/geo/shymkent/<id>/
        assert "/maps/geo/shymkent/1842519191/" in url
        assert "yandex.kz" in url

    def test_kyzylorda_uses_geo_style(self):
        url = build_search_url("аптеки", "Кызылорда")
        assert "/maps/geo/qyzylorda/53168216/" in url

    def test_turkistan_uses_geo_style(self):
        url = build_search_url("кафе", "Туркестан")
        assert "/maps/geo/turkistan/53168220/" in url

    def test_taldykorgan_uses_geo_style(self):
        url = build_search_url("салоны", "Талдыкорган")
        assert "/maps/geo/taldyqorghan/53168289/" in url

    def test_karaganda_classic_style(self):
        url = build_search_url("рестораны", "Караганда")
        assert "/maps/164/karaganda/" in url

    def test_aktobe_classic_style(self):
        url = build_search_url("салоны", "Актобе")
        assert "/maps/20273/aktobe/" in url

    def test_unknown_city_falls_back_to_text(self):
        # Выбираем заведомо отсутствующий в справочнике — любой городок КЗ,
        # не входящий в топ-19.
        url = build_search_url("кафе", "Балхаш")
        assert "yandex.kz/maps/?" in url
        assert urllib.parse.quote_plus("Балхаш кафе") in url

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
            build_search_url("кафе", SHYMKENT),
            build_search_url("кафе", "неизвестный город"),
        ]:
            assert "yandex.kz" in url
            assert "yandex.ru" not in url

    @pytest.mark.parametrize("city", ALL_CITIES)
    def test_every_city_builds_valid_url(self, city):
        """Каждый из 19 городов должен давать работоспособный URL."""
        url = build_search_url("кафе", city)
        assert url.startswith("https://yandex.kz/")
        assert "text=" in url
        assert str(city.geo_id) in url
        assert city.slug in url
        if city.url_style == "geo":
            assert f"/maps/geo/{city.slug}/{city.geo_id}/" in url
        else:
            assert f"/maps/{city.geo_id}/{city.slug}/" in url
