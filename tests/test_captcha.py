"""Тесты yamap.captcha — детекция и обработка CAPTCHA через mock Page."""

from __future__ import annotations

from unittest.mock import MagicMock

from yamap.captcha import _bezier_mouse_move, detect_captcha, handle_captcha


class _FakeLocator:
    def __init__(self, count: int = 0, visible: bool = True, box: dict | None = None):
        self._count = count
        self._visible = visible
        self._box = box

    def count(self):
        return self._count

    @property
    def first(self):
        return self

    def is_visible(self):
        return self._visible

    def bounding_box(self):
        return self._box

    def click(self):
        pass


class _FakePage:
    def __init__(self, url: str = "https://yandex.ru/maps/", locators: dict | None = None):
        self.url = url
        self._locators = locators or {}
        self.mouse = MagicMock()
        self._reload_count = 0

    def locator(self, sel: str):
        return self._locators.get(sel, _FakeLocator(count=0))

    def wait_for_timeout(self, ms: int):
        pass

    def reload(self, **kwargs):
        self._reload_count += 1


class TestDetectCaptcha:
    def test_no_captcha_returns_false(self):
        page = _FakePage()
        assert detect_captcha(page) is False

    def test_captcha_class_detected(self):
        page = _FakePage(locators={
            "[class*='captcha']": _FakeLocator(count=1),
        })
        assert detect_captcha(page) is True

    def test_smartcaptcha_detected(self):
        page = _FakePage(locators={
            "[class*='smartcaptcha']": _FakeLocator(count=1),
        })
        assert detect_captcha(page) is True

    def test_iframe_captcha_detected(self):
        page = _FakePage(locators={
            "iframe[src*='captcha']": _FakeLocator(count=1),
        })
        assert detect_captcha(page) is True

    def test_url_with_showcaptcha(self):
        page = _FakePage(url="https://yandex.ru/showcaptcha?cc=1")
        assert detect_captcha(page) is True

    def test_url_with_captcha_substring(self):
        page = _FakePage(url="https://yandex.ru/some-page?has=captcha")
        assert detect_captcha(page) is True


class TestBezierMouseMove:
    def test_calls_mouse_move_multiple_times(self):
        page = _FakePage()
        _bezier_mouse_move(page, 0, 0, 100, 100, steps=10)
        # +1 потому что range(steps + 1)
        assert page.mouse.move.call_count == 11

    def test_starts_near_start_point(self):
        page = _FakePage()
        _bezier_mouse_move(page, 50, 50, 200, 200, steps=10)
        first_call = page.mouse.move.call_args_list[0]
        x, y = first_call[0]
        # Начало пути близко к (50, 50) с небольшим шумом ±2
        assert abs(x - 50) < 10
        assert abs(y - 50) < 10

    def test_ends_near_end_point(self):
        page = _FakePage()
        _bezier_mouse_move(page, 0, 0, 500, 500, steps=10)
        last_call = page.mouse.move.call_args_list[-1]
        x, y = last_call[0]
        # Конец пути близко к (500, 500)
        assert abs(x - 500) < 10
        assert abs(y - 500) < 10


class TestHandleCaptchaNoOp:
    def test_no_captcha_returns_true_immediately(self):
        page = _FakePage()
        assert handle_captcha(page, headless=True) is True

    def test_handles_captcha_resolved_after_reload(self):
        # Сначала есть капча, после reload — нет
        captcha_locator = _FakeLocator(count=1, visible=False)
        page = _FakePage(locators={
            "[class*='captcha']": captcha_locator,
        })

        reload_count = [0]
        original_reload = page.reload

        def reload_clearing(**kwargs):
            original_reload(**kwargs)
            # После первого reload убираем капчу
            reload_count[0] += 1
            if reload_count[0] >= 1:
                page._locators = {}
                page.url = "https://yandex.ru/maps/"

        page.reload = reload_clearing

        result = handle_captcha(page, headless=True)
        assert result is True
        assert reload_count[0] >= 1

    def test_handles_captcha_clears_via_url_change(self):
        # Капча обнаружена через URL
        page = _FakePage(url="https://yandex.ru/showcaptcha?cc=1")
        # После reload URL очищается
        original_reload = page.reload

        def reload_clearing(**kwargs):
            original_reload(**kwargs)
            page.url = "https://yandex.ru/maps/"

        page.reload = reload_clearing
        result = handle_captcha(page, headless=True)
        assert result is True
