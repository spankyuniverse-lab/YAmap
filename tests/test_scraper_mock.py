"""Тесты scraper через mock Page — покрывают DOM-логику без браузера.

Проверяем parse_snippet, _do_search, _human_scroll, enrich_from_detail
с моками Playwright Locator/Page.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from yamap.models import Organization
from yamap.scraper import (
    _click_show_more,
    _do_scroll_step,
    _find_scroll_container,
    enrich_from_detail,
    parse_snippet,
)
from yamap.selectors import SelectorEngine


@pytest.fixture
def fresh_engine(tmp_path, monkeypatch):
    """Свежий SelectorEngine с пустым cache, hardcoded-селекторами в _active."""
    fake_cache = tmp_path / "cache.json"
    monkeypatch.setattr("yamap.selectors.SELECTORS_CACHE_FILE", fake_cache)
    engine = SelectorEngine(fake_cache)
    # Заполняем _active hardcoded-значениями (имитация loaded state)
    from yamap.selectors import _HARDCODED, SELECTOR_KEYS
    for key in SELECTOR_KEYS:
        if key in _HARDCODED:
            engine._active[key] = _HARDCODED[key]
    monkeypatch.setattr("yamap.scraper.get_selector_engine", lambda: engine)
    yield engine


class _FakeLocator:
    """Минимальная имитация Playwright Locator."""

    def __init__(self, text: str = "", count: int = 1, href: str | None = None,
                 nested: dict | None = None, visible: bool = True):
        self._text = text
        self._count = count
        self._href = href
        self._nested = nested or {}
        self._visible = visible

    def count(self) -> int:
        return self._count

    @property
    def first(self):
        return self

    def nth(self, idx: int):
        return self

    def inner_text(self) -> str:
        return self._text

    def get_attribute(self, name: str) -> str | None:
        if name == "href":
            return self._href
        return None

    def is_visible(self) -> bool:
        return self._visible

    def click(self) -> None:
        pass

    def evaluate(self, *args, **kwargs):
        pass

    def locator(self, sel: str):
        return self._nested.get(sel, _FakeLocator(count=0))


class _FakePage:
    """Минимальная имитация Playwright Page."""

    def __init__(self, locators: dict | None = None, eval_returns=None,
                 url: str = "https://yandex.ru/maps/"):
        self._locators = locators or {}
        self._eval_returns = eval_returns or {}
        self.url = url
        self.mouse = MagicMock()
        self._call_log: list[tuple[str, tuple]] = []

    def locator(self, sel: str):
        return self._locators.get(sel, _FakeLocator(count=0))

    def evaluate(self, script: str, *args):
        self._call_log.append(("evaluate", (script[:80],)))
        # Сравнение по началу строки
        for prefix, value in self._eval_returns.items():
            if script.startswith(prefix) or prefix in script:
                return value
        return None

    def goto(self, url: str, **kwargs):
        self.url = url

    def wait_for_timeout(self, ms: int):
        pass

    def wait_for_selector(self, sel: str, timeout: int = 0):
        if sel in self._locators and self._locators[sel].count() > 0:
            return self._locators[sel]
        raise TimeoutError(f"selector {sel} not found")


class TestParseSnippet:
    def test_parses_full_snippet(self, fresh_engine):
        engine = fresh_engine
        item_sel = engine.get("item")
        title_sel = engine.get("snippet_title")
        addr_sel = engine.get("snippet_address")
        cat_sel = engine.get("snippet_category")
        rating_sel = engine.get("snippet_rating")
        reviews_sel = engine.get("snippet_reviews")
        hours_sel = engine.get("snippet_hours")
        link_sel = engine.get("link")

        # card.locator(...) возвращает разные локаторы для разных селекторов
        card = _FakeLocator(count=1, nested={
            title_sel: _FakeLocator(text="Кофейня"),
            addr_sel: _FakeLocator(text="ул. Ленина, 5"),
            cat_sel: _FakeLocator(text="Кафе"),
            rating_sel: _FakeLocator(text="4.5"),
            reviews_sel: _FakeLocator(text="120 отзывов"),
            hours_sel: _FakeLocator(text="9:00-22:00"),
            link_sel: _FakeLocator(href="/maps/org/12345/"),
        })
        page = _FakePage(locators={item_sel: card})

        org = parse_snippet(page, 0)
        assert org.name == "Кофейня"
        assert org.address == "ул. Ленина, 5"
        assert org.category == "Кафе"
        assert org.rating == "4.5"
        assert org.reviews_count == "120"  # цифры из "120 отзывов"
        assert org.working_hours == "9:00-22:00"
        assert org.yandex_url == "/maps/org/12345/"

    def test_handles_missing_optional_fields(self, fresh_engine):
        engine = fresh_engine
        item_sel = engine.get("item")
        title_sel = engine.get("snippet_title")

        card = _FakeLocator(count=1, nested={
            title_sel: _FakeLocator(text="Только название"),
            # Все остальные локаторы по умолчанию count=0
        })
        page = _FakePage(locators={item_sel: card})

        org = parse_snippet(page, 0)
        assert org.name == "Только название"
        assert org.address == ""
        assert org.rating == ""

    def test_reviews_count_extracts_digits(self, fresh_engine):
        engine = fresh_engine
        reviews_sel = engine.get("snippet_reviews")
        title_sel = engine.get("snippet_title")
        item_sel = engine.get("item")

        card = _FakeLocator(count=1, nested={
            title_sel: _FakeLocator(text="X"),
            reviews_sel: _FakeLocator(text="1 234 отзыва"),
        })
        page = _FakePage(locators={item_sel: card})
        org = parse_snippet(page, 0)
        assert org.reviews_count == "1234"

    def test_normalize_applied(self, fresh_engine):
        # parse_snippet вызывает normalize_org в конце — провряем побочно
        engine = fresh_engine
        title_sel = engine.get("snippet_title")
        item_sel = engine.get("item")

        card = _FakeLocator(count=1, nested={
            title_sel: _FakeLocator(text="X"),
        })
        page = _FakePage(locators={item_sel: card})
        org = parse_snippet(page, 0)
        # Просто факт что не упало и normalize отработал
        assert isinstance(org, Organization)


class TestFindScrollContainer:
    def test_returns_cached_when_works(self, fresh_engine):
        engine = fresh_engine
        sel = engine.get("scroll_container")  # hardcoded
        # Локатор найдёт элемент
        page = _FakePage(locators={sel: _FakeLocator(count=1)})
        result = _find_scroll_container(page)
        assert result == sel

    def test_falls_back_to_js_detect(self, fresh_engine):
        engine = fresh_engine
        # Hardcoded селектор не находит ничего
        page = _FakePage(
            locators={engine.get("scroll_container"): _FakeLocator(count=0)},
            eval_returns={"() => {": "[class*='detected-scroll']"},
        )
        result = _find_scroll_container(page)
        assert result == "[class*='detected-scroll']"

    def test_returns_none_when_nothing_found(self, fresh_engine):
        engine = fresh_engine
        page = _FakePage(
            locators={engine.get("scroll_container"): _FakeLocator(count=0)},
            eval_returns={},  # JS возвращает None
        )
        result = _find_scroll_container(page)
        assert result is None


class TestDoScrollStep:
    def test_uses_container_when_available(self, fresh_engine):
        loc = MagicMock()
        loc.count = MagicMock(return_value=1)
        loc.evaluate = MagicMock()
        page = _FakePage(locators={"#container": _FakeLocator(count=1)})
        # Подменим page.locator
        page.locator = lambda sel: loc if sel == "#container" else _FakeLocator(count=0)
        # Мокаем .first
        loc.first = loc

        _do_scroll_step(page, "#container", 100)
        loc.evaluate.assert_called_once()
        # Не должен трогать mouse.wheel
        assert page.mouse.wheel.call_count == 0

    def test_falls_back_to_mouse_wheel(self, fresh_engine):
        page = _FakePage()
        _do_scroll_step(page, None, 100)
        page.mouse.wheel.assert_called_once_with(0, 100)


class TestClickShowMore:
    def test_clicks_when_visible(self, fresh_engine):
        engine = fresh_engine
        sel = engine.get("show_more")
        btn = MagicMock()
        btn.count = MagicMock(return_value=1)
        btn.is_visible = MagicMock(return_value=True)
        btn.click = MagicMock()
        loc = MagicMock()
        loc.first = btn
        page = _FakePage()
        page.locator = lambda s: loc if s == sel else _FakeLocator(count=0)

        result = _click_show_more(page)
        assert result is True
        btn.click.assert_called_once()

    def test_returns_false_when_not_visible(self, fresh_engine):
        engine = fresh_engine
        sel = engine.get("show_more")
        btn = MagicMock()
        btn.count = MagicMock(return_value=1)
        btn.is_visible = MagicMock(return_value=False)
        loc = MagicMock()
        loc.first = btn
        page = _FakePage()
        page.locator = lambda s: loc if s == sel else _FakeLocator(count=0)

        assert _click_show_more(page) is False

    def test_returns_false_when_not_present(self, fresh_engine):
        page = _FakePage()
        assert _click_show_more(page) is False

    def test_returns_false_when_selector_empty(self, fresh_engine, monkeypatch):
        engine = fresh_engine
        engine._active["show_more"] = ""
        page = _FakePage()
        assert _click_show_more(page) is False


class TestEnrichFromDetail:
    def test_no_url_returns_unchanged(self, fresh_engine):
        org = Organization(name="X", yandex_url="")
        page = _FakePage()
        result = enrich_from_detail(page, org)
        assert result is org
        assert result.phone == ""

    def test_extracts_phone_from_text(self, fresh_engine):
        engine = fresh_engine
        phone_sel = engine.get("detail_phone")
        page = _FakePage(
            locators={phone_sel: _FakeLocator(text="+7 495 123-45-67")},
            url="https://yandex.ru/maps/org/123",
        )
        org = Organization(name="X", yandex_url="https://yandex.ru/maps/org/123")
        # detect_detail в SelectorEngine может ломаться без локаторов;
        # передадим _detail_detected=[True] чтобы пропустить detect
        result = enrich_from_detail(page, org, _detail_detected=[True])
        # normalize_phone сохраняет оригинальное форматирование
        assert "123-45-67" in result.phone

    def test_extracts_phone_from_tel_href(self, fresh_engine):
        engine = fresh_engine
        phone_sel = engine.get("detail_phone")
        page = _FakePage(
            locators={phone_sel: _FakeLocator(text="", href="tel:+74951234567")},
        )
        org = Organization(name="X", yandex_url="/maps/org/123")
        result = enrich_from_detail(page, org, _detail_detected=[True])
        assert "+74951234567" in result.phone or "+7" in result.phone

    def test_relative_url_converted_to_absolute(self, fresh_engine):
        page = _FakePage()
        # Регистрируем мок goto для проверки что URL развёрнут
        goto_calls = []
        page.goto = lambda url, **kwargs: goto_calls.append(url)
        org = Organization(name="X", yandex_url="/maps/org/123")
        enrich_from_detail(page, org, _detail_detected=[True])
        assert goto_calls and goto_calls[0].startswith("https://yandex.ru")

    def test_extracts_coords_from_url(self, fresh_engine):
        # После goto Яндекс перенаправляет на URL с ll-параметром
        page = _FakePage()
        page.goto = lambda *a, **kw: setattr(
            page, "url", "https://yandex.ru/maps/org/123/?ll=37.6173%2C55.7558",
        )
        org = Organization(name="X", yandex_url="https://yandex.ru/maps/org/123")
        result = enrich_from_detail(page, org, _detail_detected=[True])
        assert result.latitude == "55.7558"
        assert result.longitude == "37.6173"

    def test_extracts_emails_from_evaluate(self, fresh_engine):
        page = _FakePage(eval_returns={
            "const links = document.querySelectorAll('a[href^=\"mailto:\"]')":
                ["info@example.com", "support@example.com"],
        })
        # Селектор detail_phone пустой → пропустим
        org = Organization(name="X", yandex_url="/maps/org/123")
        result = enrich_from_detail(page, org, _detail_detected=[True])
        # Email должен быть извлечён через evaluate-блок
        assert "info@example.com" in result.email or "@example.com" in result.email

    def test_extracts_social_links(self, fresh_engine):
        page = _FakePage(eval_returns={
            "const patterns = [": [
                "https://vk.com/page",
                "https://t.me/channel",
            ],
        })
        org = Organization(name="X", yandex_url="/maps/org/123")
        result = enrich_from_detail(page, org, _detail_detected=[True])
        assert "vk.com" in result.social_links
        assert "t.me" in result.social_links

    def test_handles_goto_exception_gracefully(self, fresh_engine):
        page = _FakePage()

        def boom(*a, **kw):
            raise RuntimeError("network error")

        page.goto = boom
        org = Organization(name="X", yandex_url="/maps/org/123")
        # Не должно вылететь
        result = enrich_from_detail(page, org, _detail_detected=[True])
        assert isinstance(result, Organization)
