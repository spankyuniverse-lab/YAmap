"""
Парсер Яндекс.Карт — сбор организаций по поисковому запросу.

Яндекс.Карты используют динамическую подгрузку результатов (infinite scroll),
поэтому для парсинга применяется Playwright с эмуляцией прокрутки.

Использование:
    python yandex_parser.py "кофейни Москва"
    python yandex_parser.py "автосервис Казань" --max-results 200 --output авто.xlsx
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Playwright,
    sync_playwright,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("yandex_parser")

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Organization:
    name: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    rating: str = ""
    reviews_count: str = ""
    category: str = ""
    working_hours: str = ""
    yandex_url: str = ""


FIELDNAMES = [f.name for f in fields(Organization)]

# ---------------------------------------------------------------------------
# Selectors — Яндекс.Карты часто меняет CSS-классы, поэтому мы используем
# data-атрибуты и xpath-запросы, которые стабильнее.
# ---------------------------------------------------------------------------

# Панель результатов поиска (левая колонка)
SEARCH_LIST_SEL = "ul.search-list-view__list"
# Один элемент (карточка) в результатах
ITEM_SEL = "li.search-snippet-view"
# Кнопка «Показать ещё» (иногда появляется вместо бесконечного скролла)
SHOW_MORE_SEL = "div.search-list-view__show-more button, a.search-snippet-view__show-more"
# Ссылка на карточку организации
LINK_SEL = "a.search-snippet-view__link-overlay"


# ---------------------------------------------------------------------------
# Scroll helpers
# ---------------------------------------------------------------------------

def _scroll_search_panel(page: Page, delta: int = 800) -> None:
    """Прокрутить панель результатов поиска вниз."""
    panel = page.locator(".scroll__container").first
    if panel.count() == 0:
        # fallback — прокрутить всю страницу
        page.mouse.wheel(0, delta)
        return
    panel.evaluate(f"el => el.scrollTop += {delta}")


def _get_loaded_count(page: Page) -> int:
    """Вернуть количество подгруженных сниппетов."""
    return page.locator(ITEM_SEL).count()


def _click_show_more(page: Page) -> bool:
    """Нажать кнопку «Показать ещё», если она видна. Вернуть True при клике."""
    btn = page.locator(SHOW_MORE_SEL).first
    if btn.count() > 0 and btn.is_visible():
        btn.click()
        page.wait_for_timeout(1500)
        return True
    return False


# ---------------------------------------------------------------------------
# Loading all results
# ---------------------------------------------------------------------------

def load_all_results(page: Page, max_results: int, scroll_pause: float = 1.0) -> int:
    """Скроллить панель результатов, пока не загрузятся все или max_results."""
    prev_count = 0
    stale_rounds = 0
    max_stale = 8  # сколько раз подряд число не меняется — считаем, что всё

    while True:
        cur_count = _get_loaded_count(page)
        if cur_count >= max_results:
            log.info("Достигнут лимит: %d / %d", cur_count, max_results)
            break

        if cur_count == prev_count:
            # Пробуем кнопку «Показать ещё»
            if not _click_show_more(page):
                stale_rounds += 1
            else:
                stale_rounds = 0
        else:
            stale_rounds = 0
            log.info("Загружено сниппетов: %d", cur_count)

        if stale_rounds >= max_stale:
            log.info("Новые результаты не появляются, завершаем скролл (всего %d)", cur_count)
            break

        prev_count = cur_count
        _scroll_search_panel(page)
        page.wait_for_timeout(int(scroll_pause * 1000))

    return _get_loaded_count(page)


# ---------------------------------------------------------------------------
# Extracting data from a single card
# ---------------------------------------------------------------------------

def _text(page: Page, selector: str, parent: str = "") -> str:
    """Безопасно извлечь текст из элемента."""
    full = f"{parent} {selector}".strip() if parent else selector
    loc = page.locator(full).first
    if loc.count() > 0:
        return loc.inner_text().strip()
    return ""


def parse_card(page: Page, card_sel: str, index: int) -> Organization:
    """Извлечь данные из одного сниппета в списке."""
    card = f"{card_sel}:nth-child({index + 1})"
    org = Organization()

    # Название
    org.name = _text(page, ".search-business-snippet-view__title", card)
    if not org.name:
        org.name = _text(page, ".search-snippet-view__title", card)

    # Адрес
    org.address = _text(page, ".search-business-snippet-view__address", card)
    if not org.address:
        org.address = _text(page, ".search-snippet-view__body", card)

    # Категория
    org.category = _text(page, ".search-business-snippet-view__category", card)

    # Рейтинг
    rating_el = page.locator(f"{card} .business-rating-badge-view__rating-text").first
    if rating_el.count() > 0:
        org.rating = rating_el.inner_text().strip()

    # Кол-во отзывов
    reviews_el = page.locator(f"{card} .business-rating-badge-view__rating-count").first
    if reviews_el.count() > 0:
        raw = reviews_el.inner_text().strip()
        org.reviews_count = re.sub(r"[^\d]", "", raw)

    # Время работы
    org.working_hours = _text(page, ".search-business-snippet-view__open-hours", card)

    # Ссылка
    link = page.locator(f"{card} {LINK_SEL}").first
    if link.count() > 0:
        org.yandex_url = link.get_attribute("href") or ""

    return org


# ---------------------------------------------------------------------------
# Detail page parsing (phone, website)
# ---------------------------------------------------------------------------

def enrich_from_detail(page: Page, org: Organization) -> Organization:
    """Открыть карточку организации и дополнить данные (телефон, сайт)."""
    if not org.yandex_url:
        return org

    url = org.yandex_url
    if url.startswith("/"):
        url = f"https://yandex.ru{url}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2000)

        # Телефон
        phone_el = page.locator(
            ".card-phones-view__phone-number,"
            " .orgpage-phones-view__phone-number,"
            " a[href^='tel:']"
        ).first
        if phone_el.count() > 0:
            org.phone = phone_el.inner_text().strip()
            if not org.phone:
                href = phone_el.get_attribute("href") or ""
                if href.startswith("tel:"):
                    org.phone = href[4:]

        # Сайт
        site_el = page.locator(
            ".card-feature-view__content a[href],"
            " .orgpage-feature-view__content a[href],"
            " .business-urls-view__text"
        ).first
        if site_el.count() > 0:
            org.website = site_el.inner_text().strip()
            if not org.website:
                org.website = site_el.get_attribute("href") or ""

        # Адрес (если не извлекли из списка)
        if not org.address:
            addr_el = page.locator(".orgpage-header-view__address, .card-title-view__subtitle").first
            if addr_el.count() > 0:
                org.address = addr_el.inner_text().strip()

    except Exception as exc:
        log.warning("Не удалось открыть карточку %s: %s", org.name, exc)

    return org


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def save_csv(orgs: list[Organization], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, delimiter=";")
        writer.writeheader()
        for org in orgs:
            writer.writerow(asdict(org))
    log.info("CSV сохранён: %s (%d записей)", path, len(orgs))


def save_xlsx(orgs: list[Organization], path: Path) -> None:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font
    except ImportError:
        log.warning("openpyxl не установлен — сохраняю в CSV")
        save_csv(orgs, path.with_suffix(".csv"))
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Яндекс.Карты"

    headers_ru = {
        "name": "Название",
        "address": "Адрес",
        "phone": "Телефон",
        "website": "Сайт",
        "rating": "Рейтинг",
        "reviews_count": "Отзывы",
        "category": "Категория",
        "working_hours": "Часы работы",
        "yandex_url": "Ссылка",
    }

    for col, field in enumerate(FIELDNAMES, 1):
        cell = ws.cell(row=1, column=col, value=headers_ru.get(field, field))
        cell.font = Font(bold=True)

    for row_idx, org in enumerate(orgs, 2):
        d = asdict(org)
        for col, field in enumerate(FIELDNAMES, 1):
            ws.cell(row=row_idx, column=col, value=d[field])

    # Автоширина
    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)

    wb.save(path)
    log.info("XLSX сохранён: %s (%d записей)", path, len(orgs))


# ---------------------------------------------------------------------------
# Main parser flow
# ---------------------------------------------------------------------------

def run_parser(
    query: str,
    max_results: int = 500,
    output: str = "results.xlsx",
    headless: bool = True,
    detail: bool = False,
    scroll_pause: float = 1.0,
) -> list[Organization]:
    """Главная функция парсера."""

    out_path = Path(output)

    with sync_playwright() as pw:
        browser: Browser = pw.chromium.launch(headless=headless)
        ctx: BrowserContext = browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="ru-RU",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page: Page = ctx.new_page()

        # 1. Перейти на Яндекс.Карты с запросом
        search_url = f"https://yandex.ru/maps/?text={query}"
        log.info("Открываю %s", search_url)
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # 2. Дождаться появления результатов
        try:
            page.wait_for_selector(ITEM_SEL, timeout=15000)
        except Exception:
            log.error("Результаты поиска не найдены. Проверьте запрос.")
            browser.close()
            return []

        # 3. Прокрутить для подгрузки всех результатов
        total = load_all_results(page, max_results, scroll_pause)
        log.info("Итого загружено сниппетов: %d", total)

        # 4. Спарсить каждый сниппет
        orgs: list[Organization] = []
        count = min(total, max_results)
        for i in range(count):
            org = parse_card(page, ITEM_SEL, i)
            if org.name:
                orgs.append(org)
            if (i + 1) % 50 == 0:
                log.info("Обработано карточек: %d / %d", i + 1, count)

        log.info("Извлечено организаций: %d", len(orgs))

        # 5. (Опционально) обогатить данные со страниц организаций
        if detail and orgs:
            log.info("Обогащаю данные с карточек организаций…")
            detail_page = ctx.new_page()
            for idx, org in enumerate(orgs):
                enrich_from_detail(detail_page, org)
                if (idx + 1) % 20 == 0:
                    log.info("Обогащено: %d / %d", idx + 1, len(orgs))
            detail_page.close()

        browser.close()

    # 6. Сохранить результат
    if out_path.suffix == ".csv":
        save_csv(orgs, out_path)
    else:
        save_xlsx(orgs, out_path)

    return orgs


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Парсер организаций с Яндекс.Карт (динамический скролл)",
    )
    parser.add_argument("query", help='Поисковый запрос, напр. "кофейни Москва"')
    parser.add_argument(
        "--max-results", "-n", type=int, default=500,
        help="Максимум организаций для сбора (по умолчанию 500)",
    )
    parser.add_argument(
        "--output", "-o", default="results.xlsx",
        help="Файл для сохранения (.xlsx или .csv)",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="Показывать браузер (для отладки)",
    )
    parser.add_argument(
        "--detail", action="store_true",
        help="Открывать карточку каждой организации для телефона/сайта (медленнее)",
    )
    parser.add_argument(
        "--scroll-pause", type=float, default=1.0,
        help="Пауза между прокрутками в секундах (по умолчанию 1.0)",
    )

    args = parser.parse_args()

    orgs = run_parser(
        query=args.query,
        max_results=args.max_results,
        output=args.output,
        headless=not args.no_headless,
        detail=args.detail,
        scroll_pause=args.scroll_pause,
    )

    print(f"\nГотово! Собрано {len(orgs)} организаций → {args.output}")


if __name__ == "__main__":
    main()
