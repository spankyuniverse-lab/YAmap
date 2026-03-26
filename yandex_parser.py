"""
Парсер Яндекс.Карт — сбор организаций по поисковому запросу или по категориям.

Яндекс.Карты используют динамическую подгрузку результатов (infinite scroll),
поэтому для парсинга применяется Playwright с эмуляцией прокрутки.

Режимы работы:
  1. По запросу:    python yandex_parser.py "кофейни Москва"
  2. По категориям: python yandex_parser.py --city Москва --category еда рестораны кафе
  3. Все категории: python yandex_parser.py --city Москва --all-categories
  4. Список категорий: python yandex_parser.py --list-categories

Дополнительно:
  --api-intercept  — перехват JSON из внутреннего API (надёжнее DOM)
  --detail         — открытие карточек для телефона/сайта
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
    search_query: str = ""


FIELDNAMES = [f.name for f in fields(Organization)]

# ---------------------------------------------------------------------------
# Каталог категорий (аналог 2ГИС)
#
# Структура: группа → список поисковых запросов.
# При парсинге каждый запрос дополняется названием города.
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, list[str]] = {
    "еда": [
        "рестораны", "кафе", "бары", "пиццерии", "суши-бары",
        "столовые", "фастфуд", "кофейни", "кондитерские", "пекарни",
        "шаурма", "бургерные", "доставка еды",
    ],
    "продукты": [
        "продуктовые магазины", "супермаркеты", "мясные магазины",
        "рыбные магазины", "овощи и фрукты", "молочные продукты",
        "алкогольные магазины", "кулинарии",
    ],
    "здоровье": [
        "аптеки", "больницы", "поликлиники", "стоматологии",
        "медицинские центры", "ветеринарные клиники", "лаборатории",
        "оптика", "косметология",
    ],
    "авто": [
        "автосервисы", "шиномонтаж", "автомойки", "автозапчасти",
        "АЗС", "автосалоны", "эвакуаторы", "парковки",
        "техосмотр", "автострахование",
    ],
    "красота": [
        "салоны красоты", "парикмахерские", "барбершопы",
        "маникюр", "массаж", "спа-салоны", "солярии", "тату-салоны",
    ],
    "покупки": [
        "торговые центры", "магазины одежды", "магазины обуви",
        "магазины электроники", "магазины мебели", "строительные магазины",
        "магазины цветов", "зоомагазины", "книжные магазины",
        "магазины подарков", "ювелирные магазины",
    ],
    "услуги": [
        "банки", "банкоматы", "нотариусы", "юридические услуги",
        "страховые компании", "фотостудии", "ателье", "химчистки",
        "ремонт телефонов", "ремонт бытовой техники", "клининг",
        "курьерские службы", "типографии",
    ],
    "образование": [
        "школы", "детские сады", "университеты", "колледжи",
        "языковые курсы", "автошколы", "репетиторы",
        "курсы программирования", "музыкальные школы",
    ],
    "спорт": [
        "фитнес-клубы", "тренажёрные залы", "бассейны",
        "спортивные магазины", "йога-студии", "танцевальные студии",
        "боксёрские клубы", "теннисные корты", "спортивные площадки",
    ],
    "развлечения": [
        "кинотеатры", "театры", "музеи", "парки развлечений",
        "боулинг", "бильярд", "караоке", "квесты",
        "ночные клубы", "концертные залы",
    ],
    "туризм": [
        "гостиницы", "хостелы", "турагентства", "достопримечательности",
        "экскурсии", "аренда автомобилей", "визовые центры",
    ],
    "транспорт": [
        "такси", "каршеринг", "автобусные станции",
        "железнодорожные вокзалы", "аэропорты", "грузоперевозки",
    ],
    "недвижимость": [
        "агентства недвижимости", "новостройки",
        "управляющие компании", "жилые комплексы",
    ],
    "дом": [
        "мебельные магазины", "сантехника", "электрика",
        "окна и двери", "кухни на заказ", "натяжные потолки",
        "кондиционеры", "отопление",
    ],
    "IT": [
        "компьютерные магазины", "ремонт компьютеров",
        "IT-компании", "интернет-провайдеры", "веб-студии",
    ],
}


def list_categories() -> None:
    """Вывести каталог категорий в консоль."""
    print("\n📂 Каталог категорий (аналог 2ГИС):\n")
    for group, items in CATEGORIES.items():
        print(f"  [{group}]")
        for item in items:
            print(f"    • {item}")
        print()
    print("Использование:")
    print('  python yandex_parser.py --city Москва --category еда')
    print('  python yandex_parser.py --city Москва --category еда рестораны кафе')
    print('  python yandex_parser.py --city Москва --all-categories')


def resolve_categories(names: list[str]) -> list[str]:
    """Преобразовать названия групп/категорий в список поисковых запросов.

    Принимает как названия групп (еда, авто), так и конкретные запросы
    (рестораны, кафе). Если имя совпадает с группой — разворачивает все
    подкатегории. Иначе трактует как прямой поисковый запрос.
    """
    queries: list[str] = []
    for name in names:
        key = name.lower().strip()
        if key in CATEGORIES:
            queries.extend(CATEGORIES[key])
        else:
            queries.append(name.strip())
    return queries


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


HEADERS_RU = {
    "name": "Название",
    "address": "Адрес",
    "phone": "Телефон",
    "website": "Сайт",
    "rating": "Рейтинг",
    "reviews_count": "Отзывы",
    "category": "Категория",
    "working_hours": "Часы работы",
    "search_query": "Поисковый запрос",
    "yandex_url": "Ссылка",
}


def _write_sheet(ws, orgs: list[Organization], include_query: bool = False) -> None:
    """Записать организации на один лист Excel."""
    from openpyxl.styles import Font

    cols = FIELDNAMES + (["search_query"] if include_query else [])

    for col_idx, field in enumerate(cols, 1):
        cell = ws.cell(row=1, column=col_idx, value=HEADERS_RU.get(field, field))
        cell.font = Font(bold=True)

    for row_idx, org in enumerate(orgs, 2):
        d = asdict(org)
        for col_idx, field in enumerate(cols, 1):
            ws.cell(row=row_idx, column=col_idx, value=d.get(field, ""))

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)


def save_xlsx(orgs: list[Organization], path: Path) -> None:
    try:
        from openpyxl import Workbook
    except ImportError:
        log.warning("openpyxl не установлен — сохраняю в CSV")
        save_csv(orgs, path.with_suffix(".csv"))
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Яндекс.Карты"
    _write_sheet(ws, orgs)
    wb.save(path)
    log.info("XLSX сохранён: %s (%d записей)", path, len(orgs))


def save_xlsx_by_categories(
    results: dict[str, list[Organization]],
    path: Path,
) -> None:
    """Сохранить результаты по категориям: отдельный лист на каждую + сводный."""
    try:
        from openpyxl import Workbook
    except ImportError:
        log.warning("openpyxl не установлен — сохраняю сводный CSV")
        all_orgs = []
        for orgs in results.values():
            all_orgs.extend(orgs)
        save_csv(all_orgs, path.with_suffix(".csv"))
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # Сводный лист со всеми результатами
    ws_all = wb.active
    ws_all.title = "Все результаты"
    all_orgs: list[Organization] = []
    for orgs in results.values():
        all_orgs.extend(orgs)
    _write_sheet(ws_all, all_orgs, include_query=True)

    # Отдельный лист на каждую категорию
    for query, orgs in results.items():
        if not orgs:
            continue
        # Имя листа Excel ≤ 31 символ, без спецсимволов
        sheet_name = re.sub(r'[\\/*?\[\]:]', '', query)[:31]
        ws = wb.create_sheet(title=sheet_name)
        _write_sheet(ws, orgs)

    wb.save(path)
    total = sum(len(v) for v in results.values())
    log.info(
        "XLSX сохранён: %s (%d записей, %d листов)",
        path, total, len(wb.sheetnames),
    )


# ---------------------------------------------------------------------------
# Main parser flow
# ---------------------------------------------------------------------------

def _create_browser_context(pw, headless: bool):
    """Создать браузер и контекст с общими настройками."""
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
    return browser, ctx


def _setup_page(ctx: BrowserContext) -> Page:
    """Создать страницу с блокировкой тяжёлых ресурсов."""
    page: Page = ctx.new_page()
    page.route("**/*.{png,jpg,jpeg,gif,webp,svg,ico}", lambda route: route.abort())
    page.route("**/mc.yandex.ru/**", lambda route: route.abort())
    page.route("**/yandex.ru/metrika/**", lambda route: route.abort())
    return page


def _search_and_collect(
    page: Page,
    query: str,
    max_results: int,
    scroll_pause: float,
    api_intercept: bool,
) -> list[Organization]:
    """Выполнить поиск и собрать результаты (общая логика для всех режимов)."""
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://yandex.ru/maps/?text={encoded_query}"
    log.info("Открываю %s", search_url)
    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    try:
        page.wait_for_selector(ITEM_SEL, timeout=15000)
    except Exception:
        log.warning("Результаты не найдены для запроса: %s", query)
        return []

    if api_intercept:
        log.info("Режим API-перехвата")
        orgs = run_api_intercept(page, max_results, scroll_pause)
    else:
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
    return orgs


def _enrich_orgs(ctx: BrowserContext, orgs: list[Organization]) -> None:
    """Обогатить организации данными с карточек."""
    if not orgs:
        return
    log.info("Обогащаю данные с карточек организаций (%d шт)…", len(orgs))
    detail_page = _setup_page(ctx)
    for idx, org in enumerate(orgs):
        enrich_from_detail(detail_page, org)
        if (idx + 1) % 20 == 0:
            log.info("Обогащено: %d / %d", idx + 1, len(orgs))
    detail_page.close()


def run_parser(
    query: str,
    max_results: int = 500,
    output: str = "results.xlsx",
    headless: bool = True,
    detail: bool = False,
    scroll_pause: float = 1.0,
    api_intercept: bool = False,
) -> list[Organization]:
    """Парсер по одному поисковому запросу."""
    out_path = Path(output)

    with sync_playwright() as pw:
        browser, ctx = _create_browser_context(pw, headless)
        page = _setup_page(ctx)

        orgs = _search_and_collect(page, query, max_results, scroll_pause, api_intercept)

        for org in orgs:
            org.search_query = query

        if detail:
            _enrich_orgs(ctx, orgs)

        browser.close()

    if out_path.suffix == ".csv":
        save_csv(orgs, out_path)
    else:
        save_xlsx(orgs, out_path)

    return orgs


# ---------------------------------------------------------------------------
# Category-based parser (аналог 2ГИС)
# ---------------------------------------------------------------------------

def run_category_parser(
    city: str,
    categories: list[str],
    max_results_per_category: int = 500,
    output: str = "categories.xlsx",
    headless: bool = True,
    detail: bool = False,
    scroll_pause: float = 1.0,
    api_intercept: bool = False,
) -> dict[str, list[Organization]]:
    """Парсер по категориям: для каждой категории запускает поиск «категория город».

    Возвращает словарь {запрос: [организации]}.
    Сохраняет в Excel с отдельным листом на каждую категорию + сводный лист.
    """
    out_path = Path(output)
    results: dict[str, list[Organization]] = {}
    seen_global: set[str] = set()

    queries = resolve_categories(categories)
    total_queries = len(queries)
    log.info(
        "Город: %s | Категорий: %d | Макс. на категорию: %d",
        city, total_queries, max_results_per_category,
    )

    with sync_playwright() as pw:
        browser, ctx = _create_browser_context(pw, headless)
        page = _setup_page(ctx)

        for q_idx, cat_query in enumerate(queries, 1):
            full_query = f"{cat_query} {city}"
            log.info(
                "━━━ [%d/%d] %s ━━━",
                q_idx, total_queries, full_query,
            )

            orgs = _search_and_collect(
                page, full_query, max_results_per_category,
                scroll_pause, api_intercept,
            )

            # Дедупликация по имени+адресу
            unique_orgs: list[Organization] = []
            for org in orgs:
                key = f"{org.name}|{org.address}"
                if key not in seen_global:
                    seen_global.add(key)
                    org.search_query = full_query
                    unique_orgs.append(org)

            if detail:
                _enrich_orgs(ctx, unique_orgs)

            results[full_query] = unique_orgs
            log.info(
                "Категория «%s»: %d организаций (уникальных)",
                cat_query, len(unique_orgs),
            )

            # Небольшая пауза между категориями
            if q_idx < total_queries:
                page.wait_for_timeout(2000)

        browser.close()

    # Сохранение
    if out_path.suffix == ".csv":
        all_orgs: list[Organization] = []
        for orgs in results.values():
            all_orgs.extend(orgs)
        save_csv(all_orgs, out_path)
    else:
        save_xlsx_by_categories(results, out_path)

    total_orgs = sum(len(v) for v in results.values())
    log.info("Всего собрано: %d организаций по %d категориям", total_orgs, total_queries)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Парсер организаций с Яндекс.Карт — по запросу или по категориям (аналог 2ГИС)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Примеры:
  # По запросу (как раньше)
  python yandex_parser.py "кофейни Москва"
  python yandex_parser.py "автосервис Казань" -n 200 -o авто.xlsx

  # Список категорий
  python yandex_parser.py --list-categories

  # По категориям — группа «еда» (все подкатегории)
  python yandex_parser.py --city Москва --category еда

  # По категориям — конкретные запросы
  python yandex_parser.py --city Москва --category рестораны кафе бары

  # Комбинация группы и конкретных категорий
  python yandex_parser.py --city СПб --category авто шиномонтаж

  # Все категории сразу
  python yandex_parser.py --city Казань --all-categories -n 100
""",
    )
    parser.add_argument(
        "query", nargs="?", default=None,
        help='Поисковый запрос, напр. "кофейни Москва"',
    )
    parser.add_argument(
        "--list-categories", action="store_true",
        help="Показать каталог категорий и выйти",
    )
    parser.add_argument(
        "--city", type=str, default=None,
        help="Город для парсинга по категориям",
    )
    parser.add_argument(
        "--category", nargs="+", default=None,
        help="Категории или группы категорий (напр. еда рестораны кафе)",
    )
    parser.add_argument(
        "--all-categories", action="store_true",
        help="Парсить все категории из каталога",
    )
    parser.add_argument(
        "--max-results", "-n", type=int, default=500,
        help="Максимум организаций на запрос/категорию (по умолчанию 500)",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Файл результатов: .xlsx или .csv (по умолчанию results.xlsx / categories.xlsx)",
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

    # Режим: показать каталог категорий
    if args.list_categories:
        list_categories()
        return

    # Режим: парсинг по категориям
    if args.city and (args.category or args.all_categories):
        if args.all_categories:
            cats = list(CATEGORIES.keys())
        else:
            cats = args.category

        output = args.output or f"{args.city}_categories.xlsx"

        results = run_category_parser(
            city=args.city,
            categories=cats,
            max_results_per_category=args.max_results,
            output=output,
            headless=not args.no_headless,
            detail=args.detail,
            scroll_pause=args.scroll_pause,
            api_intercept=args.api_intercept,
        )

        total = sum(len(v) for v in results.values())
        print(f"\nГотово! Собрано {total} организаций по {len(results)} категориям → {output}")
        return

    # Режим: обычный поиск по запросу
    if not args.query:
        parser.print_help()
        print("\nОшибка: укажите поисковый запрос или --city + --category")
        sys.exit(1)

    output = args.output or "results.xlsx"
    orgs = run_parser(
        query=args.query,
        max_results=args.max_results,
        output=output,
        headless=not args.no_headless,
        detail=args.detail,
        scroll_pause=args.scroll_pause,
        api_intercept=args.api_intercept,
    )

    print(f"\nГотово! Собрано {len(orgs)} организаций → {output}")


if __name__ == "__main__":
    main()
