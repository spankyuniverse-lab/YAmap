"""Тесты yamap.config — load_config."""

from __future__ import annotations

import json

import pytest

from yamap.config import DEFAULT_CONFIG, load_config


class TestLoadConfig:
    def test_missing_file_returns_empty(self, tmp_path):
        result = load_config(str(tmp_path / "nope.yaml"))
        assert result == {}

    def test_load_json(self, tmp_path):
        p = tmp_path / "cfg.json"
        p.write_text(json.dumps({"max_results": 100, "city": "Москва"}), encoding="utf-8")
        result = load_config(str(p))
        assert result["max_results"] == 100
        assert result["city"] == "Москва"

    def test_load_yaml_if_available(self, tmp_path):
        try:
            import yaml  # noqa: F401
        except ImportError:
            pytest.skip("PyYAML не установлен")
        p = tmp_path / "cfg.yaml"
        p.write_text("max_results: 200\ncity: Питер\n", encoding="utf-8")
        result = load_config(str(p))
        assert result["max_results"] == 200
        assert result["city"] == "Питер"

    def test_invalid_json_returns_empty(self, tmp_path):
        p = tmp_path / "bad.json"
        p.write_text("{broken", encoding="utf-8")
        result = load_config(str(p))
        assert result == {}

    def test_empty_file_returns_empty(self, tmp_path):
        p = tmp_path / "empty.json"
        p.write_text("", encoding="utf-8")
        result = load_config(str(p))
        assert result == {}


class TestDefaultConfig:
    def test_has_all_main_keys(self):
        for key in ("max_results", "scroll_pause", "headless", "detail",
                    "api_intercept", "output", "proxy", "city", "categories"):
            assert key in DEFAULT_CONFIG
