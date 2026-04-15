"""Тесты: парсинг API-ответов (без браузера)."""

import sys
import types
import pytest

# Mock playwright/patchright чтобы scraper.py мог импортироваться без браузера
if "patchright" not in sys.modules:
    mock_pw = types.ModuleType("patchright")
    mock_sync = types.ModuleType("patchright.sync_api")
    mock_pw.sync_api = mock_sync
    sys.modules["patchright"] = mock_pw
    sys.modules["patchright.sync_api"] = mock_sync

from yandex_parser.models import Organization
from yandex_parser.scraper import _extract_orgs_from_api_response


# ---------------------------------------------------------------------------
# _extract_orgs_from_api_response
# ---------------------------------------------------------------------------


class TestExtractOrgsFromAPI:
    """Тест парсинга JSON-ответов Яндекс.Карт API."""

    def test_standard_format(self):
        """Стандартный формат с features."""
        data = {
            "features": [
                {
                    "properties": {
                        "CompanyMetaData": {
                            "name": "Кафе Рога",
                            "address": "ул. Ленина, 5",
                            "Phones": [
                                {"formatted": "+7 (495) 123-45-67"},
                                {"formatted": "+7 (495) 765-43-21"},
                            ],
                            "url": "roga-cafe.ru",
                            "Hours": {"text": "Пн-Вс 09:00-22:00"},
                            "Categories": [
                                {"name": "Кафе"},
                                {"name": "Ресторан"},
                            ],
                        },
                    },
                    "geometry": {
                        "coordinates": [37.6173, 55.7558],
                    },
                },
            ],
        }

        orgs = _extract_orgs_from_api_response(data)
        assert len(orgs) == 1
        org = orgs[0]
        assert org.name == "Кафе Рога"
        assert org.address == "ул. Ленина, 5"
        assert "+7 (495) 123-45-67" in org.phone
        assert "+7 (495) 765-43-21" in org.phone
        assert org.website == "roga-cafe.ru"
        assert org.working_hours == "Пн-Вс 09:00-22:00"
        assert "Кафе" in org.category
        assert org.longitude == "37.6173"
        assert org.latitude == "55.7558"

    def test_nested_data_format(self):
        """Формат с data.features."""
        data = {
            "data": {
                "features": [
                    {
                        "properties": {
                            "CompanyMetaData": {
                                "name": "Аптека",
                                "address": "пр-т Мира, 10",
                            },
                        },
                        "geometry": {"coordinates": [37.0, 55.0]},
                    },
                ],
            },
        }

        orgs = _extract_orgs_from_api_response(data)
        assert len(orgs) == 1
        assert orgs[0].name == "Аптека"

    def test_items_format(self):
        """Формат с items."""
        data = {
            "items": [
                {
                    "name": "Магазин",
                    "address": "ул. Пушкина, 1",
                },
            ],
        }

        orgs = _extract_orgs_from_api_response(data)
        assert len(orgs) == 1
        assert orgs[0].name == "Магазин"

    def test_rating_dict(self):
        """Рейтинг в виде dict."""
        data = {
            "features": [
                {
                    "properties": {
                        "CompanyMetaData": {
                            "name": "Ресторан",
                            "rating": {"value": 4.7, "ratings": 123},
                        },
                    },
                    "geometry": {"coordinates": []},
                },
            ],
        }

        orgs = _extract_orgs_from_api_response(data)
        assert orgs[0].rating == "4.7"
        assert orgs[0].reviews_count == "123"

    def test_empty_features(self):
        data = {"features": []}
        orgs = _extract_orgs_from_api_response(data)
        assert orgs == []

    def test_no_features(self):
        data = {"something": "else"}
        orgs = _extract_orgs_from_api_response(data)
        assert orgs == []

    def test_skip_without_name(self):
        """Записи без name отбрасываются."""
        data = {
            "features": [
                {
                    "properties": {
                        "CompanyMetaData": {"address": "somewhere"},
                    },
                    "geometry": {"coordinates": []},
                },
            ],
        }
        orgs = _extract_orgs_from_api_response(data)
        assert orgs == []

    def test_emails(self):
        data = {
            "features": [
                {
                    "properties": {
                        "CompanyMetaData": {
                            "name": "Фирма",
                            "Emails": [
                                {"value": "info@example.com"},
                                {"value": "sales@example.com"},
                            ],
                        },
                    },
                    "geometry": {"coordinates": []},
                },
            ],
        }
        orgs = _extract_orgs_from_api_response(data)
        assert "info@example.com" in orgs[0].email
        assert "sales@example.com" in orgs[0].email

    def test_social_links(self):
        data = {
            "features": [
                {
                    "properties": {
                        "CompanyMetaData": {
                            "name": "Бар",
                            "Links": [
                                {"href": "https://vk.com/bar"},
                                {"href": "https://t.me/bar"},
                                {"href": "https://example.com/other"},
                            ],
                        },
                    },
                    "geometry": {"coordinates": []},
                },
            ],
        }
        orgs = _extract_orgs_from_api_response(data)
        assert "vk.com/bar" in orgs[0].social_links
        assert "t.me/bar" in orgs[0].social_links
        assert "example.com/other" not in orgs[0].social_links
