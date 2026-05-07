"""Детекция и обработка CAPTCHA Яндекса."""

from __future__ import annotations

import logging
import random

from ._playwright import Page


log = logging.getLogger("yandex_parser")


_CAPTCHA_SELECTORS = [
    "[class*='captcha']",
    "[class*='Captcha']",
    "[class*='CheckboxCaptcha']",
    "#js-button",
    "[class*='smartcaptcha']",
    "iframe[src*='captcha']",
    "[class*='AdvancedCaptcha']",
]


def detect_captcha(page: Page) -> bool:
    """Проверить, показала ли страница CAPTCHA."""
    for sel in _CAPTCHA_SELECTORS:
        if page.locator(sel).count() > 0:
            return True
    if "showcaptcha" in page.url or "captcha" in page.url.lower():
        return True
    return False


def _bezier_mouse_move(page: Page, start_x: float, start_y: float,
                       end_x: float, end_y: float, steps: int = 15) -> None:
    """Двигаем мышь по кривой Безье — как человек.

    Прямые линии палятся как автоматизация. Человек двигает мышь по дуге
    с ускорением в начале и замедлением в конце.
    """
    ctrl_x = (start_x + end_x) / 2 + random.randint(-80, 80)
    ctrl_y = (start_y + end_y) / 2 + random.randint(-60, 60)

    for i in range(steps + 1):
        t = i / steps
        x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t ** 2 * end_x
        y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t ** 2 * end_y
        x += random.randint(-2, 2)
        y += random.randint(-2, 2)
        page.mouse.move(x, y)
        if i < 3 or i > steps - 3:
            page.wait_for_timeout(random.randint(20, 60))
        else:
            page.wait_for_timeout(random.randint(5, 20))


def _try_click_captcha(page: Page) -> bool:
    """Попробовать автоматически кликнуть 'Я не робот' (checkbox-капча)."""
    checkbox_sels = [
        "[class*='CheckboxCaptcha'] .CheckboxCaptcha-Button",
        "[class*='CheckboxCaptcha-Button']",
        "#js-button",
        "input[type='submit'][value*='робот']",
        "button:has-text('робот')",
        "[class*='captcha'] button",
        ".smartcaptcha input[type='checkbox']",
    ]
    for sel in checkbox_sels:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                box = btn.bounding_box()
                if box:
                    start_x = random.randint(200, 600)
                    start_y = random.randint(100, 400)
                    page.mouse.move(start_x, start_y)
                    page.wait_for_timeout(random.randint(200, 500))
                    target_x = box["x"] + box["width"] / 2 + random.randint(-3, 3)
                    target_y = box["y"] + box["height"] / 2 + random.randint(-2, 2)
                    _bezier_mouse_move(page, start_x, start_y, target_x, target_y)
                    page.wait_for_timeout(random.randint(100, 300))
                    page.mouse.click(target_x, target_y)
                else:
                    btn.click()
                log.info("Автоклик по кнопке капчи: %s", sel)
                page.wait_for_timeout(3000)
                return not detect_captcha(page)
        except Exception as exc:
            log.debug("Автоклик не сработал на селекторе %s: %s", sel, exc)
            continue
    return False


def handle_captcha(page: Page, headless: bool) -> bool:
    """Обработать CAPTCHA: автоклик → пауза/reload → ручное решение.

    Стратегия без платных сервисов:
    1. Автоклик checkbox «Я не робот» (Bezier-мышь)
    2. Пауза + reload — иногда капча протухает
    3. В headless: повторная пауза + reload; иначе — просим решить вручную

    Возвращает True если CAPTCHA решена, False если таймаут.
    """
    if not detect_captcha(page):
        return True

    log.warning("=" * 60)
    log.warning("ОБНАРУЖЕНА CAPTCHA!")

    if _try_click_captcha(page):
        log.info("CAPTCHA решена автокликом!")
        log.warning("=" * 60)
        return True

    log.info("Пауза 15-25 сек + reload (капча может протухнуть)…")
    page.wait_for_timeout(random.randint(15000, 25000))
    try:
        page.reload(wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(random.randint(3000, 5000))
        if not detect_captcha(page):
            log.info("CAPTCHA исчезла после паузы и reload!")
            log.warning("=" * 60)
            return True
        if _try_click_captcha(page):
            log.info("CAPTCHA решена автокликом после reload!")
            log.warning("=" * 60)
            return True
    except Exception as exc:
        log.debug("Reload капчи не удался: %s", exc)

    if headless:
        log.warning("Headless-режим: пауза 60 сек + reload…")
        page.wait_for_timeout(60000)
        try:
            page.reload(wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(5000)
            if not detect_captcha(page):
                log.info("CAPTCHA исчезла после паузы!")
                log.warning("=" * 60)
                return True
        except Exception as exc:
            log.debug("Headless reload не удался: %s", exc)
    else:
        log.warning("")
        log.warning("  >>> РЕШИТЕ КАПЧУ В БРАУЗЕРЕ! <<<")
        log.warning("  После решения парсер продолжит автоматически.")
        log.warning("  (persistent profile запомнит — следующий раз без капчи)")
        log.warning("")
        for i in range(60):  # 60 * 5 = 300 секунд (5 минут)
            page.wait_for_timeout(5000)
            if not detect_captcha(page):
                log.info("CAPTCHA решена! Продолжаем…")
                log.warning("=" * 60)
                return True
            if i > 0 and i % 12 == 0:
                log.info("Ожидание решения капчи… (%d сек)", i * 5)
        log.error("Таймаут ожидания решения CAPTCHA (5 мин)")

    log.warning("=" * 60)
    return not detect_captcha(page)
