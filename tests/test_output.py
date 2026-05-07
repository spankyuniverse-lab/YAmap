"""Тесты yamap.output — save_csv/json/xlsx, print_stats."""

from __future__ import annotations

import csv
import json

import pytest

from yamap.models import Organization
from yamap.output import (
    make_incremental_saver,
    print_stats,
    save_auto,
    save_csv,
    save_json,
    save_xlsx,
    save_xlsx_by_categories,
)


@pytest.fixture
def sample_orgs():
    return [
        Organization(
            name="Кофейня A",
            address="ул. Ленина, 1",
            phone="+7 495 123",
            rating="4.5",
            category="кафе",
        ),
        Organization(
            name="Кофейня B",
            address="ул. Мира, 2",
            website="b.ru",
            rating="—",
            category="кофейни",
        ),
        Organization(name="Магазин C", address="пр. Победы, 3"),
    ]


class TestSaveCsv:
    def test_writes_file_with_bom(self, tmp_path, sample_orgs):
        p = tmp_path / "r.csv"
        save_csv(sample_orgs, p)
        # UTF-8 BOM для Excel
        raw = p.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")

    def test_roundtrip(self, tmp_path, sample_orgs):
        p = tmp_path / "r.csv"
        save_csv(sample_orgs, p)
        with open(p, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        assert len(rows) == 3
        assert rows[0]["name"] == "Кофейня A"
        assert rows[0]["phone"] == "+7 495 123"

    def test_creates_parent_dir(self, tmp_path, sample_orgs):
        p = tmp_path / "deep" / "nested" / "r.csv"
        save_csv(sample_orgs, p)
        assert p.exists()

    def test_empty_list_creates_header_only(self, tmp_path):
        p = tmp_path / "empty.csv"
        save_csv([], p)
        with open(p, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        assert rows == []


class TestSaveJson:
    def test_total_field(self, tmp_path, sample_orgs):
        p = tmp_path / "r.json"
        save_json(sample_orgs, p)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["total"] == 3
        assert len(data["organizations"]) == 3

    def test_unicode_preserved(self, tmp_path, sample_orgs):
        p = tmp_path / "r.json"
        save_json(sample_orgs, p)
        text = p.read_text(encoding="utf-8")
        assert "Кофейня" in text  # не \u-escaped

    def test_empty_list(self, tmp_path):
        p = tmp_path / "r.json"
        save_json([], p)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["total"] == 0
        assert data["organizations"] == []


class TestSaveXlsx:
    def test_creates_file(self, tmp_path, sample_orgs):
        pytest.importorskip("openpyxl")
        p = tmp_path / "r.xlsx"
        save_xlsx(sample_orgs, p)
        assert p.exists()

    def test_xlsx_has_russian_headers(self, tmp_path, sample_orgs):
        openpyxl = pytest.importorskip("openpyxl")
        p = tmp_path / "r.xlsx"
        save_xlsx(sample_orgs, p)
        wb = openpyxl.load_workbook(p, read_only=True)
        ws = wb.active
        headers = [c.value for c in next(ws.iter_rows(min_row=1, max_row=1))]
        assert "Название" in headers
        assert "Телефон" in headers
        wb.close()

    def test_xlsx_rows_contain_data(self, tmp_path, sample_orgs):
        openpyxl = pytest.importorskip("openpyxl")
        p = tmp_path / "r.xlsx"
        save_xlsx(sample_orgs, p)
        wb = openpyxl.load_workbook(p, read_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(min_row=2, values_only=True))
        assert len(rows) == 3
        names = [r[0] for r in rows]
        assert "Кофейня A" in names
        wb.close()


class TestSaveXlsxByCategories:
    def test_creates_sheet_per_category_plus_summary(self, tmp_path, sample_orgs):
        openpyxl = pytest.importorskip("openpyxl")
        p = tmp_path / "cats.xlsx"
        results = {
            "кофейни Москва": sample_orgs[:2],
            "магазины Москва": [sample_orgs[2]],
        }
        save_xlsx_by_categories(results, p)
        wb = openpyxl.load_workbook(p, read_only=True)
        sheets = wb.sheetnames
        assert "Все результаты" in sheets
        assert any("кофейни" in s for s in sheets)
        assert any("магазины" in s for s in sheets)
        wb.close()

    def test_skips_empty_category_sheets(self, tmp_path, sample_orgs):
        openpyxl = pytest.importorskip("openpyxl")
        p = tmp_path / "cats.xlsx"
        results = {"non_empty": sample_orgs, "empty": []}
        save_xlsx_by_categories(results, p)
        wb = openpyxl.load_workbook(p, read_only=True)
        # Лист 'empty' не создан
        assert "empty" not in wb.sheetnames
        wb.close()


class TestSaveAuto:
    def test_picks_csv_by_suffix(self, tmp_path, sample_orgs):
        p = tmp_path / "r.csv"
        save_auto(sample_orgs, p)
        assert p.exists()
        assert p.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_picks_json_by_suffix(self, tmp_path, sample_orgs):
        p = tmp_path / "r.json"
        save_auto(sample_orgs, p)
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["total"] == 3

    def test_default_xlsx(self, tmp_path, sample_orgs):
        pytest.importorskip("openpyxl")
        p = tmp_path / "r.xlsx"
        save_auto(sample_orgs, p)
        assert p.exists()


class TestPrintStats:
    def test_handles_empty_list(self, capsys):
        print_stats([])
        out = capsys.readouterr().out
        assert "0 организаций" in out

    def test_handles_garbage_ratings_without_crash(self, capsys):
        orgs = [
            Organization(name="A", rating="4.5"),
            Organization(name="B", rating="—"),
            Organization(name="C", rating="5/5"),
            Organization(name="D", rating="Н/Д"),
            Organization(name="E", rating=""),
            Organization(name="F", rating="4,7"),
            Organization(name="G", rating="3.2 (5 отзывов)"),
        ]
        # Не должно крашить
        print_stats(orgs)
        out = capsys.readouterr().out
        assert "Всего организаций:  7" in out
        # Среднее считается по 4 валидным (4.5, 5, 4.7, 3.2)
        assert "Средний рейтинг" in out

    def test_counts_with_phone_etc(self, capsys, sample_orgs):
        print_stats(sample_orgs)
        out = capsys.readouterr().out
        # 1 из 3 имеет телефон
        assert "С телефоном:        1" in out
        # 1 из 3 имеет сайт
        assert "С сайтом:           1" in out

    def test_top_categories(self, capsys):
        orgs = [
            Organization(name="A", category="кафе"),
            Organization(name="B", category="кафе"),
            Organization(name="C", category="ресторан"),
        ]
        print_stats(orgs)
        out = capsys.readouterr().out
        assert "Топ категории" in out
        assert "кафе: 2" in out


class TestIncrementalSaver:
    def test_returns_callback_and_list(self, tmp_path):
        p = tmp_path / "r.csv"
        on_org, all_orgs = make_incremental_saver(p, save_every=25)
        assert callable(on_org)
        assert all_orgs == []

    def test_appends_to_internal_list(self, tmp_path, sample_orgs):
        p = tmp_path / "r.csv"
        on_org, all_orgs = make_incremental_saver(p, save_every=10)
        for i, o in enumerate(sample_orgs, 1):
            on_org(o, i)
        assert len(all_orgs) == 3

    def test_saves_on_threshold(self, tmp_path):
        p = tmp_path / "r.csv"
        on_org, _ = make_incremental_saver(p, save_every=2)
        on_org(Organization(name="A"), 1)
        # Не сохранён после 1
        assert not p.exists()
        on_org(Organization(name="B"), 2)
        # Сохранён после 2-й
        assert p.exists()
