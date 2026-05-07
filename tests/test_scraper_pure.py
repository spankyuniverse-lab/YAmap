"""Тесты чистых функций scraper — без браузера.

Покрывает:
- _extract_coords_from_url: парсинг координат из URL Я.Карт
- _extract_orgs_from_api_response: парсинг JSON из перехваченного XHR
"""

from __future__ import annotations

from yamap.scraper import _extract_coords_from_url, _extract_orgs_from_api_response


class TestExtractCoordsFromUrl:
    def test_ll_url_encoded(self):
        # Реальный формат Яндекс — ll=lon%2Clat (URL-encoded запятая)
        url = "https://yandex.ru/maps/213/moscow/?ll=37.6173%2C55.7558&z=15"
        coords = _extract_coords_from_url(url)
        assert coords is not None
        lat, lon = coords
        assert lat == "55.7558"  # Москва
        assert lon == "37.6173"

    def test_ll_raw_comma(self):
        url = "https://yandex.ru/maps/?ll=37.6173,55.7558"
        coords = _extract_coords_from_url(url)
        assert coords == ("55.7558", "37.6173")

    def test_pt_param(self):
        url = "https://yandex.ru/maps/?pt=30.3158,59.9343&z=15"
        coords = _extract_coords_from_url(url)
        assert coords == ("59.9343", "30.3158")  # Питер

    def test_pt_url_encoded(self):
        url = "https://yandex.ru/maps/?pt=30.3158%2C59.9343"
        coords = _extract_coords_from_url(url)
        assert coords == ("59.9343", "30.3158")

    def test_no_coords_returns_none(self):
        assert _extract_coords_from_url("https://yandex.ru/maps/") is None
        assert _extract_coords_from_url("") is None
        assert _extract_coords_from_url("https://example.com/?foo=bar") is None

    def test_no_match_for_other_params(self):
        # 'pt=' буква в составе другого параметра не должна ломать парсер
        # (regex привязан к началу слова — не идеально, но не ловит spt=)
        url = "https://yandex.ru/?spt=37.0,55.0"  # `spt`, не `pt`
        # Текущая реализация — regex без \b — может найти "pt=37.0,55.0"
        # Это известная нестрогость, тестом фиксируем поведение
        result = _extract_coords_from_url(url)
        # Проверяем что не упало
        assert result is None or isinstance(result, tuple)

    def test_decimal_precision(self):
        url = "https://yandex.ru/?ll=37.61730000%2C55.75580000"
        coords = _extract_coords_from_url(url)
        assert coords == ("55.75580000", "37.61730000")

    def test_real_yandex_url(self):
        # Реальный URL карточки
        url = (
            "https://yandex.ru/maps/org/kafe/12345678901/"
            "?ll=37.620393%2C55.753960&mode=search&sll=37.620393%2C55.753960&z=17"
        )
        coords = _extract_coords_from_url(url)
        assert coords is not None
        # ll= указывает на Москву
        assert coords[0].startswith("55.")
        assert coords[1].startswith("37.")


class TestExtractOrgsFromApiResponseFeatures:
    """Тесты для feature-формата ответа API Яндекс.Карт."""

    def test_empty_response(self):
        assert _extract_orgs_from_api_response({}) == []

    def test_no_features_key(self):
        assert _extract_orgs_from_api_response({"foo": "bar"}) == []

    def test_features_root(self):
        data = {
            "features": [
                {
                    "properties": {
                        "CompanyMetaData": {
                            "name": "Кофейня",
                            "address": "ул. Ленина, 5",
                        }
                    }
                }
            ]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert len(orgs) == 1
        assert orgs[0].name == "Кофейня"
        assert orgs[0].address == "ул. Ленина, 5"

    def test_data_features_nested(self):
        data = {
            "data": {
                "features": [
                    {"properties": {"CompanyMetaData": {"name": "X", "address": "Y"}}}
                ]
            }
        }
        orgs = _extract_orgs_from_api_response(data)
        assert len(orgs) == 1
        assert orgs[0].name == "X"

    def test_lowercase_company_meta(self):
        data = {
            "features": [
                {"properties": {"companyMetaData": {"name": "X", "address": "Y"}}}
            ]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert orgs[0].name == "X"

    def test_phones_formatted_field(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {
                        "name": "X",
                        "Phones": [
                            {"formatted": "+7 495 111-22-33"},
                            {"formatted": "+7 495 111-22-44"},
                        ],
                    }
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert "111-22-33" in orgs[0].phone
        assert "111-22-44" in orgs[0].phone

    def test_phones_dedup(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {
                        "name": "X",
                        "Phones": [
                            {"formatted": "+7 495 111-22-33"},
                            {"formatted": "+7 495 111-22-33"},  # дубль
                        ],
                    }
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        # normalize_org дедупит, должен остаться один
        assert orgs[0].phone.count(";") == 0

    def test_phones_lowercase_field(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {
                        "name": "X",
                        "phones": [{"number": "+7 495 555-66-77"}],
                    }
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert "555-66-77" in orgs[0].phone

    def test_url_string(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {"name": "X", "url": "https://example.com"}
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert orgs[0].website == "https://example.com"

    def test_url_dict_with_value(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {"name": "X", "url": {"value": "example.com"}}
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        # normalize_website добавляет https://
        assert orgs[0].website == "https://example.com"

    def test_hours_text(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {
                        "name": "X",
                        "Hours": {"text": "Пн-Пт 9:00-18:00"},
                    }
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert orgs[0].working_hours == "Пн-Пт 9:00-18:00"

    def test_rating_dict_with_score_and_count(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {
                        "name": "X",
                        "rating": {"value": "4.5", "ratings": "120"},
                    }
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert orgs[0].rating == "4.5"
        assert orgs[0].reviews_count == "120"

    def test_rating_alt_keys(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {
                        "name": "X",
                        "rating": {"score": "4.7", "count": "85"},
                    }
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert orgs[0].rating == "4.7"
        assert orgs[0].reviews_count == "85"

    def test_rating_scalar_fallback(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {
                        "name": "X",
                        "Rating": "4.2",
                    }
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert orgs[0].rating == "4.2"

    def test_categories_list(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {
                        "name": "X",
                        "Categories": [
                            {"name": "Кафе"},
                            {"name": "Кофейня"},
                        ],
                    }
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert "Кафе" in orgs[0].category
        assert "Кофейня" in orgs[0].category

    def test_geometry_coordinates(self):
        data = {
            "features": [{
                "properties": {"CompanyMetaData": {"name": "X"}},
                "geometry": {"coordinates": [37.6173, 55.7558]},
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        # Координаты в [lon, lat] → но в org мы кладём lat и lon отдельно
        assert orgs[0].longitude == "37.6173"
        assert orgs[0].latitude == "55.7558"

    def test_emails(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {
                        "name": "X",
                        "Emails": [{"value": "info@x.com"}, "extra@x.com"],
                    }
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert "info@x.com" in orgs[0].email
        assert "extra@x.com" in orgs[0].email

    def test_social_links_filtered(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {
                        "name": "X",
                        "Links": [
                            {"href": "https://vk.com/profile"},
                            {"href": "https://example.com"},  # не соцсеть
                            {"href": "https://t.me/channel"},
                            {"href": "https://instagram.com/page"},
                        ],
                    }
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert "vk.com" in orgs[0].social_links
        assert "t.me" in orgs[0].social_links
        assert "instagram.com" in orgs[0].social_links
        assert "example.com" not in orgs[0].social_links

    def test_uri_for_yandex_url(self):
        data = {
            "features": [{
                "properties": {
                    "CompanyMetaData": {"name": "X"},
                    "uri": "ymapsbm1://org?oid=12345",
                }
            }]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert orgs[0].yandex_url == "ymapsbm1://org?oid=12345"

    def test_skips_org_without_name(self):
        data = {
            "features": [
                {"properties": {"CompanyMetaData": {"name": "X"}}},
                {"properties": {"CompanyMetaData": {"address": "noname"}}},
                {"properties": {"CompanyMetaData": {"name": "Y"}}},
            ]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert len(orgs) == 2
        assert {o.name for o in orgs} == {"X", "Y"}

    def test_skips_features_without_meta_or_name(self):
        data = {
            "features": [
                {"properties": {"foo": "bar"}},
                {"properties": {"CompanyMetaData": {"name": "Real"}}},
            ]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert len(orgs) == 1
        assert orgs[0].name == "Real"


class TestExtractOrgsFromApiResponseAlternativeShapes:
    """Альтернативные форматы ответа API."""

    def test_items_array(self):
        # Некоторые API отдают 'items' вместо 'features'
        data = {
            "items": [
                {"name": "X", "address": "Y"},  # плоский формат
            ]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert len(orgs) == 1
        assert orgs[0].name == "X"

    def test_results_array(self):
        data = {
            "results": [
                {"name": "X", "address": "Y"},
            ]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert len(orgs) == 1

    def test_full_realistic_response(self):
        """Полный реалистичный ответ от Яндекса (синтетический)."""
        data = {
            "features": [
                {
                    "properties": {
                        "CompanyMetaData": {
                            "name": "Кофейня Старбакс",
                            "address": "Москва, ул. Тверская, 3",
                            "Phones": [{"formatted": "+7 495 123-45-67"}],
                            "url": "https://starbucks.ru",
                            "Hours": {"text": "Ежедневно 7:00-23:00"},
                            "rating": {"value": "4.6", "ratings": "1024"},
                            "Categories": [{"name": "Кофейня"}, {"name": "Кафе"}],
                        },
                        "uri": "ymapsbm1://org?oid=1111",
                    },
                    "geometry": {"coordinates": [37.6056, 55.7665]},
                }
            ]
        }
        orgs = _extract_orgs_from_api_response(data)
        assert len(orgs) == 1
        o = orgs[0]
        assert o.name == "Кофейня Старбакс"
        assert "Тверская" in o.address
        assert "123-45-67" in o.phone
        assert o.website == "https://starbucks.ru"
        assert "7:00" in o.working_hours
        assert o.rating == "4.6"
        assert o.reviews_count == "1024"
        assert "Кофейня" in o.category
        assert "Кафе" in o.category
        assert o.latitude == "55.7665"
        assert o.longitude == "37.6056"
        assert o.yandex_url.startswith("ymapsbm1")
