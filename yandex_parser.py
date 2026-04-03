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
import random
import re
import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

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
    latitude: str = ""
    longitude: str = ""
    email: str = ""
    social_links: str = ""
    yandex_url: str = ""
    search_query: str = ""


FIELDNAMES = [f.name for f in fields(Organization)]


# ---------------------------------------------------------------------------
# Proxy rotation
# ---------------------------------------------------------------------------

class ProxyRotator:
    """Ротация прокси-серверов для обхода блокировок.

    Форматы прокси:
      - http://host:port
      - http://user:pass@host:port
      - socks5://host:port
    """

    def __init__(self, proxies: list[str] | None = None):
        self._proxies = proxies or []
        self._index = 0
        self._fail_counts: dict[str, int] = {}

    @classmethod
    def from_file(cls, path: str) -> "ProxyRotator":
        """Загрузить прокси из файла (одна строка = один прокси)."""
        p = Path(path)
        if not p.exists():
            log.warning("Файл прокси не найден: %s", path)
            return cls([])
        lines = [
            line.strip() for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        log.info("Загружено %d прокси из %s", len(lines), path)
        return cls(lines)

    @property
    def has_proxies(self) -> bool:
        return len(self._proxies) > 0

    def next(self) -> str | None:
        """Следующий прокси (round-robin)."""
        if not self._proxies:
            return None
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        return proxy

    def current(self) -> str | None:
        if not self._proxies:
            return None
        return self._proxies[(self._index - 1) % len(self._proxies)]

    def mark_failed(self, proxy: str) -> None:
        self._fail_counts[proxy] = self._fail_counts.get(proxy, 0) + 1
        if self._fail_counts[proxy] >= 3:
            log.warning("Прокси %s — 3 ошибки, удаляю из ротации", proxy)
            self._proxies = [p for p in self._proxies if p != proxy]
            self._fail_counts.pop(proxy, None)

    def to_playwright_arg(self, proxy_url: str) -> dict:
        """Конвертировать URL прокси в формат Playwright."""
        result: dict[str, str] = {"server": proxy_url}
        parsed = urllib.parse.urlparse(proxy_url)
        if parsed.username:
            result["username"] = parsed.username
        if parsed.password:
            result["password"] = parsed.password
        if parsed.username or parsed.password:
            result["server"] = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        return result


# Глобальный ротатор (инициализируется в main)
_proxy_rotator: ProxyRotator | None = None


def get_proxy_rotator() -> ProxyRotator:
    global _proxy_rotator
    if _proxy_rotator is None:
        _proxy_rotator = ProxyRotator()
    return _proxy_rotator


# ---------------------------------------------------------------------------
# CAPTCHA detection
# ---------------------------------------------------------------------------

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
    # Дополнительно проверяем по URL
    if "showcaptcha" in page.url or "captcha" in page.url.lower():
        return True
    return False


def handle_captcha(page: Page, headless: bool) -> bool:
    """Обработать CAPTCHA: пауза и ожидание решения.

    Возвращает True если CAPTCHA решена, False если таймаут.
    """
    if not detect_captcha(page):
        return True

    log.warning("=" * 60)
    log.warning("ОБНАРУЖЕНА CAPTCHA!")
    if headless:
        log.warning("Парсер в headless-режиме — решение капчи невозможно.")
        log.warning("Перезапустите с --no-headless для ручного решения.")
        log.warning("Пауза 30 секунд перед продолжением…")
        page.wait_for_timeout(30000)
    else:
        log.warning("Решите капчу в браузере. Ожидание до 120 секунд…")
        # Ждём пока капча исчезнет (пользователь решит вручную)
        for _ in range(24):  # 24 * 5 = 120 секунд
            page.wait_for_timeout(5000)
            if not detect_captcha(page):
                log.info("CAPTCHA решена!")
                return True
        log.error("Таймаут ожидания решения CAPTCHA (120 сек)")
    log.warning("=" * 60)
    return not detect_captcha(page)


# ---------------------------------------------------------------------------
# Resume manager — продолжение с места остановки
# ---------------------------------------------------------------------------

class ResumeManager:
    """Менеджер докачки: загружает уже собранные данные из файла."""

    def __init__(self, path: Path | None = None):
        self._existing: dict[str, Organization] = {}
        self._completed_queries: set[str] = set()
        if path and path.exists():
            self._load(path)

    def _load(self, path: Path) -> None:
        """Загрузить существующий файл результатов."""
        suffix = path.suffix.lower()
        rows: list[dict] = []

        if suffix == ".csv":
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f, delimiter=";")
                    rows = list(reader)
            except Exception as exc:
                log.warning("Не удалось прочитать CSV для резюме: %s", exc)
                return

        elif suffix == ".xlsx":
            try:
                from openpyxl import load_workbook
                wb = load_workbook(path, read_only=True)
                ws = wb.active
                if ws is None:
                    return
                headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
                for row in ws.iter_rows(min_row=2, values_only=True):
                    row_dict = {h: (v or "") for h, v in zip(headers, row) if h}
                    rows.append(row_dict)
                wb.close()
            except Exception as exc:
                log.warning("Не удалось прочитать XLSX для резюме: %s", exc)
                return

        elif suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    rows = data
                elif isinstance(data, dict) and "organizations" in data:
                    rows = data["organizations"]
            except Exception as exc:
                log.warning("Не удалось прочитать JSON для резюме: %s", exc)
                return

        # Маппинг русских заголовков на английские поля
        ru_to_en = {v: k for k, v in HEADERS_RU.items()}

        for row in rows:
            # Нормализуем ключи (могут быть русские заголовки из xlsx)
            norm = {}
            for k, v in row.items():
                en_key = ru_to_en.get(k, k)
                norm[en_key] = str(v) if v else ""

            key = f"{norm.get('name', '')}|{norm.get('address', '')}"
            if key != "|":
                org = Organization(**{f: norm.get(f, "") for f in FIELDNAMES if f in norm})
                self._existing[key] = org
                q = norm.get("search_query", "")
                if q:
                    self._completed_queries.add(q)

        log.info(
            "Резюме: загружено %d существующих организаций, %d выполненных запросов",
            len(self._existing), len(self._completed_queries),
        )

    @property
    def existing_count(self) -> int:
        return len(self._existing)

    def is_known(self, name: str, address: str) -> bool:
        return f"{name}|{address}" in self._existing

    def is_query_done(self, query: str) -> bool:
        return query in self._completed_queries

    def existing_orgs(self) -> list[Organization]:
        return list(self._existing.values())

    def existing_keys(self) -> set[str]:
        return set(self._existing.keys())


# ---------------------------------------------------------------------------
# Config file support (YAML/JSON)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "max_results": 500,
    "scroll_pause": 1.0,
    "headless": True,
    "detail": False,
    "api_intercept": False,
    "output": "results.xlsx",
    "proxy": None,
    "proxy_file": None,
    "city": None,
    "categories": None,
}


def load_config(path: str) -> dict[str, Any]:
    """Загрузить конфиг из YAML или JSON файла."""
    p = Path(path)
    if not p.exists():
        log.warning("Конфиг-файл не найден: %s", path)
        return {}

    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        try:
            import yaml
            return yaml.safe_load(text) or {}
        except ImportError:
            log.warning("PyYAML не установлен — пробую как JSON")
    try:
        return json.loads(text)
    except Exception as exc:
        log.warning("Ошибка чтения конфига %s: %s", path, exc)
        return {}


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
# Selector auto-detection engine
#
# Яндекс периодически меняет DOM-структуру и обфусцирует классы.
# SelectorDetector анализирует живую страницу через JS и находит
# нужные элементы по эвристикам: schema.org microdata, ARIA,
# семантика HTML, текстовые паттерны, повторяющиеся структуры.
#
# Цепочка: кеш → hardcoded → auto-detect → fallback
# ---------------------------------------------------------------------------

SELECTORS_CACHE_FILE = Path("selectors_cache.json")

# Ключи, которые мы детектим
SELECTOR_KEYS = [
    "scroll_container", "item", "link",
    "snippet_title", "snippet_address", "snippet_category",
    "snippet_hours", "snippet_rating", "snippet_reviews",
    "detail_name", "detail_address", "detail_phone",
    "detail_website", "detail_rating", "detail_reviews_count",
    "detail_hours", "detail_category",
    "show_more",
]

# Маппинг ключ → hardcoded-селектор (для fallback)
_HARDCODED: dict[str, str] = {
    "scroll_container": SCROLL_CONTAINER_SEL,
    "item": ITEM_SEL,
    "link": LINK_SEL,
    "show_more": SHOW_MORE_SEL,
    "snippet_title": SNIPPET_TITLE_SEL,
    "snippet_address": SNIPPET_ADDRESS_SEL,
    "snippet_category": SNIPPET_CATEGORY_SEL,
    "snippet_hours": SNIPPET_HOURS_SEL,
    "snippet_rating": SNIPPET_RATING_SEL,
    "snippet_reviews": SNIPPET_REVIEWS_SEL,
    "detail_name": DETAIL_NAME_SEL,
    "detail_address": DETAIL_ADDRESS_SEL,
    "detail_phone": DETAIL_PHONE_SEL,
    "detail_website": DETAIL_WEBSITE_SEL,
    "detail_rating": DETAIL_RATING_SEL,
    "detail_reviews_count": DETAIL_REVIEWS_COUNT_SEL,
    "detail_hours": DETAIL_HOURS_SEL,
    "detail_category": DETAIL_CATEGORY_SEL,
}


class SelectorCache:
    """Кеш обнаруженных селекторов в JSON-файле."""

    def __init__(self, path: Path = SELECTORS_CACHE_FILE):
        self._path = path
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
                log.info("Загружен кеш селекторов: %s (%d записей)", self._path, len(self._data))
            except Exception:
                self._data = {}

    def save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("Кеш селекторов сохранён: %s", self._path)

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def update(self, mapping: dict[str, str]) -> None:
        self._data.update(mapping)

    def all(self) -> dict[str, str]:
        return dict(self._data)


# --- JS-код для автодетекта, выполняется через page.evaluate() ---

_JS_DETECT_SNIPPETS = """() => {
    // Ищем повторяющиеся карточки в левой панели (sidebar)
    // Стратегия: найти контейнер со списком, в нём — повторяющиеся элементы

    // 1) Пробуем по известным паттернам подстроки класса
    const knownPatterns = [
        'search-snippet-view',
        'search-list-view__item',
        'serp-item',
        'search-result',
    ];
    for (const pat of knownPatterns) {
        const els = document.querySelectorAll(`[class*="${pat}"]`);
        if (els.length >= 3) {
            return { item: `[class*="${pat}"]`, count: els.length };
        }
    }

    // 2) Эвристика: ищем группы >3 однотипных элементов с ссылками внутри
    //    в левой трети экрана (sidebar Я.Карт)
    const sidebar = document.querySelector('[class*="sidebar"], [class*="panel"], aside')
        || document.body;
    const candidates = {};
    const allEls = sidebar.querySelectorAll('div, li, article, section');

    for (const el of allEls) {
        if (el.children.length < 2) continue;
        if (el.offsetWidth > window.innerWidth * 0.6) continue;
        const cl = Array.from(el.classList).find(c => c.length > 4) || '';
        if (!cl) continue;
        // Группируем по «базовому» классу (без цифровых префиксов)
        const base = cl.replace(/^_[a-f0-9]+_/i, '').replace(/^_+/, '');
        if (!base) continue;
        if (!candidates[base]) candidates[base] = { sel: '', count: 0 };
        candidates[base].count++;
        candidates[base].sel = `[class*="${base}"]`;
    }

    // Берём класс с наибольшим количеством повторений (>= 3)
    let best = null;
    for (const [base, info] of Object.entries(candidates)) {
        if (info.count >= 3 && (!best || info.count > best.count)) {
            best = info;
        }
    }
    if (best) return { item: best.sel, count: best.count };
    return null;
}"""


_JS_DETECT_SNIPPET_FIELDS = """(itemSel) => {
    const cards = document.querySelectorAll(itemSel);
    if (cards.length === 0) return {};

    const result = {};
    // Анализируем первые 3 карточки для надёжности
    const sample = Array.from(cards).slice(0, 3);

    // Собираем все уникальные классы потомков
    function findBySubstring(els, patterns) {
        for (const pat of patterns) {
            for (const el of els) {
                const matches = el.querySelectorAll(`[class*="${pat}"]`);
                if (matches.length > 0) return `[class*="${pat}"]`;
            }
        }
        return null;
    }

    // Название — самый крупный текст / заголовок
    result.snippet_title = findBySubstring(sample, [
        'snippet-view__title', 'business-snippet-view__title',
        'title-view__title', 'card-title',
    ]);
    if (!result.snippet_title) {
        // Fallback: ищем элемент с наибольшим font-size
        for (const card of sample) {
            let maxSize = 0, bestEl = null;
            for (const child of card.querySelectorAll('*')) {
                const fs = parseFloat(getComputedStyle(child).fontSize);
                const text = child.textContent.trim();
                if (fs > maxSize && text.length > 2 && text.length < 100) {
                    maxSize = fs;
                    bestEl = child;
                }
            }
            if (bestEl) {
                const cls = Array.from(bestEl.classList).find(c => c.length > 4);
                if (cls) { result.snippet_title = `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`; break; }
            }
        }
    }

    // Адрес
    result.snippet_address = findBySubstring(sample, [
        'snippet-view__address', 'business-snippet-view__address',
        'address-view', 'subtitle',
    ]);

    // Категория
    result.snippet_category = findBySubstring(sample, [
        'snippet-view__categories', 'business-snippet-view__categories',
        'categories-view', 'category',
    ]);

    // Рейтинг (число)
    result.snippet_rating = findBySubstring(sample, [
        'rating-badge-view__rating-text', 'rating-text',
        'rating-value', 'rating__value',
    ]);

    // Отзывы (счётчик)
    result.snippet_reviews = findBySubstring(sample, [
        'rating-badge-view__rating-count', 'rating-count',
        'reviews-count',
    ]);

    // Часы работы
    result.snippet_hours = findBySubstring(sample, [
        'snippet-view__open-hours', 'open-hours',
        'working-status', 'hours',
    ]);

    // Ссылка-оверлей
    result.link = findBySubstring(sample, [
        'snippet-view__link-overlay', 'link-overlay',
        'snippet-view__link',
    ]);
    // Fallback: первый <a> с href, содержащим /org/
    if (!result.link) {
        for (const card of sample) {
            const a = card.querySelector('a[href*="/org/"]');
            if (a) {
                const cls = Array.from(a.classList).find(c => c.length > 4);
                if (cls) { result.link = `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`; break; }
                else { result.link = 'a[href*="/org/"]'; break; }
            }
        }
    }

    // Убираем null-значения
    for (const k in result) {
        if (!result[k]) delete result[k];
    }
    return result;
}"""


_JS_DETECT_DETAIL_FIELDS = """() => {
    const result = {};

    function findSel(patterns) {
        for (const pat of patterns) {
            if (document.querySelector(`[class*="${pat}"]`)) return `[class*="${pat}"]`;
        }
        return null;
    }

    // Название — h1
    const h1 = document.querySelector('h1');
    if (h1) {
        const cls = Array.from(h1.classList).find(c => c.length > 4);
        result.detail_name = cls
            ? `h1[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`
            : 'h1';
    }

    // Адрес
    result.detail_address = findSel([
        'business-contacts-view__address', 'orgpage-header-view__address',
        'card-title-view__subtitle', 'address-link',
    ]);

    // Телефон — ищем a[href^="tel:"] или элементы с паттерном телефона
    const telLink = document.querySelector('a[href^="tel:"]');
    if (telLink) {
        const cls = Array.from(telLink.classList).find(c => c.length > 4);
        result.detail_phone = cls
            ? `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`
            : 'a[href^="tel:"]';
    }
    if (!result.detail_phone) {
        result.detail_phone = findSel([
            'card-phones-view__phone-number', 'orgpage-phones-view__phone-number',
            'phone-number', 'phones-view',
        ]);
    }

    // Сайт — ссылка наружу (не yandex, не tel:)
    result.detail_website = findSel([
        'business-urls-view__text', 'card-feature-view__content',
        'orgpage-feature-view__content', 'urls-view',
    ]);
    if (!result.detail_website) {
        const links = document.querySelectorAll('a[href^="http"]');
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            if (!href.includes('yandex') && !href.includes('google') && a.textContent.trim()) {
                const cls = Array.from(a.classList).find(c => c.length > 4);
                if (cls) {
                    result.detail_website = `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`;
                    break;
                }
            }
        }
    }

    // Рейтинг
    result.detail_rating = findSel([
        'business-summary-rating-badge-view__rating-text',
        'business-rating-badge-view__rating-text',
        'rating-badge-view__rating-text', 'rating-text',
    ]);

    // Кол-во отзывов
    result.detail_reviews_count = findSel([
        'tabs-select-view__counter', 'rating-count', 'reviews-count',
    ]);

    // Часы работы — schema.org microdata или класс
    if (document.querySelector('meta[itemprop="openingHours"]')) {
        result.detail_hours = 'meta[itemprop="openingHours"]';
    }
    if (!result.detail_hours) {
        result.detail_hours = findSel([
            'business-working-status-view__text', 'working-status-view',
            'open-hours', 'working-hours',
        ]);
    }

    // Категория
    result.detail_category = findSel([
        'business-categories-view__category', 'breadcrumbs-view__text',
        'categories-view', 'category-view',
    ]);

    // Убираем null
    for (const k in result) {
        if (!result[k]) delete result[k];
    }
    return result;
}"""


_JS_DETECT_SCROLL = """() => {
    // 1) Известные паттерны
    const knownPatterns = ['scroll__container', 'scroll-container', 'scrollable'];
    for (const pat of knownPatterns) {
        const el = document.querySelector(`[class*="${pat}"]`);
        if (el && el.scrollHeight > el.clientHeight + 50) {
            return `[class*="${pat}"]`;
        }
    }

    // 2) Ищем любой div с overflow-y: auto/scroll и достаточной высотой в левой части
    const all = document.querySelectorAll('div, section, aside, ul');
    for (const el of all) {
        const style = getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        if ((style.overflowY === 'auto' || style.overflowY === 'scroll') &&
            el.scrollHeight > el.clientHeight + 100 &&
            el.clientHeight > 200 &&
            rect.left < window.innerWidth * 0.5) {
            if (el.id) return '#' + el.id;
            const cls = Array.from(el.classList).find(c => c.length > 4);
            if (cls) return `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`;
            return el.tagName.toLowerCase() + '.' + Array.from(el.classList).join('.');
        }
    }
    return null;
}"""


_JS_DETECT_SHOW_MORE = """() => {
    // Кнопка «Показать ещё» / «Ещё» / «Show more»
    const patterns = ['show-more', 'search-list-view__more', 'load-more'];
    for (const pat of patterns) {
        const el = document.querySelector(`[class*="${pat}"] button, button[class*="${pat}"]`);
        if (el) {
            const cls = Array.from(el.parentElement.classList).find(c => c.length > 4) ||
                        Array.from(el.classList).find(c => c.length > 4);
            if (cls) return `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"] button`;
        }
    }
    // Fallback: кнопка с текстом «ещё» или «показать»
    const buttons = document.querySelectorAll('button');
    for (const b of buttons) {
        const t = b.textContent.toLowerCase().trim();
        if (t.includes('ещё') || t.includes('показать') || t.includes('more')) {
            const cls = Array.from(b.classList).find(c => c.length > 4);
            if (cls) return `button[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`;
        }
    }
    return null;
}"""


def _probe_selector(page: Page, selector: str) -> bool:
    """Проверить, находит ли селектор хотя бы один элемент на странице."""
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


def detect_selectors(page: Page, mode: str = "list") -> dict[str, str]:
    """Обнаружить актуальные CSS-селекторы на живой странице.

    Args:
        page: Playwright-страница с загруженными результатами.
        mode: 'list' — детектим селекторы списка/сниппетов,
              'detail' — детектим селекторы карточки организации.

    Returns:
        dict с ключами из SELECTOR_KEYS и значениями — CSS-селекторами.
    """
    found: dict[str, str] = {}

    if mode == "list":
        # Скролл-контейнер
        scroll_sel = page.evaluate(_JS_DETECT_SCROLL)
        if scroll_sel:
            found["scroll_container"] = scroll_sel
            log.debug("Auto-detect scroll_container: %s", scroll_sel)

        # Сниппеты (карточки результатов)
        snippet_info = page.evaluate(_JS_DETECT_SNIPPETS)
        if snippet_info and snippet_info.get("item"):
            item_sel = snippet_info["item"]
            found["item"] = item_sel
            log.debug("Auto-detect item: %s (%d шт)", item_sel, snippet_info.get("count", 0))

            # Поля внутри сниппетов
            fields = page.evaluate(_JS_DETECT_SNIPPET_FIELDS, item_sel)
            if fields:
                found.update(fields)
                log.debug("Auto-detect snippet fields: %s", list(fields.keys()))

        # Кнопка «Показать ещё»
        show_more = page.evaluate(_JS_DETECT_SHOW_MORE)
        if show_more:
            found["show_more"] = show_more
            log.debug("Auto-detect show_more: %s", show_more)

    elif mode == "detail":
        fields = page.evaluate(_JS_DETECT_DETAIL_FIELDS)
        if fields:
            found.update(fields)
            log.debug("Auto-detect detail fields: %s", list(fields.keys()))

    return found


class SelectorEngine:
    """Умный движок селекторов с цепочкой: кеш → hardcoded → auto-detect.

    Использование:
        engine = SelectorEngine()
        engine.probe_and_detect(page, mode='list')  # запуск на живой странице
        sel = engine.get('item')  # получить селектор
    """

    def __init__(self, cache_path: Path = SELECTORS_CACHE_FILE):
        self._cache = SelectorCache(cache_path)
        self._active: dict[str, str] = {}
        self._detected = False

    def get(self, key: str) -> str:
        """Получить селектор по ключу. Fallback: hardcoded."""
        return self._active.get(key) or _HARDCODED.get(key, "")

    def probe_and_detect(self, page: Page, mode: str = "list") -> None:
        """Проверить селекторы на живой странице и при необходимости передетектить.

        1. Проверяем кешированные селекторы
        2. Проверяем hardcoded-селекторы
        3. Если ключевые не работают — запускаем auto-detect
        4. Обновляем кеш
        """
        # Ключевые селекторы, без которых парсинг невозможен
        critical_keys = (
            ["item", "snippet_title"] if mode == "list"
            else ["detail_name"]
        )

        # Шаг 1: пробуем кеш
        cache_ok = True
        for key in critical_keys:
            cached = self._cache.get(key)
            if cached and _probe_selector(page, cached):
                self._active[key] = cached
            else:
                cache_ok = False

        if cache_ok:
            # Загружаем остальные из кеша, fallback на hardcoded
            for key in SELECTOR_KEYS:
                if key not in self._active:
                    cached = self._cache.get(key)
                    if cached:
                        self._active[key] = cached
                    elif key in _HARDCODED:
                        self._active[key] = _HARDCODED[key]
            log.info("Селекторы загружены из кеша (все критичные работают)")
            return

        # Шаг 2: пробуем hardcoded
        hardcoded_ok = True
        for key in critical_keys:
            hc = _HARDCODED.get(key, "")
            if hc and _probe_selector(page, hc):
                self._active[key] = hc
            else:
                hardcoded_ok = False

        if hardcoded_ok:
            for key in SELECTOR_KEYS:
                if key not in self._active and key in _HARDCODED:
                    self._active[key] = _HARDCODED[key]
            log.info("Используются hardcoded-селекторы (все критичные работают)")
            # Сохраняем работающие в кеш
            self._cache.update(self._active)
            self._cache.save()
            return

        # Шаг 3: auto-detect
        log.warning("Hardcoded-селекторы не работают — запускаю автодетект…")
        detected = detect_selectors(page, mode=mode)

        if detected:
            log.info("Автодетект нашёл %d селекторов: %s", len(detected), list(detected.keys()))
            self._active.update(detected)
            self._detected = True

            # Дополняем hardcoded-ами то, что не нашли
            for key in SELECTOR_KEYS:
                if key not in self._active and key in _HARDCODED:
                    self._active[key] = _HARDCODED[key]

            # Сохраняем в кеш
            self._cache.update(self._active)
            self._cache.save()
        else:
            log.error("Автодетект не смог найти селекторы — используем hardcoded как есть")
            self._active = dict(_HARDCODED)

    def detect_detail(self, page: Page) -> None:
        """Детектить селекторы карточки организации (detail page)."""
        # Проверяем критичные detail-селекторы
        critical = ["detail_phone", "detail_website"]
        all_ok = all(
            _probe_selector(page, self.get(k)) for k in critical if self.get(k)
        )

        if not all_ok:
            log.info("Detail-селекторы не работают — запускаю автодетект…")
            detected = detect_selectors(page, mode="detail")
            if detected:
                self._active.update(detected)
                self._cache.update(detected)
                self._cache.save()
                log.info("Detail автодетект: %s", list(detected.keys()))

    def report(self) -> str:
        """Отчёт о состоянии селекторов."""
        lines = ["Активные селекторы:"]
        for key in SELECTOR_KEYS:
            sel = self._active.get(key, "—")
            source = "cache" if self._cache.get(key) == sel else (
                "detected" if self._detected else "hardcoded"
            )
            lines.append(f"  {key:25s} [{source:9s}] {sel}")
        return "\n".join(lines)


# Глобальный экземпляр (инициализируется при первом запуске)
_selector_engine: SelectorEngine | None = None


def get_selector_engine() -> SelectorEngine:
    """Получить или создать глобальный SelectorEngine."""
    global _selector_engine
    if _selector_engine is None:
        _selector_engine = SelectorEngine()
    return _selector_engine


# ---------------------------------------------------------------------------
# Scroll helpers (используют SelectorEngine)
# ---------------------------------------------------------------------------

def _find_scroll_container(page: Page) -> str | None:
    """Найти скроллящийся контейнер с результатами поиска."""
    engine = get_selector_engine()
    sel = engine.get("scroll_container")
    if sel and _probe_selector(page, sel):
        return sel

    # Fallback: JS-поиск по overflow-y
    container_sel = page.evaluate(_JS_DETECT_SCROLL)
    return container_sel


def _human_scroll(page: Page, container_sel: str | None) -> None:
    """Плавный скролл с имитацией поведения человека.

    - Случайная дельта прокрутки (200–700 px)
    - Микро-шаги внутри одного скролла (3–6 шагов с паузами 50–150 мс)
    - Случайные движения мыши в зоне панели результатов
    - Иногда «задумывается» — длинная пауза
    - Иногда скроллит чуть вверх (как человек, вернувшийся посмотреть)
    """
    # Случайное движение мыши в области левой панели
    mouse_x = random.randint(150, 420)
    mouse_y = random.randint(200, 700)
    page.mouse.move(mouse_x, mouse_y)
    page.wait_for_timeout(random.randint(50, 200))

    # Общая дельта этого скролла
    total_delta = random.randint(200, 700)

    # Иногда (15%) скроллим немного вверх — как будто пересматриваем
    if random.random() < 0.15:
        up_delta = random.randint(50, 150)
        _do_scroll_step(page, container_sel, -up_delta)
        page.wait_for_timeout(random.randint(300, 800))

    # Разбиваем на микро-шаги
    steps = random.randint(3, 6)
    for i in range(steps):
        step_delta = total_delta // steps
        # Добавляем немного шума к каждому шагу
        step_delta += random.randint(-20, 20)
        step_delta = max(30, step_delta)

        _do_scroll_step(page, container_sel, step_delta)

        # Микро-пауза между шагами (50–150 мс)
        page.wait_for_timeout(random.randint(50, 150))

    # Иногда (10%) «задумываемся» — длинная пауза
    if random.random() < 0.10:
        think_ms = random.randint(1500, 3500)
        log.debug("Имитация паузы: %d мс", think_ms)
        page.wait_for_timeout(think_ms)


def _do_scroll_step(page: Page, container_sel: str | None, delta: int) -> None:
    """Один шаг прокрутки (через контейнер или mouse.wheel)."""
    if container_sel:
        loc = page.locator(container_sel).first
        if loc.count() > 0:
            loc.evaluate(f"el => el.scrollTop += {delta}")
            return

    # Fallback: колёсико мыши
    page.mouse.wheel(0, delta)


def _get_loaded_count(page: Page) -> int:
    """Вернуть количество подгруженных сниппетов."""
    engine = get_selector_engine()
    return page.locator(engine.get("item")).count()


def _click_show_more(page: Page) -> bool:
    """Нажать кнопку «Показать ещё», если она видна."""
    engine = get_selector_engine()
    sel = engine.get("show_more")
    if not sel:
        return False
    btn = page.locator(sel).first
    if btn.count() > 0 and btn.is_visible():
        btn.click()
        page.wait_for_timeout(1500)
        return True
    return False


# ---------------------------------------------------------------------------
# Streaming scroll + parse: скроллим и парсим на лету
# ---------------------------------------------------------------------------

def scroll_and_parse(
    page: Page,
    max_results: int,
    scroll_pause: float = 1.0,
    on_org: Any = None,
) -> list[Organization]:
    """Скроллить и парсить сниппеты на лету по мере появления.

    Вместо двухэтапного подхода (сначала весь скролл, потом парсинг)
    каждый новый сниппет парсится сразу при появлении.

    Args:
        page: Playwright-страница с результатами поиска.
        max_results: Максимум организаций.
        scroll_pause: Базовая пауза между скроллами (рандомизируется).
        on_org: Callback(org, index) — вызывается при каждой новой организации.

    Returns:
        Список собранных Organization.
    """
    engine = get_selector_engine()
    item_sel = engine.get("item")
    container_sel = _find_scroll_container(page)

    if container_sel:
        log.info("Скролл-контейнер: %s", container_sel)
    else:
        log.warning("Скролл-контейнер не найден, используем mouse.wheel")

    orgs: list[Organization] = []
    parsed_indices: set[int] = set()
    stale_rounds = 0
    max_stale = 10

    # Прогресс-бар (если tqdm установлен)
    pbar = None
    if HAS_TQDM:
        pbar = tqdm(total=max_results, desc="Сбор организаций", unit="орг")

    while True:
        cur_count = page.locator(item_sel).count()

        # Парсим новые сниппеты, которые появились после скролла
        new_parsed = 0
        for i in range(cur_count):
            if i in parsed_indices:
                continue
            if len(orgs) >= max_results:
                break
            try:
                org = parse_snippet(page, i)
                if org.name:
                    orgs.append(org)
                    if on_org:
                        on_org(org, len(orgs))
                    new_parsed += 1
                    if pbar:
                        pbar.update(1)
            except Exception as exc:
                log.debug("Ошибка парсинга сниппета #%d: %s", i, exc)
            parsed_indices.add(i)

        if new_parsed > 0:
            if not pbar:
                log.info("Собрано: %d организаций (новых: +%d)", len(orgs), new_parsed)
            stale_rounds = 0
        else:
            # Новых сниппетов нет — пробуем «Показать ещё»
            if not _click_show_more(page):
                stale_rounds += 1
            else:
                stale_rounds = 0

        # Проверяем лимиты
        if len(orgs) >= max_results:
            log.info("Достигнут лимит: %d / %d", len(orgs), max_results)
            break

        if stale_rounds >= max_stale:
            log.info("Новые результаты не появляются, завершаем (всего %d)", len(orgs))
            break

        # Плавный человеческий скролл
        _human_scroll(page, container_sel)

        # Рандомизированная пауза (±30% от базовой)
        jitter = scroll_pause * random.uniform(0.7, 1.3)
        page.wait_for_timeout(int(jitter * 1000))

    if pbar:
        pbar.close()

    return orgs


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
    engine = get_selector_engine()
    card = page.locator(engine.get("item")).nth(index)
    org = Organization()

    # Название
    org.name = _safe_text(card.locator(engine.get("snippet_title")))

    # Адрес
    sel = engine.get("snippet_address")
    if sel:
        org.address = _safe_text(card.locator(sel))

    # Категория
    sel = engine.get("snippet_category")
    if sel:
        org.category = _safe_text(card.locator(sel))

    # Рейтинг
    sel = engine.get("snippet_rating")
    if sel:
        org.rating = _safe_text(card.locator(sel))

    # Количество отзывов
    sel = engine.get("snippet_reviews")
    if sel:
        raw_reviews = _safe_text(card.locator(sel))
        if raw_reviews:
            org.reviews_count = re.sub(r"[^\d]", "", raw_reviews)

    # Часы работы
    sel = engine.get("snippet_hours")
    if sel:
        org.working_hours = _safe_text(card.locator(sel))

    # Ссылка на карточку
    sel = engine.get("link")
    if sel:
        link = card.locator(sel).first
        if link.count() > 0:
            org.yandex_url = link.get_attribute("href") or ""

    return org


# ---------------------------------------------------------------------------
# Detail page parsing (phone, website, etc.)
# ---------------------------------------------------------------------------

def enrich_from_detail(page: Page, org: Organization, _detail_detected: list[bool] | None = None) -> Organization:
    """Открыть карточку организации и дополнить данные."""
    if not org.yandex_url:
        return org

    engine = get_selector_engine()
    url = org.yandex_url
    if url.startswith("/"):
        url = f"https://yandex.ru{url}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)

        # При первом открытии detail-страницы — детектим селекторы
        if _detail_detected is not None and not _detail_detected[0]:
            engine.detect_detail(page)
            _detail_detected[0] = True

        # Телефон
        sel = engine.get("detail_phone")
        if sel:
            phone_loc = page.locator(sel).first
            if phone_loc.count() > 0:
                org.phone = phone_loc.inner_text().strip()
                if not org.phone:
                    href = phone_loc.get_attribute("href") or ""
                    if href.startswith("tel:"):
                        org.phone = href[4:]

        # Сайт
        sel = engine.get("detail_website")
        if sel:
            site_loc = page.locator(sel).first
            if site_loc.count() > 0:
                org.website = site_loc.inner_text().strip()
                if not org.website:
                    org.website = site_loc.get_attribute("href") or ""

        # Адрес (если не извлекли из сниппета)
        if not org.address:
            sel = engine.get("detail_address")
            if sel:
                org.address = _safe_text(page.locator(sel))

        # Рейтинг (если не извлекли из сниппета)
        if not org.rating:
            sel = engine.get("detail_rating")
            if sel:
                org.rating = _safe_text(page.locator(sel))

        # Количество отзывов (если не извлекли)
        if not org.reviews_count:
            sel = engine.get("detail_reviews_count")
            if sel:
                raw = _safe_text(page.locator(sel))
                if raw:
                    org.reviews_count = re.sub(r"[^\d]", "", raw)

        # Часы работы
        if not org.working_hours:
            sel = engine.get("detail_hours")
            if sel:
                if "meta[itemprop" in sel:
                    hours_meta = page.locator(sel)
                    if hours_meta.count() > 0:
                        parts = []
                        for i in range(hours_meta.count()):
                            val = hours_meta.nth(i).get_attribute("content") or ""
                            if val:
                                parts.append(val)
                        org.working_hours = "; ".join(parts)
                else:
                    org.working_hours = _safe_text(page.locator(sel))

            # Fallback на meta-тег
            if not org.working_hours:
                hours_meta = page.locator("meta[itemprop='openingHours']")
                if hours_meta.count() > 0:
                    parts = []
                    for i in range(hours_meta.count()):
                        val = hours_meta.nth(i).get_attribute("content") or ""
                        if val:
                            parts.append(val)
                    org.working_hours = "; ".join(parts)

        # Категория
        if not org.category:
            sel = engine.get("detail_category")
            if sel:
                org.category = _safe_text(page.locator(sel))

        # Координаты — из URL (ll=lon,lat) или meta-тегов
        if not org.latitude:
            coords = _extract_coords_from_url(page.url)
            if coords:
                org.latitude, org.longitude = coords[0], coords[1]
            else:
                coords = page.evaluate("""() => {
                    // Из meta-тегов
                    const lat = document.querySelector('meta[itemprop="latitude"]');
                    const lng = document.querySelector('meta[itemprop="longitude"]');
                    if (lat && lng) return [lat.content, lng.content];
                    // Из JSON-LD
                    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                    for (const s of scripts) {
                        try {
                            const d = JSON.parse(s.textContent);
                            const geo = d.geo || (d.address && d.address.geo);
                            if (geo) return [String(geo.latitude), String(geo.longitude)];
                        } catch(e) {}
                    }
                    return null;
                }""")
                if coords:
                    org.latitude, org.longitude = coords[0], coords[1]

        # Email
        if not org.email:
            emails = page.evaluate("""() => {
                const links = document.querySelectorAll('a[href^="mailto:"]');
                return Array.from(links).map(a => a.href.replace('mailto:', '')).filter(Boolean);
            }""")
            if emails:
                org.email = "; ".join(emails)

        # Соцсети (VK, Telegram, Instagram, Facebook, OK, YouTube, Twitter/X)
        if not org.social_links:
            socials = page.evaluate("""() => {
                const patterns = [
                    'vk.com', 't.me', 'telegram', 'instagram.com',
                    'facebook.com', 'fb.com', 'ok.ru', 'youtube.com',
                    'twitter.com', 'x.com', 'tiktok.com',
                ];
                const links = document.querySelectorAll('a[href]');
                const found = [];
                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    for (const pat of patterns) {
                        if (href.includes(pat) && !found.includes(href)) {
                            found.push(href);
                            break;
                        }
                    }
                }
                return found;
            }""")
            if socials:
                org.social_links = "; ".join(socials)

    except Exception as exc:
        log.warning("Не удалось открыть карточку %s: %s", org.name, exc)

    return org


def _extract_coords_from_url(url: str) -> tuple[str, str] | None:
    """Извлечь координаты из URL Яндекс.Карт (ll=lon,lat или pt=lon,lat)."""
    for param in ("ll", "pt"):
        match = re.search(rf'{param}=([\d.]+)%2C([\d.]+)|{param}=([\d.]+),([\d.]+)', url)
        if match:
            lon = match.group(1) or match.group(3)
            lat = match.group(2) or match.group(4)
            return (lat, lon)
    return None


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

        # Координаты (из geometry GeoJSON)
        geometry = feat.get("geometry", {})
        coords = geometry.get("coordinates", [])
        if coords and len(coords) >= 2:
            org.longitude = str(coords[0])
            org.latitude = str(coords[1])

        # Email
        emails = company.get("Emails", company.get("emails", []))
        if emails:
            org.email = "; ".join(
                e.get("value", e) if isinstance(e, dict) else str(e)
                for e in emails
            )

        # Соцсети (из Links / links)
        links = company.get("Links", company.get("links", []))
        social_patterns = ("vk.com", "t.me", "instagram", "facebook", "fb.com",
                           "ok.ru", "youtube", "twitter", "x.com", "tiktok")
        social_urls = []
        for link_obj in links:
            href = link_obj.get("href", link_obj.get("url", "")) if isinstance(link_obj, dict) else str(link_obj)
            if any(p in href for p in social_patterns):
                social_urls.append(href)
        if social_urls:
            org.social_links = "; ".join(social_urls)

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
        _human_scroll(page, container_sel)
        _click_show_more(page)

        # Рандомизированная пауза
        jitter = scroll_pause * random.uniform(0.7, 1.3)
        page.wait_for_timeout(int(jitter * 1000))

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


def save_json(orgs: list[Organization], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "total": len(orgs),
        "organizations": [asdict(org) for org in orgs],
    }
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("JSON сохранён: %s (%d записей)", path, len(orgs))


HEADERS_RU = {
    "name": "Название",
    "address": "Адрес",
    "phone": "Телефон",
    "website": "Сайт",
    "rating": "Рейтинг",
    "reviews_count": "Отзывы",
    "category": "Категория",
    "working_hours": "Часы работы",
    "latitude": "Широта",
    "longitude": "Долгота",
    "email": "Email",
    "social_links": "Соцсети",
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

def _create_browser_context(pw, headless: bool, proxy_url: str | None = None):
    """Создать браузер и контекст с общими настройками."""
    launch_args: dict[str, Any] = {"headless": headless}
    if proxy_url:
        rotator = get_proxy_rotator()
        launch_args["proxy"] = rotator.to_playwright_arg(proxy_url)
        log.info("Прокси: %s", proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url)

    browser: Browser = pw.chromium.launch(**launch_args)
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
    on_org: Any = None,
    headless: bool = True,
) -> list[Organization]:
    """Выполнить поиск и собрать результаты (общая логика для всех режимов)."""
    engine = get_selector_engine()
    encoded_query = urllib.parse.quote(query)
    search_url = f"https://yandex.ru/maps/?text={encoded_query}"
    log.info("Открываю %s", search_url)
    page.goto(search_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # Проверка CAPTCHA
    if detect_captcha(page):
        if not handle_captcha(page, headless):
            log.error("CAPTCHA не решена, пропускаю запрос: %s", query)
            return []

    # Ждём появления результатов, пробуем несколько селекторов
    item_sel = engine.get("item")
    found = False
    for sel_candidate in [item_sel, ITEM_SEL, "[class*='search-snippet']", "[class*='serp-item']"]:
        if not sel_candidate:
            continue
        try:
            page.wait_for_selector(sel_candidate, timeout=5000)
            found = True
            break
        except Exception:
            continue

    if not found:
        # Может быть CAPTCHA появилась после загрузки
        if detect_captcha(page):
            if not handle_captcha(page, headless):
                return []
            # Пробуем ещё раз найти результаты
            try:
                page.wait_for_selector(item_sel or ITEM_SEL, timeout=10000)
                found = True
            except Exception:
                pass

    if not found:
        log.warning("Результаты не найдены для запроса: %s", query)
        return []

    # Запускаем автодетект на живой странице с результатами
    engine.probe_and_detect(page, mode="list")

    if api_intercept:
        log.info("Режим API-перехвата")
        orgs = run_api_intercept(page, max_results, scroll_pause)
    else:
        # Стриминг: скроллим + парсим на лету
        orgs = scroll_and_parse(page, max_results, scroll_pause, on_org=on_org)

    log.info("Извлечено организаций: %d", len(orgs))
    return orgs


def _enrich_orgs(ctx: BrowserContext, orgs: list[Organization]) -> None:
    """Обогатить организации данными с карточек."""
    if not orgs:
        return
    log.info("Обогащаю данные с карточек организаций (%d шт)…", len(orgs))
    detail_page = _setup_page(ctx)
    detail_detected = [False]  # мутабельный флаг для одноразовой детекции
    for idx, org in enumerate(orgs):
        enrich_from_detail(detail_page, org, _detail_detected=detail_detected)
        if (idx + 1) % 20 == 0:
            log.info("Обогащено: %d / %d", idx + 1, len(orgs))
    detail_page.close()


def _make_incremental_saver(out_path: Path, save_every: int = 25):
    """Создать callback для инкрементального сохранения по мере сбора.

    Возвращает (on_org_callback, all_orgs_list).
    Каждые save_every организаций промежуточный результат сбрасывается в файл.
    """
    all_orgs: list[Organization] = []

    def on_org(org: Organization, index: int) -> None:
        all_orgs.append(org)
        if index % save_every == 0:
            try:
                if out_path.suffix == ".csv":
                    save_csv(list(all_orgs), out_path)
                else:
                    save_xlsx(list(all_orgs), out_path)
                log.info("Промежуточное сохранение: %d записей → %s", len(all_orgs), out_path)
            except Exception as exc:
                log.debug("Ошибка промежуточного сохранения: %s", exc)

    return on_org, all_orgs


def _save_auto(orgs: list[Organization], out_path: Path) -> None:
    """Сохранить в формат по расширению файла (.xlsx / .csv / .json)."""
    suffix = out_path.suffix.lower()
    if suffix == ".csv":
        save_csv(orgs, out_path)
    elif suffix == ".json":
        save_json(orgs, out_path)
    else:
        save_xlsx(orgs, out_path)


def print_stats(orgs: list[Organization], label: str = "Результаты") -> None:
    """Напечатать сводную статистику по собранным организациям."""
    if not orgs:
        print(f"\n{label}: 0 организаций")
        return

    total = len(orgs)
    with_phone = sum(1 for o in orgs if o.phone)
    with_website = sum(1 for o in orgs if o.website)
    with_email = sum(1 for o in orgs if o.email)
    with_social = sum(1 for o in orgs if o.social_links)
    with_coords = sum(1 for o in orgs if o.latitude)
    with_rating = [float(o.rating.replace(",", ".")) for o in orgs if o.rating]
    avg_rating = sum(with_rating) / len(with_rating) if with_rating else 0

    # Категории (топ-5)
    cat_counts: dict[str, int] = {}
    for o in orgs:
        for c in (o.category or "").split(","):
            c = c.strip()
            if c:
                cat_counts[c] = cat_counts.get(c, 0) + 1
    top_cats = sorted(cat_counts.items(), key=lambda x: -x[1])[:5]

    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}")
    print(f"  Всего организаций:  {total}")
    print(f"  С телефоном:        {with_phone} ({with_phone * 100 // total}%)")
    print(f"  С сайтом:           {with_website} ({with_website * 100 // total}%)")
    print(f"  С email:            {with_email} ({with_email * 100 // total}%)")
    print(f"  С соцсетями:        {with_social} ({with_social * 100 // total}%)")
    print(f"  С координатами:     {with_coords} ({with_coords * 100 // total}%)")
    if with_rating:
        print(f"  Средний рейтинг:    {avg_rating:.1f} (из {len(with_rating)} оценок)")
    if top_cats:
        print(f"  Топ категории:")
        for cat, cnt in top_cats:
            print(f"    {cat}: {cnt}")
    print(f"{'=' * 50}\n")


def run_parser(
    query: str,
    max_results: int = 500,
    output: str = "results.xlsx",
    headless: bool = True,
    detail: bool = False,
    scroll_pause: float = 1.0,
    api_intercept: bool = False,
    proxy_url: str | None = None,
    resume_path: Path | None = None,
) -> list[Organization]:
    """Парсер по одному поисковому запросу."""
    out_path = Path(output)

    # Резюме: загружаем уже собранные данные
    resume = ResumeManager(resume_path) if resume_path else None
    if resume and resume.existing_count > 0:
        log.info("Резюме: %d организаций уже собраны", resume.existing_count)

    with sync_playwright() as pw:
        browser, ctx = _create_browser_context(pw, headless, proxy_url=proxy_url)
        page = _setup_page(ctx)

        orgs = _search_and_collect(
            page, query, max_results, scroll_pause, api_intercept,
            headless=headless,
        )

        for org in orgs:
            org.search_query = query

        # Фильтруем уже известные (из резюме)
        if resume:
            before = len(orgs)
            orgs = [o for o in orgs if not resume.is_known(o.name, o.address)]
            if before != len(orgs):
                log.info("Резюме: пропущено %d уже собранных", before - len(orgs))
            orgs = resume.existing_orgs() + orgs

        if detail:
            _enrich_orgs(ctx, orgs)

        browser.close()

    _save_auto(orgs, out_path)
    print_stats(orgs)
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
    proxy_url: str | None = None,
    resume_path: Path | None = None,
) -> dict[str, list[Organization]]:
    """Парсер по категориям: для каждой категории запускает поиск «категория город»."""
    out_path = Path(output)
    results: dict[str, list[Organization]] = {}
    seen_global: set[str] = set()

    # Резюме
    resume = ResumeManager(resume_path) if resume_path else None
    if resume and resume.existing_count > 0:
        seen_global = resume.existing_keys()
        log.info("Резюме: %d организаций уже собраны", resume.existing_count)

    queries = resolve_categories(categories)
    total_queries = len(queries)
    log.info(
        "Город: %s | Категорий: %d | Макс. на категорию: %d",
        city, total_queries, max_results_per_category,
    )

    # Прогресс по категориям
    cat_iter = enumerate(queries, 1)
    if HAS_TQDM:
        cat_iter_tqdm = tqdm(list(cat_iter), desc="Категории", unit="кат")
    else:
        cat_iter_tqdm = None

    with sync_playwright() as pw:
        browser, ctx = _create_browser_context(pw, headless, proxy_url=proxy_url)
        page = _setup_page(ctx)

        for q_idx, cat_query in (cat_iter_tqdm if cat_iter_tqdm else enumerate(queries, 1)):
            full_query = f"{cat_query} {city}"

            # Пропускаем уже выполненные запросы (резюме)
            if resume and resume.is_query_done(full_query):
                log.info("Пропуск (резюме): %s", full_query)
                continue

            log.info("━━━ [%d/%d] %s ━━━", q_idx, total_queries, full_query)

            orgs = _search_and_collect(
                page, full_query, max_results_per_category,
                scroll_pause, api_intercept, headless=headless,
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
            log.info("Категория «%s»: %d организаций (уникальных)", cat_query, len(unique_orgs))

            # Промежуточное сохранение после каждой категории
            try:
                suffix = out_path.suffix.lower()
                if suffix == ".csv":
                    all_tmp: list[Organization] = []
                    for v in results.values():
                        all_tmp.extend(v)
                    save_csv(all_tmp, out_path)
                elif suffix == ".json":
                    all_tmp = []
                    for v in results.values():
                        all_tmp.extend(v)
                    save_json(all_tmp, out_path)
                else:
                    save_xlsx_by_categories(results, out_path)
                total_so_far = sum(len(v) for v in results.values())
                log.info("Промежуточное сохранение: %d записей → %s", total_so_far, out_path)
            except Exception as exc:
                log.debug("Ошибка промежуточного сохранения: %s", exc)

            # Человеческая пауза между категориями
            if q_idx < total_queries:
                pause = random.randint(1500, 4000)
                page.wait_for_timeout(pause)

        browser.close()

    # Финальное сохранение
    all_orgs: list[Organization] = []
    for v in results.values():
        all_orgs.extend(v)

    # Добавляем ранее собранные (из резюме)
    if resume:
        existing = resume.existing_orgs()
        all_orgs = existing + all_orgs

    suffix = out_path.suffix.lower()
    if suffix == ".json":
        save_json(all_orgs, out_path)
    elif suffix == ".csv":
        save_csv(all_orgs, out_path)
    else:
        save_xlsx_by_categories(results, out_path)

    total_orgs = len(all_orgs)
    log.info("Всего собрано: %d организаций по %d категориям", total_orgs, total_queries)
    print_stats(all_orgs, label=f"Статистика: {city}")
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
  # По запросу
  python yandex_parser.py "кофейни Москва"
  python yandex_parser.py "автосервис Казань" -n 200 -o авто.xlsx

  # По категориям
  python yandex_parser.py --city Москва --category еда
  python yandex_parser.py --city СПб --category авто красота -n 100
  python yandex_parser.py --city Казань --all-categories

  # С прокси
  python yandex_parser.py "аптеки Москва" --proxy http://user:pass@host:port
  python yandex_parser.py --city Москва --category еда --proxy-file proxies.txt

  # Продолжить прерванный сбор
  python yandex_parser.py --city Москва --all-categories --resume Москва_categories.xlsx

  # Экспорт в JSON
  python yandex_parser.py "рестораны Москва" -o results.json

  # С конфиг-файлом
  python yandex_parser.py --config config.yaml

  # Автодетект селекторов / список категорий
  python yandex_parser.py --detect-selectors --no-headless
  python yandex_parser.py --list-categories
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
        help="Файл результатов: .xlsx, .csv или .json (по умолчанию results.xlsx / categories.xlsx)",
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
    parser.add_argument(
        "--proxy", type=str, default=None,
        help="Прокси-сервер (http://host:port или socks5://user:pass@host:port)",
    )
    parser.add_argument(
        "--proxy-file", type=str, default=None,
        help="Файл со списком прокси (одна строка = один прокси, ротация round-robin)",
    )
    parser.add_argument(
        "--resume", type=str, default=None,
        help="Путь к файлу с предыдущими результатами для продолжения сбора",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Конфиг-файл (JSON или YAML) с параметрами парсера",
    )
    parser.add_argument(
        "--detect-selectors", action="store_true",
        help="Открыть Я.Карты, автоматически обнаружить актуальные CSS-селекторы "
             "и сохранить в selectors_cache.json",
    )
    parser.add_argument(
        "--show-selectors", action="store_true",
        help="Показать текущие активные селекторы (из кеша или hardcoded)",
    )
    parser.add_argument(
        "--reset-selectors", action="store_true",
        help="Удалить кеш селекторов и вернуться к hardcoded",
    )

    args = parser.parse_args()

    # Режим: показать текущие селекторы
    if args.show_selectors:
        engine = get_selector_engine()
        cache = engine._cache.all()
        print("\nАктивные CSS-селекторы:\n")
        for key in SELECTOR_KEYS:
            cached = cache.get(key)
            hardcoded = _HARDCODED.get(key, "")
            if cached:
                src = "cache"
                sel = cached
            else:
                src = "hardcoded"
                sel = hardcoded
            print(f"  {key:25s} [{src:9s}]  {sel}")
        print(f"\nФайл кеша: {SELECTORS_CACHE_FILE}")
        if SELECTORS_CACHE_FILE.exists():
            print(f"  (существует, {SELECTORS_CACHE_FILE.stat().st_size} байт)")
        else:
            print("  (не существует — будет создан при первом запуске)")
        return

    # Режим: сбросить кеш
    if args.reset_selectors:
        if SELECTORS_CACHE_FILE.exists():
            SELECTORS_CACHE_FILE.unlink()
            print("Кеш селекторов удалён. Будут использоваться hardcoded-селекторы.")
        else:
            print("Кеш селекторов не найден.")
        return

    # Режим: показать каталог категорий
    if args.list_categories:
        list_categories()
        return

    # Режим: принудительная детекция селекторов
    if args.detect_selectors:
        test_query = args.query or "кофейни Москва"
        print(f"\nЗапуск автодетекта селекторов (запрос: «{test_query}»)…\n")

        # Удаляем старый кеш для чистого детекта
        if SELECTORS_CACHE_FILE.exists():
            SELECTORS_CACHE_FILE.unlink()

        with sync_playwright() as pw:
            browser, ctx = _create_browser_context(pw, headless=not args.no_headless)
            page = _setup_page(ctx)

            encoded = urllib.parse.quote(test_query)
            page.goto(
                f"https://yandex.ru/maps/?text={encoded}",
                wait_until="domcontentloaded", timeout=30000,
            )
            page.wait_for_timeout(4000)

            engine = get_selector_engine()

            # Детект списка
            print("--- Детекция селекторов списка результатов ---")
            list_sels = detect_selectors(page, mode="list")
            for k, v in list_sels.items():
                works = _probe_selector(page, v)
                status = "OK" if works else "??"
                print(f"  [{status}] {k:25s} → {v}")

            # Проверяем hardcoded для сравнения
            print("\n--- Проверка hardcoded-селекторов ---")
            for k, v in _HARDCODED.items():
                if k.startswith("detail_"):
                    continue
                works = _probe_selector(page, v)
                status = "OK" if works else "FAIL"
                print(f"  [{status}] {k:25s} → {v}")

            # Пробуем открыть первую карточку для детекции detail-селекторов
            item_sel = list_sels.get("item") or ITEM_SEL
            link_sel = list_sels.get("link") or LINK_SEL
            card = page.locator(item_sel).first
            if card.count() > 0:
                link = card.locator(link_sel).first
                href = ""
                if link.count() > 0:
                    href = link.get_attribute("href") or ""
                if href:
                    if href.startswith("/"):
                        href = f"https://yandex.ru{href}"
                    print(f"\n--- Детекция селекторов карточки ({href[:60]}…) ---")
                    page.goto(href, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(3000)

                    detail_sels = detect_selectors(page, mode="detail")
                    for k, v in detail_sels.items():
                        works = _probe_selector(page, v)
                        status = "OK" if works else "??"
                        print(f"  [{status}] {k:25s} → {v}")

                    print("\n--- Проверка hardcoded detail-селекторов ---")
                    for k, v in _HARDCODED.items():
                        if not k.startswith("detail_"):
                            continue
                        works = _probe_selector(page, v)
                        status = "OK" if works else "FAIL"
                        print(f"  [{status}] {k:25s} → {v}")

                    list_sels.update(detail_sels)

            browser.close()

        # Сохраняем результат
        if list_sels:
            cache = SelectorCache()
            cache.update(list_sels)
            cache.save()
            print(f"\nСохранено {len(list_sels)} селекторов → {SELECTORS_CACHE_FILE}")
        else:
            print("\nАвтодетект не нашёл селекторов.")
        return

    # Общие параметры
    headless = not args.no_headless

    # Конфиг-файл (перезаписывает дефолты, CLI-аргументы приоритетнее)
    if args.config:
        cfg = load_config(args.config)
        for k, v in cfg.items():
            arg_key = k.replace("-", "_")
            if hasattr(args, arg_key) and getattr(args, arg_key) is None:
                setattr(args, arg_key, v)

    # Прокси
    proxy_url = None
    if args.proxy:
        proxy_url = args.proxy
    elif args.proxy_file:
        global _proxy_rotator
        _proxy_rotator = ProxyRotator.from_file(args.proxy_file)
        proxy_url = _proxy_rotator.next()

    # Резюме
    resume_path = Path(args.resume) if args.resume else None

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
            headless=headless,
            detail=args.detail,
            scroll_pause=args.scroll_pause,
            api_intercept=args.api_intercept,
            proxy_url=proxy_url,
            resume_path=resume_path,
        )

        total = sum(len(v) for v in results.values())
        print(f"\nГотово! Собрано {total} организаций по {len(results)} категориям -> {output}")
        return

    # Режим: обычный поиск по запросу
    if not args.query:
        # Нет аргументов → запускаем интерактивное меню
        interactive_menu()
        return

    output = args.output or "results.xlsx"
    orgs = run_parser(
        query=args.query,
        max_results=args.max_results,
        output=output,
        headless=headless,
        detail=args.detail,
        scroll_pause=args.scroll_pause,
        api_intercept=args.api_intercept,
        proxy_url=proxy_url,
        resume_path=resume_path,
    )

    print(f"\nГотово! Собрано {len(orgs)} организаций -> {output}")


# ---------------------------------------------------------------------------
# Интерактивное меню (для PyCharm / запуска без аргументов)
# ---------------------------------------------------------------------------

def _input_choice(prompt: str, options: list[str], allow_empty: bool = False) -> str:
    """Показать пронумерованный список и запросить выбор."""
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        raw = input(f"\n{prompt} ").strip()
        if allow_empty and raw == "":
            return ""
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        # Может ввели текст напрямую
        if raw in options or allow_empty:
            return raw
        print("  Неверный выбор, попробуйте ещё раз.")


def _input_yn(prompt: str, default: bool = False) -> bool:
    """Да/нет вопрос."""
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"{prompt} {hint}: ").strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes", "д", "да", "1")


def interactive_menu() -> None:
    """Пошаговое интерактивное меню — запускается при старте без аргументов."""
    print()
    print("=" * 55)
    print("  YAmap — Парсер Яндекс.Карт")
    print("=" * 55)
    print()

    # 1. Режим работы
    print("Выберите режим:")
    mode = _input_choice(
        "Номер:",
        [
            "Поиск по запросу",
            "Парсинг по категориям (как 2ГИС)",
            "Все категории города",
            "Показать список категорий",
            "Автодетект селекторов",
        ],
    )

    if mode == "Показать список категорий":
        list_categories()
        return

    if mode == "Автодетект селекторов":
        print("\nЗапускаю автодетект селекторов…")
        # Формируем sys.argv и перезапускаем main
        sys.argv = [sys.argv[0], "--detect-selectors", "--no-headless"]
        main()
        return

    # 2. Город / запрос
    query = None
    city = None
    categories_input: list[str] = []

    if mode == "Поиск по запросу":
        query = input("\nПоисковый запрос (напр. кофейни Москва): ").strip()
        if not query:
            print("Запрос не может быть пустым!")
            return

    elif mode in ("Парсинг по категориям (как 2ГИС)", "Все категории города"):
        city = input("\nГород: ").strip()
        if not city:
            print("Город не может быть пустым!")
            return

        if mode == "Парсинг по категориям (как 2ГИС)":
            print("\nДоступные группы категорий:")
            groups = list(CATEGORIES.keys())
            for i, g in enumerate(groups, 1):
                count = len(CATEGORIES[g])
                examples = ", ".join(CATEGORIES[g][:3])
                print(f"  {i:2d}. {g:15s} ({count} шт: {examples}…)")

            print(f"\nВведите номера через пробел (напр. 1 3 5)")
            print(f"Или названия: еда авто красота")
            raw = input("\nКатегории: ").strip()
            if not raw:
                print("Категории не выбраны!")
                return

            parts = raw.split()
            for p in parts:
                if p.isdigit():
                    idx = int(p) - 1
                    if 0 <= idx < len(groups):
                        categories_input.append(groups[idx])
                else:
                    categories_input.append(p)
        else:
            categories_input = list(CATEGORIES.keys())
            print(f"\nБудут спарсены ВСЕ {len(categories_input)} групп категорий")

    # 3. Максимум результатов
    raw_max = input("\nМаксимум организаций на запрос [500]: ").strip()
    max_results = int(raw_max) if raw_max.isdigit() else 500

    # 4. Формат вывода
    print("\nФормат сохранения:")
    fmt = _input_choice("Номер:", ["Excel (.xlsx)", "CSV (.csv)", "JSON (.json)"])
    ext_map = {"Excel (.xlsx)": ".xlsx", "CSV (.csv)": ".csv", "JSON (.json)": ".json"}
    ext = ext_map[fmt]

    # Имя файла
    if city:
        default_name = f"{city}_categories{ext}"
    elif query:
        safe_name = re.sub(r'[^\w\s-]', '', query)[:30].strip().replace(' ', '_')
        default_name = f"{safe_name}{ext}"
    else:
        default_name = f"results{ext}"

    raw_output = input(f"\nИмя файла [{default_name}]: ").strip()
    output = raw_output if raw_output else default_name

    # 5. Дополнительные опции
    print("\n--- Дополнительные настройки ---")
    detail = _input_yn("Открывать карточки для телефона/сайта? (медленнее, но больше данных)", False)
    api_intercept = _input_yn("Режим перехвата API? (надёжнее, данные из JSON)", False)
    headless = not _input_yn("Показывать браузер? (для отладки)", False)

    # 6. Прокси
    proxy_url = None
    if _input_yn("Использовать прокси?", False):
        print("\n  1. Один прокси (ввести вручную)")
        print("  2. Файл с прокси-списком")
        proxy_mode = input("\nНомер [1]: ").strip()
        if proxy_mode == "2":
            proxy_file = input("Путь к файлу с прокси: ").strip()
            if proxy_file:
                global _proxy_rotator
                _proxy_rotator = ProxyRotator.from_file(proxy_file)
                proxy_url = _proxy_rotator.next()
        else:
            proxy_url = input("Прокси (http://host:port): ").strip() or None

    # 7. Резюме
    resume_path = None
    existing_file = Path(output)
    if existing_file.exists():
        if _input_yn(f"Файл {output} уже существует. Продолжить сбор (resume)?", True):
            resume_path = existing_file

    # 8. Подтверждение
    print("\n" + "=" * 55)
    print("  Параметры запуска:")
    print("=" * 55)
    if query:
        print(f"  Режим:       поиск по запросу")
        print(f"  Запрос:      {query}")
    else:
        print(f"  Режим:       по категориям")
        print(f"  Город:       {city}")
        print(f"  Категории:   {', '.join(categories_input)}")
    print(f"  Макс. орг:   {max_results}")
    print(f"  Файл:        {output}")
    print(f"  Detail:      {'да' if detail else 'нет'}")
    print(f"  API-перехват: {'да' if api_intercept else 'нет'}")
    print(f"  Браузер:     {'видимый' if not headless else 'скрытый'}")
    print(f"  Прокси:      {proxy_url or 'нет'}")
    print(f"  Резюме:      {resume_path or 'нет'}")
    print("=" * 55)

    if not _input_yn("\nЗапустить?", True):
        print("Отменено.")
        return

    print()

    # 9. Запуск
    if query:
        orgs = run_parser(
            query=query,
            max_results=max_results,
            output=output,
            headless=headless,
            detail=detail,
            scroll_pause=1.0,
            api_intercept=api_intercept,
            proxy_url=proxy_url,
            resume_path=resume_path,
        )
        print(f"\nГотово! Собрано {len(orgs)} организаций -> {output}")
    else:
        results = run_category_parser(
            city=city,
            categories=categories_input,
            max_results_per_category=max_results,
            output=output,
            headless=headless,
            detail=detail,
            scroll_pause=1.0,
            api_intercept=api_intercept,
            proxy_url=proxy_url,
            resume_path=resume_path,
        )
        total = sum(len(v) for v in results.values())
        print(f"\nГотово! Собрано {total} организаций по {len(results)} категориям -> {output}")


if __name__ == "__main__":
    main()
