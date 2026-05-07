"""Тесты yamap.categories — каталог + резолвинг."""

from __future__ import annotations

from yamap.categories import CATEGORIES, resolve_categories


class TestCategoriesCatalog:
    def test_has_main_groups(self):
        for group in ("еда", "здоровье", "авто", "красота"):
            assert group in CATEGORIES

    def test_each_group_non_empty(self):
        for group, items in CATEGORIES.items():
            assert items, f"Group {group} is empty"

    def test_no_duplicates_within_group(self):
        for group, items in CATEGORIES.items():
            assert len(items) == len(set(items)), f"Duplicates in {group}"


class TestResolveCategories:
    def test_expand_group(self):
        result = resolve_categories(["еда"])
        assert "рестораны" in result
        assert "кафе" in result
        assert len(result) == len(CATEGORIES["еда"])

    def test_expand_multiple_groups(self):
        result = resolve_categories(["еда", "авто"])
        assert "рестораны" in result
        assert "автосервисы" in result
        assert len(result) == len(CATEGORIES["еда"]) + len(CATEGORIES["авто"])

    def test_passthrough_unknown_name(self):
        # 'аптеки' — это не группа, а конкретный запрос; должен сохраниться
        result = resolve_categories(["аптеки"])
        assert result == ["аптеки"]

    def test_mixed_group_and_query(self):
        result = resolve_categories(["еда", "аптеки"])
        assert "аптеки" in result
        assert "рестораны" in result

    def test_case_insensitive_group_lookup(self):
        result = resolve_categories(["ЕДА", "Авто"])
        assert "рестораны" in result
        assert "автосервисы" in result

    def test_strips_whitespace(self):
        result = resolve_categories(["  еда  "])
        assert "рестораны" in result

    def test_empty_input(self):
        assert resolve_categories([]) == []
