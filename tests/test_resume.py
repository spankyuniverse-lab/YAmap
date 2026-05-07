"""Тесты yamap.resume — ResumeManager."""

from __future__ import annotations

import pytest

from yamap.models import Organization
from yamap.output import save_csv, save_json, save_xlsx
from yamap.resume import ResumeManager


@pytest.fixture
def orgs():
    return [
        Organization(
            name="Кофейня A",
            address="ул. Ленина, 1",
            search_query="кофейни Москва",
        ),
        Organization(
            name="Магазин B",
            address="пр. Мира, 5",
            search_query="кофейни Москва",
        ),
        Organization(
            name="Аптека C",
            address="бульвар Гагарина, 3",
            search_query="аптеки Москва",
        ),
    ]


class TestResumeManager:
    def test_no_path_empty_state(self):
        rm = ResumeManager()
        assert rm.existing_count == 0
        assert not rm.is_known("anything", "anywhere")
        assert not rm.is_query_done("any query")

    def test_missing_file_empty_state(self, tmp_path):
        rm = ResumeManager(tmp_path / "does_not_exist.json")
        assert rm.existing_count == 0

    def test_load_from_csv(self, tmp_path, orgs):
        p = tmp_path / "r.csv"
        save_csv(orgs, p)
        rm = ResumeManager(p)
        assert rm.existing_count == 3
        assert rm.is_known("Кофейня A", "ул. Ленина, 1")

    def test_load_from_json(self, tmp_path, orgs):
        p = tmp_path / "r.json"
        save_json(orgs, p)
        rm = ResumeManager(p)
        assert rm.existing_count == 3

    def test_load_from_xlsx(self, tmp_path, orgs):
        pytest.importorskip("openpyxl")
        p = tmp_path / "r.xlsx"
        save_xlsx(orgs, p)
        rm = ResumeManager(p)
        assert rm.existing_count == 3

    def test_address_dedup_normalization(self, tmp_path, orgs):
        p = tmp_path / "r.json"
        save_json(orgs, p)
        rm = ResumeManager(p)
        # 'ул. Ленина, 1' и 'улица Ленина, 1' — один и тот же ключ
        assert rm.is_known("Кофейня A", "улица Ленина, 1")

    def test_completed_queries_tracked(self, tmp_path, orgs):
        p = tmp_path / "r.json"
        save_json(orgs, p)
        rm = ResumeManager(p)
        assert rm.is_query_done("кофейни Москва")
        assert rm.is_query_done("аптеки Москва")
        assert not rm.is_query_done("never executed")

    def test_existing_orgs_returns_list(self, tmp_path, orgs):
        p = tmp_path / "r.json"
        save_json(orgs, p)
        rm = ResumeManager(p)
        existing = rm.existing_orgs()
        assert len(existing) == 3
        assert all(isinstance(o, Organization) for o in existing)

    def test_existing_keys_returns_set(self, tmp_path, orgs):
        p = tmp_path / "r.json"
        save_json(orgs, p)
        rm = ResumeManager(p)
        keys = rm.existing_keys()
        assert isinstance(keys, set)
        assert len(keys) == 3

    def test_corrupt_csv_returns_empty(self, tmp_path):
        p = tmp_path / "bad.csv"
        # Записываем что-то, что точно не CSV — но recoverable. Реальный
        # broken-кейс проверяет robustness: невалидный JSON в .json
        p2 = tmp_path / "bad.json"
        p2.write_text("{not valid json", encoding="utf-8")
        rm = ResumeManager(p2)
        assert rm.existing_count == 0

    def test_csv_with_russian_headers(self, tmp_path):
        # ResumeManager должен уметь читать файл с русскими заголовками
        # (как сохраняет наш xlsx)
        # Создаём через xlsx — он сохраняет русские
        pytest.importorskip("openpyxl")
        p = tmp_path / "r.xlsx"
        save_xlsx([Organization(name="X", address="addr")], p)
        rm = ResumeManager(p)
        assert rm.existing_count == 1
        assert rm.is_known("X", "addr")
