"""CSS-селекторы, автодетект, SelectorEngine."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from patchright.sync_api import Page

log = logging.getLogger("yandex_parser")


SEARCH_INPUT_SEL = (
    "[class*='search-form-view__input'] input, "
    "input.input__control, "
    "input[placeholder*='Поиск' i], "
    "input[aria-label*='Поиск' i]"
)
# Кнопка поиска
SEARCH_BUTTON_SEL = (
    "[class*='small-search-form-view__button'], "
    "[class*='search-form-view'] button[type='submit'], "
    "button[aria-label*='Найти' i]"
)

# Контейнер прокрутки результатов
SCROLL_CONTAINER_SEL = (
    "[class*='scroll__container'], "
    "[class*='search-list-view'], "
    "[class*='sidebar'] [class*='scroll']"
)
# Ползунок скроллбара (для определения наличия прокрутки)
SCROLLBAR_THUMB_SEL = "[class*='scroll__scrollbar-thumb']"

# Один сниппет организации в списке результатов
ITEM_SEL = (
    "[class*='search-snippet-view'], "
    "[class*='search-business-snippet-view'], "
    "li[class*='search-snippet']"
)
# Ссылка-оверлей на карточку организации
LINK_SEL = (
    "[class*='search-snippet-view__link-overlay'], "
    "a[href*='/maps/org/'][class*='link-overlay'], "
    "a[href*='/maps/org/']"
)
# Кнопка «Показать ещё»
SHOW_MORE_SEL = (
    "[class*='show-more'] button, "
    "[class*='search-list-view__more'] button, "
    "button[class*='more'][class*='button']"
)

# -- Поля внутри сниппета (список результатов) --
SNIPPET_TITLE_SEL = (
    "[class*='search-business-snippet-view__title'], "
    "[class*='snippet-view__title'], "
    "[class*='search-snippet-view__title']"
)
SNIPPET_ADDRESS_SEL = (
    "[class*='search-business-snippet-view__address'], "
    "[class*='snippet-view__address']"
)
SNIPPET_CATEGORY_SEL = (
    "[class*='search-business-snippet-view__categories'], "
    "[class*='snippet-view__categories']"
)
SNIPPET_HOURS_SEL = (
    "[class*='search-business-snippet-view__open-hours'], "
    "[class*='business-working-status-view__text']"
)
SNIPPET_RATING_SEL = (
    "[class*='business-rating-badge-view__rating-text'], "
    "[class*='business-summary-rating-badge-view__rating-text'], "
    "span[itemprop='ratingValue']"
)
SNIPPET_REVIEWS_SEL = (
    "[class*='business-rating-badge-view__rating-count'], "
    "[class*='business-rating-amount-view'], "
    "meta[itemprop='reviewCount']"
)

# -- Карточка организации (detail page) --
DETAIL_NAME_SEL = (
    "h1[class*='orgpage-header-view__header'], "
    "h1[class*='card-title-view__title'], "
    "h1[itemprop='name']"
)
DETAIL_ADDRESS_SEL = (
    "[class*='business-contacts-view__address-link'], "
    "[class*='orgpage-header-view__address'], "
    "[class*='card-title-view__subtitle'], "
    "[class*='business-contacts-view__address'], "
    "a[href*='/maps/'][itemprop='address'], "
    "[itemprop='address']"
)
DETAIL_PHONE_SEL = (
    "[class*='card-phones-view__phone-number'], "
    "[class*='orgpage-phones-view__phone-number'], "
    "[class*='business-phones-view__phone'], "
    "a[href^='tel:'], "
    "[itemprop='telephone']"
)
DETAIL_WEBSITE_SEL = (
    "[class*='business-urls-view__text'], "
    "[class*='business-urls-view'] a[href], "
    "[class*='card-feature-view__content'] a[href], "
    "[class*='orgpage-feature-view__content'] a[href], "
    "a[itemprop='url']"
)
DETAIL_RATING_SEL = (
    "[class*='business-summary-rating-badge-view__rating-text'], "
    "[class*='business-rating-badge-view__rating-text'], "
    "span[itemprop='ratingValue']"
)
DETAIL_REVIEWS_COUNT_SEL = (
    "[class*='tabs-select-view__counter'], "
    "[class*='business-header-rating-view__text'], "
    "meta[itemprop='reviewCount']"
)
DETAIL_HOURS_SEL = (
    "meta[itemprop='openingHours'], "
    "[class*='business-working-status-view__text'], "
    "[class*='business-working-intervals-view'], "
    "[itemprop='openingHours']"
)
DETAIL_CATEGORY_SEL = (
    "[class*='business-categories-view__category'], "
    "[class*='breadcrumbs-view__text'], "
    "a[class*='breadcrumbs__link']"
)


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

