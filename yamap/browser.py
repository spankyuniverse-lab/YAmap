"""Создание persistent-контекста, прогрев, человекоподобный ввод."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import Any

from ._playwright import BrowserContext, LIB_NAME, Page
from .captcha import detect_captcha, handle_captcha
from .proxy import get_proxy_rotator


log = logging.getLogger("yandex_parser")


# Минимальный stealth JS — используется ТОЛЬКО как fallback для обычного Playwright.
# С patchright + channel="chrome" этот скрипт НЕ инжектится.
_STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
}
"""


BROWSER_DATA_DIR = Path(".browser_profile")


def create_browser_context(pw, headless: bool, proxy_url: str | None = None):
    """Создать persistent browser context с настоящим Chrome."""
    launch_args: dict[str, Any] = {
        "channel": "chrome",
        "headless": headless,
        "no_viewport": True,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
        ],
        "locale": "ru-RU",
        "timezone_id": "Europe/Moscow",
        "color_scheme": "light",
    }
    if proxy_url:
        rotator = get_proxy_rotator()
        launch_args["proxy"] = rotator.to_playwright_arg(proxy_url)
        log.info("Прокси: %s", proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url)

    ctx: BrowserContext = pw.chromium.launch_persistent_context(
        str(BROWSER_DATA_DIR),
        **launch_args,
    )
    log.info("Браузер: настоящий Chrome + persistent-профиль (%s)", BROWSER_DATA_DIR)

    if LIB_NAME == "playwright":
        log.warning("patchright не установлен, используем playwright + stealth JS")
        ctx.add_init_script(_STEALTH_JS)

    return None, ctx


def setup_page(ctx: BrowserContext) -> Page:
    """Получить страницу из persistent-контекста."""
    for p in ctx.pages[1:]:
        try:
            p.close()
        except Exception as exc:
            log.debug("Не удалось закрыть лишнюю страницу: %s", exc)
    if ctx.pages:
        page = ctx.pages[0]
    else:
        page: Page = ctx.new_page()
    # НЕ блокируем Яндекс.Метрику — Яндекс проверяет что его трекеры загрузились.
    return page


_warmed_up = False


def reset_warmup() -> None:
    """Сбросить флаг прогрева (вызывать при старте нового контекста)."""
    global _warmed_up
    _warmed_up = False


def warmup(page: Page, headless: bool) -> None:
    """Прогрев: зайти на Яндекс как обычный пользователь перед парсингом.

    Стратегия: yandex.ru → maps. Естественная цепочка переходов
    с реферером yandex.ru вызывает меньше подозрений у антибота.
    """
    global _warmed_up
    if _warmed_up:
        return
    _warmed_up = True

    log.info("Прогрев: естественная навигация yandex.ru → карты…")
    try:
        page.goto("https://yandex.ru/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(random.randint(2000, 4000))

        if detect_captcha(page):
            handle_captcha(page, headless)

        for _ in range(random.randint(2, 4)):
            page.mouse.move(
                random.randint(100, 900),
                random.randint(100, 600),
            )
            page.wait_for_timeout(random.randint(100, 400))

        page.wait_for_timeout(random.randint(1000, 2500))
        page.goto("https://yandex.ru/maps/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(random.randint(2000, 4000))

        if detect_captcha(page):
            handle_captcha(page, headless)

        for _ in range(random.randint(3, 6)):
            page.mouse.move(
                random.randint(100, 900),
                random.randint(100, 700),
            )
            page.wait_for_timeout(random.randint(100, 400))

        page.mouse.click(random.randint(500, 900), random.randint(300, 600))
        page.wait_for_timeout(random.randint(1000, 2500))

        page.mouse.wheel(0, random.randint(-200, 200))
        page.wait_for_timeout(random.randint(500, 1500))

        if random.random() < 0.3:
            try:
                search_input = page.locator("input[class*='search'], input[class*='input']").first
                if search_input.count() > 0 and search_input.is_visible():
                    search_input.click()
                    page.wait_for_timeout(random.randint(300, 700))
                    page.mouse.click(random.randint(500, 900), random.randint(300, 600))
                    page.wait_for_timeout(random.randint(500, 1000))
            except Exception as exc:
                log.debug("Прогрев: фейковый клик в поиск не удался: %s", exc)

        log.info("Прогрев завершён")
    except Exception as exc:
        log.debug("Прогрев не удался: %s", exc)


def type_like_human(page: Page, selector: str, text: str) -> None:
    """Напечатать текст по буквам с человеческими задержками между символами."""
    el = page.locator(selector).first
    el.click()
    page.wait_for_timeout(random.randint(200, 500))
    el.press("Control+a")
    page.wait_for_timeout(random.randint(50, 150))
    el.press("Delete")
    page.wait_for_timeout(random.randint(100, 300))
    for char in text:
        el.press_sequentially(char, delay=random.randint(40, 120))
        if random.random() < 0.05:
            page.wait_for_timeout(random.randint(200, 600))
