"""Тесты публичного API пакета и шим-файла."""

from __future__ import annotations

import importlib

import yamap


class TestPublicApi:
    def test_organization_exposed(self):
        assert hasattr(yamap, "Organization")
        org = yamap.Organization(name="X")
        assert org.name == "X"

    def test_run_parser_exposed(self):
        assert callable(yamap.run_parser)

    def test_run_category_parser_exposed(self):
        assert callable(yamap.run_category_parser)

    def test_helpers_exposed(self):
        assert callable(yamap.dedup_key)
        assert callable(yamap.normalize_for_dedup)

    def test_constants_exposed(self):
        assert isinstance(yamap.FIELDNAMES, list)
        assert isinstance(yamap.HEADERS_RU, dict)

    def test_all_attribute_complete(self):
        # Все имена в __all__ действительно экспортированы
        for name in yamap.__all__:
            assert hasattr(yamap, name), f"Missing exported name: {name}"


class TestModulesImportable:
    """Все модули пакета импортируются без ошибок."""

    def test_models(self):
        importlib.import_module("yamap.models")

    def test_proxy(self):
        importlib.import_module("yamap.proxy")

    def test_categories(self):
        importlib.import_module("yamap.categories")

    def test_config(self):
        importlib.import_module("yamap.config")

    def test_captcha(self):
        importlib.import_module("yamap.captcha")

    def test_selectors(self):
        importlib.import_module("yamap.selectors")

    def test_throttle(self):
        importlib.import_module("yamap.throttle")

    def test_browser(self):
        importlib.import_module("yamap.browser")

    def test_scraper(self):
        importlib.import_module("yamap.scraper")

    def test_output(self):
        importlib.import_module("yamap.output")

    def test_resume(self):
        importlib.import_module("yamap.resume")

    def test_runner(self):
        importlib.import_module("yamap.runner")

    def test_cli(self):
        importlib.import_module("yamap.cli")

    def test_validators(self):
        importlib.import_module("yamap.validators")

    def test_metrics(self):
        importlib.import_module("yamap.metrics")

    def test_logging_utils(self):
        importlib.import_module("yamap.logging_utils")


class TestShim:
    def test_yandex_parser_imports_main(self):
        # Шим-файл yandex_parser.py должен переэкспортировать main
        import yandex_parser
        from yamap.cli import main
        assert yandex_parser.main is main

    def test_dunder_main_module_runs(self):
        # python -m yamap должен использовать тот же main
        from yamap import __main__ as m
        from yamap.cli import main
        assert m.main is main
