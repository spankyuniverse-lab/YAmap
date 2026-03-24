"""
Парсер Яндекс.Карт — сбор организаций по поисковому запросу.

Яндекс.Карты используют динамическую подгрузку результатов (infinite scroll),
поэтому для парсинга применяется Playwright с эмуляцией прокрутки.

Два режима работы:
  1. DOM-парсинг (по умолчанию) — скролл + извлечение из HTML
  2. API-перехват (--api-intercept) — ловит JSON-ответы внутреннего API

Использование:
    python yandex_parser.py "кофейни Москва"
    python yandex_parser.py "автосервис Казань" --max-results 200 --output авто.xlsx
    python yandex_parser.py "аптеки Москва" --api-intercept
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    Browser,
    BrowserContext,
    Page,
    Response,
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
# Selectors
#
# Яндекс.Карты периодически добавляет числовой префикс к классам
# (напр. "_1a2b3c_search-snippet-view"), поэтому используем
# [class*="..."] — поиск по подстроке класса, что устойчивее.
#
# Селекторы верифицированы по открытым парсерам:
#   - github.com/artemsteshenko/parser_maps
#   - github.com/chernyshov-dp/YMapsGrabber
#   - github.com/erdzhemadinov/PARSING_DISTRIBUTION_CENTRES
# ---------------------------------------------------------------------------

# -- Список результатов (левая панель) --
# Поле ввода поиска
SEARCH_INPUT_SEL = "[class*='search-form-view__input'] input, input.input__control"
# Кнопка поиска
SEARCH_BUTTON_SEL = "[class*='small-search-form-view__button'], [class*='search-form-view'] button[type='submit']"

# Контейнер прокрутки результатов
SCROLL_CONTAINER_SEL = "[class*='scroll__container']"
# Ползунок скроллбара (для определения наличия прокрутки)
SCROLLBAR_THUMB_SEL = "[class*='scroll__scrollbar-thumb']"

# Один сниппет организации в списке результатов
ITEM_SEL = "[class*='search-snippet-view']"
# Ссылка-оверлей на карточку организации
LINK_SEL = "[class*='search-snippet-view__link-overlay']"
# Кнопка «Показать ещё»
SHOW_MORE_SEL = "[class*='show-more'] button, [class*='search-list-view__more'] button"

# -- Поля внутри сниппета (список результатов) --
SNIPPET_TITLE_SEL = "[class*='search-business-snippet-view__title']"
SNIPPET_ADDRESS_SEL = "[class*='search-business-snippet-view__address']"
SNIPPET_CATEGORY_SEL = "[class*='search-business-snippet-view__categories']"
SNIPPET_HOURS_SEL = "[class*='search-business-snippet-view__open-hours']"
SNIPPET_RATING_SEL = "[class*='business-rating-badge-view__rating-text'], [class*='business-summary-rating-badge-view__rating-text']"
SNIPPET_REVIEWS_SEL = "[class*='business-rating-badge-view__rating-count']"

# -- Карточка организации (detail page) --
DETAIL_NAME_SEL = "h1[class*='orgpage-header-view__header'], h1[class*='card-title-view__title']"
DETAIL_ADDRESS_SEL = "[class*='business-contacts-view__address-link'], [class*='orgpage-header-view__address'], [class*='card-title-view__subtitle']"
DETAIL_PHONE_SEL = "[class*='card-phones-view__phone-number'], [class*='orgpage-phones-view__phone-number'], a[href^='tel:']"
DETAIL_WEBSITE_SEL = "[class*='business-urls-view__text'], [class*='card-feature-view__content'] a[href], [class*='orgpage-feature-view__content'] a[href]"
DETAIL_RATING_SEL = "[class*='business-summary-rating-badge-view__rating-text'], [class*='business-rating-badge-view__rating-text']"
DETAIL_REVIEWS_COUNT_SEL = "[class*='tabs-select-view__counter']"
DETAIL_HOURS_SEL = "meta[itemprop='openingHours'], [class*='business-working-status-view__text']"
DETAIL_CATEGORY_SEL = "[class*='business-categories-view__category'], [class*='breadcrumbs-view__text']"


# ---------------------------------------------------------------------------
# Scroll helpers
# ---------------------------------------------------------------------------

def _find_scroll_container(page: Page) -> str | None:
    """Найти скроллящийся контейнер с результатами поиска."""
    # Стратегия 1: ищем по известному классу
    sel = SCROLL_CONTAINER_SEL
    loc = page.locator(sel)
    if loc.count() > 0:
        return sel

    # Стратегия 2: ищем любой элемент с overflow-y: auto/scroll и достаточной высотой
    container_sel = page.evaluate("""() => {
        const candidates = document.querySelectorAll('div, section, aside, ul');
        for (const el of candidates) {
            const style = getComputedStyle(el);
            if ((style.overflowY === 'auto' || style.overflowY === 'scroll') &&
                el.scrollHeight > el.clientHeight + 100 &&
                el.clientHeight > 200) {
                // Генерируем уникальный селектор
                if (el.id) return '#' + el.id;
                const classes = Array.from(el.classList).join('.');
                if (classes) return el.tagName.toLowerCase() + '.' + classes;
            }
        }
        return null;
    }""")
    return container_sel


def _scroll_search_panel(page: Page, container_sel: str | None, delta: int = 600) -> None:
    """Прокрутить панель результатов поиска вниз."""
    if container_sel:
        loc = page.locator(container_sel).first
        if loc.count() > 0:
            loc.evaluate(f"el => el.scrollTop += {delta}")
            return

    # Fallback: прокрутить колёсиком мыши в левой части экрана
    page.mouse.move(300, 450)
    page.mouse.wheel(0, delta)


def _get_loaded_count(page: Page) -> int:
    """Вернуть количество подгруженных сниппетов."""
    return page.locator(ITEM_SEL).count()


def _click_show_more(page: Page) -> bool:
    """Нажать кнопку «Показать ещё», если она видна."""
    btn = page.locator(SHOW_MORE_SEL).first
    if btn.count() > 0 and btn.is_visible():
        btn.click()
        page.wait_for_timeout(1500)
        return True
    return False


# ---------------------------------------------------------------------------
# Loading all results via scroll
# ---------------------------------------------------------------------------

def load_all_results(page: Page, max_results: int, scroll_pause: float = 1.0) -> int:
    """Скроллить панель результатов, пока не загрузятся все или max_results."""
    container_sel = _find_scroll_container(page)
    if container_sel:
        log.info("Скролл-контейнер: %s", container_sel)
    else:
        log.warning("Скролл-контейнер не найден, используем mouse.wheel")

    prev_count = 0
    stale_rounds = 0
    max_stale = 10

    while True:
        cur_count = _get_loaded_count(page)
        if cur_count >= max_results:
            log.info("Достигнут лимит: %d / %d", cur_count, max_results)
            break

        if cur_count == prev_count:
            if not _click_show_more(page):
                stale_rounds += 1
            else:
                stale_rounds = 0
        else:
            stale_rounds = 0
            log.info("Загружено сниппетов: %d", cur_count)

        if stale_rounds >= max_stale:
            log.info("Новые результаты не появляются, завершаем (всего %d)", cur_count)
            break

        prev_count = cur_count
        _scroll_search_panel(page, container_sel)
        page.wait_for_timeout(int(scroll_pause * 1000))

    return _get_loaded_count(page)


# ---------------------------------------------------------------------------
# Extracting from snippet list (DOM)
# ---------------------------------------------------------------------------

def _safe_text(loc) -> str:
    """Извлечь текст из локатора, вернуть '' если элемент отсутствует."""
    if loc.count() > 0:
        return loc.first.inner_text().strip()
    return ""


def parse_snippet(page: Page, index: int) -> Organization:
    """Извлечь данные из одного сниппета по индексу."""
    card = page.locator(ITEM_SEL).nth(index)
    org = Organization()

    # Название
    org.name = _safe_text(card.locator(SNIPPET_TITLE_SEL))

    # Адрес
    org.address = _safe_text(card.locator(SNIPPET_ADDRESS_SEL))

    # Категория
    org.category = _safe_text(card.locator(SNIPPET_CATEGORY_SEL))

    # Рейтинг
    org.rating = _safe_text(card.locator(SNIPPET_RATING_SEL))

    # Количество отзывов
    raw_reviews = _safe_text(card.locator(SNIPPET_REVIEWS_SEL))
    if raw_reviews:
        org.reviews_count = re.sub(r"[^\d]", "", raw_reviews)

    # Часы работы
    org.working_hours = _safe_text(card.locator(SNIPPET_HOURS_SEL))

    # Ссылка на карточку
    link = card.locator(LINK_SEL).first
    if link.count() > 0:
        org.yandex_url = link.get_attribute("href") or ""

    return org


# ---------------------------------------------------------------------------
# Detail page parsing (phone, website, etc.)
# ---------------------------------------------------------------------------

def enrich_from_detail(page: Page, org: Organization) -> Organization:
    """Открыть карточку организации и дополнить данные."""
    if not org.yandex_url:
        return org

    url = org.yandex_url
    if url.startswith("/"):
        url = f"https://yandex.ru{url}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)

        # Телефон
        phone_loc = page.locator(DETAIL_PHONE_SEL).first
        if phone_loc.count() > 0:
            org.phone = phone_loc.inner_text().strip()
            if not org.phone:
                href = phone_loc.get_attribute("href") or ""
                if href.startswith("tel:"):
                    org.phone = href[4:]

        # Сайт
        site_loc = page.locator(DETAIL_WEBSITE_SEL).first
        if site_loc.count() > 0:
            org.website = site_loc.inner_text().strip()
            if not org.website:
                org.website = site_loc.get_attribute("href") or ""

        # Адрес (если не извлекли из сниппета)
        if not org.address:
            org.address = _safe_text(page.locator(DETAIL_ADDRESS_SEL))

        # Рейтинг (если не извлекли из сниппета)
        if not org.rating:
            org.rating = _safe_text(page.locator(DETAIL_RATING_SEL))

        # Количество отзывов (если не извлекли)
        if not org.reviews_count:
            raw = _safe_text(page.locator(DETAIL_REVIEWS_COUNT_SEL))
            if raw:
                org.reviews_count = re.sub(r"[^\d]", "", raw)

        # Часы работы
        if not org.working_hours:
            hours_meta = page.locator("meta[itemprop='openingHours']")
            if hours_meta.count() > 0:
                parts = []
                for i in range(hours_meta.count()):
                    val = hours_meta.nth(i).get_attribute("content") or ""
                    if val:
                        parts.append(val)
                org.working_hours = "; ".join(parts)

            if not org.working_hours:
                org.working_hours = _safe_text(
                    page.locator("[class*='business-working-status-view__text']")
                )

        # Категория
        if not org.category:
            org.category = _safe_text(page.locator(DETAIL_CATEGORY_SEL))

    except Exception as exc:
        log.warning("Не удалось открыть карточку %s: %s", org.name, exc)

    return org


# ---------------------------------------------------------------------------
# API Intercept mode
#
# Яндекс.Карты делают XHR-запросы к внутренним API при скролле.
# Мы перехватываем ответы и извлекаем JSON напрямую — это надёжнее,
# чем парсить DOM.
# ---------------------------------------------------------------------------

def _extract_orgs_from_api_response(data: dict) -> list[Organization]:
    """Извлечь организации из JSON-ответа внутреннего API."""
    orgs: list[Organization] = []

    # Формат ответа может варьироваться; ищем массив с данными организаций
    features = (
        data.get("features")
        or data.get("data", {}).get("features")
        or data.get("items")
        or data.get("results")
        or []
    )

    for feat in features:
        props = feat.get("properties", feat)
        company = props.get("CompanyMetaData", props.get("companyMetaData", {}))
        if not company and "name" not in props:
            continue

        org = Organization()
        org.name = company.get("name", props.get("name", ""))
        org.address = company.get("address", props.get("address", ""))

        # Телефоны
        phones = company.get("Phones", company.get("phones", []))
        if phones:
            formatted = phones[0].get("formatted", phones[0].get("number", ""))
            org.phone = formatted

        # Сайт
        url_obj = company.get("url", company.get("Url", ""))
        if isinstance(url_obj, str):
            org.website = url_obj
        elif isinstance(url_obj, dict):
            org.website = url_obj.get("value", "")

        # Часы работы
        hours = company.get("Hours", company.get("hours", {}))
        if isinstance(hours, dict):
            text = hours.get("text", "")
            org.working_hours = text

        # Категории
        categories = company.get("Categories", company.get("categories", []))
        if categories:
            cat_names = [c.get("name", "") for c in categories if c.get("name")]
            org.category = ", ".join(cat_names)

        # Ссылка
        org.yandex_url = props.get("uri", props.get("url", ""))

        if org.name:
            orgs.append(org)

    return orgs


def run_api_intercept(
    page: Page,
    max_results: int,
    scroll_pause: float,
) -> list[Organization]:
    """Скроллить и перехватывать JSON из XHR-ответов."""
    all_orgs: list[Organization] = []
    seen_names: set[str] = set()

    def on_response(response: Response) -> None:
        url = response.url
        # Ловим запросы к API поиска/бизнесов
        api_patterns = [
            "/maps/api/search",
            "/maps/api/business",
            "searchBusinesses",
            "/search/",
            "csrfToken",  # skip
        ]
        is_api = any(p in url for p in api_patterns[:4])
        if not is_api:
            return
        if response.status != 200:
            return

        try:
            body = response.json()
        except Exception:
            return

        orgs = _extract_orgs_from_api_response(body)
        for org in orgs:
            key = f"{org.name}|{org.address}"
            if key not in seen_names:
                seen_names.add(key)
                all_orgs.append(org)

        if orgs:
            log.info("API перехвачено: +%d (всего %d)", len(orgs), len(all_orgs))

    page.on("response", on_response)

    container_sel = _find_scroll_container(page)
    stale_rounds = 0

    while len(all_orgs) < max_results:
        prev = len(all_orgs)
        _scroll_search_panel(page, container_sel)
        _click_show_more(page)
        page.wait_for_timeout(int(scroll_pause * 1000))

        if len(all_orgs) == prev:
            stale_rounds += 1
        else:
            stale_rounds = 0

        if stale_rounds >= 12:
            log.info("API: новые данные не поступают, завершаем (всего %d)", len(all_orgs))
            break

    page.remove_listener("response", on_response)
    return all_orgs[:max_results]


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
    api_intercept: bool = False,
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
                "Chrome/125.0.0.0 Safari/537.36"
            ),
        )
        page: Page = ctx.new_page()

        # Блокируем тяжёлые ресурсы для ускорения
        page.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda route: route.abort())
        page.route("**/mc.yandex.ru/**", lambda route: route.abort())
        page.route("**/yandex.ru/metrika/**", lambda route: route.abort())

        # 1. Открыть Яндекс.Карты с запросом
        encoded_query = urllib.parse.quote(query)
        search_url = f"https://yandex.ru/maps/?text={encoded_query}"
        log.info("Открываю %s", search_url)
        page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        # 2. Дождаться появления результатов
        try:
            page.wait_for_selector(ITEM_SEL, timeout=15000)
        except Exception:
            log.error("Результаты не найдены — возможно, запрос не дал результатов "
                      "или страница не загрузилась. Проверьте запрос.")
            browser.close()
            return []

        # 3. Собрать результаты
        if api_intercept:
            log.info("Режим API-перехвата")
            orgs = run_api_intercept(page, max_results, scroll_pause)
        else:
            # DOM-режим: скролл + парсинг сниппетов
            total = load_all_results(page, max_results, scroll_pause)
            log.info("Итого загружено сниппетов: %d", total)

            orgs: list[Organization] = []
            count = min(total, max_results)
            for i in range(count):
                try:
                    org = parse_snippet(page, i)
                    if org.name:
                        orgs.append(org)
                except Exception as exc:
                    log.debug("Ошибка парсинга сниппета #%d: %s", i, exc)
                if (i + 1) % 50 == 0:
                    log.info("Обработано карточек: %d / %d", i + 1, count)

        log.info("Извлечено организаций: %d", len(orgs))

        # 4. (Опционально) обогатить данные с карточек организаций
        if detail and orgs:
            log.info("Обогащаю данные с карточек организаций (%d шт)…", len(orgs))
            detail_page = ctx.new_page()
            # Блокируем картинки и на detail-странице
            detail_page.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda route: route.abort())

            for idx, org in enumerate(orgs):
                enrich_from_detail(detail_page, org)
                if (idx + 1) % 20 == 0:
                    log.info("Обогащено: %d / %d", idx + 1, len(orgs))
            detail_page.close()

        browser.close()

    # 5. Сохранить результат
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
        help="Максимум организаций (по умолчанию 500)",
    )
    parser.add_argument(
        "--output", "-o", default="results.xlsx",
        help="Файл результатов: .xlsx или .csv (по умолчанию results.xlsx)",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="Показывать браузер (для отладки)",
    )
    parser.add_argument(
        "--detail", action="store_true",
        help="Открывать карточку каждой организации для телефона/сайта",
    )
    parser.add_argument(
        "--scroll-pause", type=float, default=1.0,
        help="Пауза между прокрутками в секундах (по умолчанию 1.0)",
    )
    parser.add_argument(
        "--api-intercept", action="store_true",
        help="Перехватывать JSON из внутреннего API вместо парсинга DOM",
    )

    args = parser.parse_args()

    orgs = run_parser(
        query=args.query,
        max_results=args.max_results,
        output=args.output,
        headless=not args.no_headless,
        detail=args.detail,
        scroll_pause=args.scroll_pause,
        api_intercept=args.api_intercept,
    )

    print(f"\nГотово! Собрано {len(orgs)} организаций → {args.output}")


if __name__ == "__main__":
    main()
