"""Тесты yamap.selectors — SelectorCache (без браузера)."""

from __future__ import annotations

import json

from yamap.selectors import SELECTOR_KEYS, SelectorCache, _HARDCODED


class TestSelectorCacheBasic:
    def test_empty_cache_no_file(self, tmp_path):
        cache = SelectorCache(tmp_path / "cache.json")
        assert cache.get("item") is None

    def test_set_and_get(self, tmp_path):
        cache = SelectorCache(tmp_path / "cache.json")
        cache.set("item", ".my-class")
        assert cache.get("item") == ".my-class"

    def test_save_creates_file(self, tmp_path):
        p = tmp_path / "cache.json"
        cache = SelectorCache(p)
        cache.set("item", ".x")
        cache.save()
        assert p.exists()
        data = json.loads(p.read_text(encoding="utf-8"))
        assert data["item"] == ".x"

    def test_load_from_existing_file(self, tmp_path):
        p = tmp_path / "cache.json"
        p.write_text(json.dumps({"item": ".cached"}), encoding="utf-8")
        cache = SelectorCache(p)
        assert cache.get("item") == ".cached"

    def test_corrupt_file_returns_empty(self, tmp_path):
        p = tmp_path / "cache.json"
        p.write_text("not json{{", encoding="utf-8")
        cache = SelectorCache(p)
        assert cache.get("item") is None
        assert cache.all() == {}

    def test_update_merges(self, tmp_path):
        cache = SelectorCache(tmp_path / "cache.json")
        cache.set("item", ".x")
        cache.update({"link": ".y", "show_more": ".z"})
        assert cache.get("item") == ".x"
        assert cache.get("link") == ".y"
        assert cache.get("show_more") == ".z"

    def test_all_returns_copy(self, tmp_path):
        cache = SelectorCache(tmp_path / "cache.json")
        cache.set("item", ".x")
        all_dict = cache.all()
        all_dict["item"] = ".modified"
        # Изменение копии не влияет на кеш
        assert cache.get("item") == ".x"

    def test_save_unicode_preserved(self, tmp_path):
        p = tmp_path / "cache.json"
        cache = SelectorCache(p)
        cache.set("item", "[class*='кафе']")
        cache.save()
        text = p.read_text(encoding="utf-8")
        assert "кафе" in text  # не \u-escaped


class TestHardcodedSelectors:
    def test_all_keys_have_hardcoded(self):
        # Все ключи из SELECTOR_KEYS должны иметь hardcoded значение
        for key in SELECTOR_KEYS:
            assert key in _HARDCODED, f"No hardcoded fallback for {key}"
            assert _HARDCODED[key], f"Empty hardcoded for {key}"
