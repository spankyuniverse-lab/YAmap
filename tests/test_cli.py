"""Тесты CLI: argparse, dry-run sub-команды.

Не запускаем настоящий парсер; только проверяем команды, которые завершаются
до Playwright-сессии: --list-categories, --show-selectors, --reset-selectors,
--help.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from yamap.cli import main as cli_main
from yamap.selectors import SELECTORS_CACHE_FILE, SelectorCache


@pytest.fixture
def isolate_selectors_cache(tmp_path, monkeypatch):
    """Подменить путь к кешу селекторов на временный, чтобы тесты не трогали реальный."""
    fake_path = tmp_path / "selectors_cache.json"
    monkeypatch.setattr("yamap.selectors.SELECTORS_CACHE_FILE", fake_path)
    monkeypatch.setattr("yamap.cli.SELECTORS_CACHE_FILE", fake_path)
    # Сбрасываем глобальный SelectorEngine — он мог быть проинициализирован
    # в другом тесте с другим путём
    monkeypatch.setattr("yamap.selectors._selector_engine", None)
    yield fake_path


class TestCliHelp:
    def test_help_exits_zero(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["yandex_parser.py", "--help"])
        with pytest.raises(SystemExit) as exc:
            cli_main()
        assert exc.value.code == 0

    def test_help_lists_main_options(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["yandex_parser.py", "--help"])
        with pytest.raises(SystemExit):
            cli_main()
        out = capsys.readouterr().out
        for opt in ("--max-results", "--output", "--proxy", "--config",
                    "--log-file", "--all-categories", "--detect-selectors"):
            assert opt in out, f"Missing option in help: {opt}"


class TestListCategories:
    def test_lists_main_groups(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["yandex_parser.py", "--list-categories"])
        cli_main()
        out = capsys.readouterr().out
        assert "[еда]" in out
        assert "[авто]" in out
        assert "рестораны" in out
        assert "автосервисы" in out


class TestShowSelectors:
    def test_no_cache_shows_hardcoded(self, capsys, monkeypatch, isolate_selectors_cache):
        monkeypatch.setattr(sys, "argv", ["yandex_parser.py", "--show-selectors"])
        cli_main()
        out = capsys.readouterr().out
        assert "hardcoded" in out
        assert "item" in out
        # Все ключи должны попасть в вывод
        assert "scroll_container" in out
        assert "detail_phone" in out

    def test_with_cache_shows_cache_source(self, capsys, monkeypatch, isolate_selectors_cache):
        # Записываем кеш
        cache = SelectorCache(isolate_selectors_cache)
        cache.set("item", "[class*='custom-test-class']")
        cache.save()

        monkeypatch.setattr(sys, "argv", ["yandex_parser.py", "--show-selectors"])
        cli_main()
        out = capsys.readouterr().out
        assert "custom-test-class" in out
        assert "cache" in out


class TestResetSelectors:
    def test_removes_existing_cache(self, capsys, monkeypatch, isolate_selectors_cache):
        # Создаём кеш
        cache = SelectorCache(isolate_selectors_cache)
        cache.set("item", ".x")
        cache.save()
        assert isolate_selectors_cache.exists()

        monkeypatch.setattr(sys, "argv", ["yandex_parser.py", "--reset-selectors"])
        cli_main()
        out = capsys.readouterr().out
        assert not isolate_selectors_cache.exists()
        assert "удалён" in out.lower() or "удалены" in out.lower()

    def test_no_cache_message(self, capsys, monkeypatch, isolate_selectors_cache):
        # Кеша ещё нет
        assert not isolate_selectors_cache.exists()

        monkeypatch.setattr(sys, "argv", ["yandex_parser.py", "--reset-selectors"])
        cli_main()
        out = capsys.readouterr().out
        assert "не найден" in out.lower()


class TestArgumentParsing:
    """Проверяем что argparse корректно собирает аргументы (без запуска парсера)."""

    def test_query_with_n_and_o(self, monkeypatch):
        # Нет --query → если он передан позиционно
        called = {}

        def fake_run_parser(**kwargs):
            called.update(kwargs)
            return []

        monkeypatch.setattr("yamap.cli.run_parser", fake_run_parser)
        monkeypatch.setattr(sys, "argv", [
            "yandex_parser.py", "кофейни Москва",
            "-n", "50", "-o", "test.csv",
        ])
        cli_main()
        assert called["query"] == "кофейни Москва"
        assert called["max_results"] == 50
        assert called["output"] == "test.csv"

    def test_city_categories_calls_category_parser(self, monkeypatch):
        called = {}

        def fake_runner(**kwargs):
            called.update(kwargs)
            return {}

        monkeypatch.setattr("yamap.cli.run_category_parser", fake_runner)
        monkeypatch.setattr(sys, "argv", [
            "yandex_parser.py", "--city", "Питер",
            "--category", "еда", "авто",
            "-n", "100",
        ])
        cli_main()
        assert called["city"] == "Питер"
        assert called["categories"] == ["еда", "авто"]
        assert called["max_results_per_category"] == 100

    def test_all_categories_expands(self, monkeypatch):
        called = {}

        def fake_runner(**kwargs):
            called.update(kwargs)
            return {}

        monkeypatch.setattr("yamap.cli.run_category_parser", fake_runner)
        monkeypatch.setattr(sys, "argv", [
            "yandex_parser.py", "--city", "Москва", "--all-categories",
        ])
        cli_main()
        from yamap.categories import CATEGORIES
        assert called["categories"] == list(CATEGORIES.keys())

    def test_no_headless_flag(self, monkeypatch):
        called = {}

        def fake_run_parser(**kwargs):
            called.update(kwargs)
            return []

        monkeypatch.setattr("yamap.cli.run_parser", fake_run_parser)
        monkeypatch.setattr(sys, "argv", [
            "yandex_parser.py", "X", "--no-headless",
        ])
        cli_main()
        assert called["headless"] is False

    def test_detail_flag(self, monkeypatch):
        called = {}
        monkeypatch.setattr("yamap.cli.run_parser",
                            lambda **kw: called.update(kw) or [])
        monkeypatch.setattr(sys, "argv",
                            ["yandex_parser.py", "X", "--detail"])
        cli_main()
        assert called["detail"] is True

    def test_api_intercept_flag(self, monkeypatch):
        called = {}
        monkeypatch.setattr("yamap.cli.run_parser",
                            lambda **kw: called.update(kw) or [])
        monkeypatch.setattr(sys, "argv",
                            ["yandex_parser.py", "X", "--api-intercept"])
        cli_main()
        assert called["api_intercept"] is True

    def test_proxy_flag(self, monkeypatch):
        called = {}
        monkeypatch.setattr("yamap.cli.run_parser",
                            lambda **kw: called.update(kw) or [])
        monkeypatch.setattr(sys, "argv",
                            ["yandex_parser.py", "X", "--proxy", "http://h:1"])
        cli_main()
        assert called["proxy_url"] == "http://h:1"

    def test_resume_flag(self, monkeypatch, tmp_path):
        called = {}
        # Файл должен существовать чтобы Path был валидный
        resume_file = tmp_path / "prev.json"
        resume_file.write_text("{}", encoding="utf-8")

        monkeypatch.setattr("yamap.cli.run_parser",
                            lambda **kw: called.update(kw) or [])
        monkeypatch.setattr(sys, "argv", [
            "yandex_parser.py", "X", "--resume", str(resume_file),
        ])
        cli_main()
        assert called["resume_path"] == resume_file


class TestConfigFileLoading:
    def test_config_overrides_defaults(self, monkeypatch, tmp_path):
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({"output": "from-config.csv"}), encoding="utf-8")

        called = {}
        monkeypatch.setattr("yamap.cli.run_parser",
                            lambda **kw: called.update(kw) or [])
        monkeypatch.setattr(sys, "argv", [
            "yandex_parser.py", "X", "--config", str(cfg),
        ])
        cli_main()
        # output не передан в CLI → берётся из конфига
        assert called["output"] == "from-config.csv"

    def test_cli_arg_wins_over_config(self, monkeypatch, tmp_path):
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({"output": "config.csv"}), encoding="utf-8")

        called = {}
        monkeypatch.setattr("yamap.cli.run_parser",
                            lambda **kw: called.update(kw) or [])
        monkeypatch.setattr(sys, "argv", [
            "yandex_parser.py", "X",
            "--config", str(cfg),
            "-o", "explicit.csv",
        ])
        cli_main()
        # CLI > config
        assert called["output"] == "explicit.csv"


class TestLogFileFlag:
    def test_log_file_creates_file(self, monkeypatch, tmp_path):
        log_path = tmp_path / "test.log"
        called = {}

        def fake_run_parser(**kwargs):
            called.update(kwargs)
            import logging
            log = logging.getLogger("yandex_parser")
            log.info("from-test")
            for h in log.handlers:
                h.flush()
            return []

        monkeypatch.setattr("yamap.cli.run_parser", fake_run_parser)
        monkeypatch.setattr(sys, "argv", [
            "yandex_parser.py", "X", "--log-file", str(log_path),
        ])
        cli_main()
        assert log_path.exists()
        assert "from-test" in log_path.read_text(encoding="utf-8")

        # Чистим логгер после теста
        from yamap.logging_utils import setup_logging
        setup_logging()
