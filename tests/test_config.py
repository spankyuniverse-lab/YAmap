"""Тесты: категории, конфигурация, ResumeManager."""

import json
import csv
import pytest
from pathlib import Path

from yandex_parser.config import (
    CATEGORIES,
    ResumeManager,
    load_config,
    resolve_categories,
)
from yandex_parser.models import FIELDNAMES, Organization


# ---------------------------------------------------------------------------
# resolve_categories
# ---------------------------------------------------------------------------


class TestResolveCategories:
    def test_group_name(self):
        result = resolve_categories(["еда"])
        assert "рестораны" in result
        assert "кафе" in result
        assert len(result) == len(CATEGORIES["еда"])

    def test_direct_query(self):
        result = resolve_categories(["рестораны"])
        assert result == ["рестораны"]

    def test_mixed(self):
        result = resolve_categories(["еда", "шиномонтаж"])
        assert "рестораны" in result
        assert "шиномонтаж" in result

    def test_multiple_groups(self):
        result = resolve_categories(["еда", "авто"])
        assert "рестораны" in result
        assert "автосервисы" in result

    def test_empty(self):
        assert resolve_categories([]) == []

    def test_case_insensitive(self):
        result = resolve_categories(["ЕДА"])
        # "ЕДА" не совпадёт с "еда" (key.lower() = "еда")
        # Нет, resolve_categories делает key = name.lower().strip()
        assert "рестораны" in result


# ---------------------------------------------------------------------------
# CATEGORIES structure
# ---------------------------------------------------------------------------


class TestCategories:
    def test_not_empty(self):
        assert len(CATEGORIES) > 0

    def test_all_lists(self):
        for group, items in CATEGORIES.items():
            assert isinstance(items, list), f"{group} is not a list"
            assert len(items) > 0, f"{group} is empty"

    def test_known_groups(self):
        assert "еда" in CATEGORIES
        assert "авто" in CATEGORIES
        assert "IT" in CATEGORIES


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_nonexistent(self, tmp_path):
        result = load_config(str(tmp_path / "nope.json"))
        assert result == {}

    def test_json(self, tmp_path):
        cfg = tmp_path / "config.json"
        cfg.write_text('{"max_results": 100, "headless": false}')
        result = load_config(str(cfg))
        assert result["max_results"] == 100
        assert result["headless"] is False

    def test_invalid_json(self, tmp_path):
        cfg = tmp_path / "bad.json"
        cfg.write_text("not json at all")
        result = load_config(str(cfg))
        assert result == {}


# ---------------------------------------------------------------------------
# ResumeManager
# ---------------------------------------------------------------------------


class TestResumeManager:
    def test_empty(self):
        rm = ResumeManager(None)
        assert rm.existing_count == 0
        assert rm.existing_orgs() == []

    def test_nonexistent_path(self, tmp_path):
        rm = ResumeManager(tmp_path / "nope.csv")
        assert rm.existing_count == 0

    def test_load_csv(self, tmp_path):
        csv_path = tmp_path / "data.csv"
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES, delimiter=";")
            writer.writeheader()
            writer.writerow({
                "name": "Кафе Рога",
                "address": "ул. Ленина, 5",
                "search_query": "кафе Москва",
                **{k: "" for k in FIELDNAMES if k not in ("name", "address", "search_query")},
            })
            writer.writerow({
                "name": "Бар Луна",
                "address": "пр-т Мира, 10",
                "search_query": "бар Москва",
                **{k: "" for k in FIELDNAMES if k not in ("name", "address", "search_query")},
            })

        rm = ResumeManager(csv_path)
        assert rm.existing_count == 2
        assert rm.is_known("Кафе Рога", "ул. Ленина, 5")
        assert rm.is_known("кафе рога", "улица Ленина 5")  # normalized
        assert not rm.is_known("Кафе Рога", "другой адрес")
        assert rm.is_query_done("кафе Москва")
        assert not rm.is_query_done("пиво Москва")

    def test_load_json(self, tmp_path):
        json_path = tmp_path / "data.json"
        data = {
            "organizations": [
                {"name": "Кафе Рога", "address": "ул. Ленина, 5", "search_query": "кафе Москва"},
            ]
        }
        json_path.write_text(json.dumps(data, ensure_ascii=False))

        rm = ResumeManager(json_path)
        assert rm.existing_count == 1
        assert rm.is_known("Кафе Рога", "ул. Ленина, 5")

    def test_existing_keys(self, tmp_path):
        json_path = tmp_path / "data.json"
        data = {
            "organizations": [
                {"name": "A", "address": "B"},
                {"name": "C", "address": "D"},
            ]
        }
        json_path.write_text(json.dumps(data))

        rm = ResumeManager(json_path)
        keys = rm.existing_keys()
        assert len(keys) == 2
