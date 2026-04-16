"""Скролл, парсинг, API-перехват, обогащение карточек."""

from __future__ import annotations

import logging
import random
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from patchright.sync_api import BrowserContext, Page, Response

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from . import browser as _browser
from .models import Organization, _dedup_key, _filter_valid
from .selectors import (
    ITEM_SEL,
    _JS_DETECT_SCROLL,
    _probe_selector,
    get_selector_engine,
)
from .export import save_csv, save_xlsx

log = logging.getLogger("yandex_parser")


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
        # Graceful shutdown — прервать скролл, сохранить что есть
        if _browser._shutdown_requested:
            log.warning("Graceful shutdown — прерываю скролл (собрано %d)", len(orgs))
            break

        cur_count = page.locator(item_sel).count()

        # Парсим новые сниппеты, которые появились после скролла
        new_parsed = 0
        for i in range(cur_count):
            if i in parsed_indices:
                continue
            if len(orgs) >= max_results:
                break
            if _browser._shutdown_requested:
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

def enrich_from_detail(
    page: Page,
    org: Organization,
    _detail_detected: list[bool] | None = None,
    budget_sec: float = 25.0,
) -> Organization:
    """Открыть карточку организации и дополнить данные.

    Args:
        budget_sec: Общий бюджет времени на обогащение одной карточки.
                    Если превышен — прерываем дальнейшие извлечения.
    """
    if not org.yandex_url:
        return org

    engine = get_selector_engine()
    url = org.yandex_url
    if url.startswith("/"):
        url = f"https://yandex.kz{url}"

    deadline = time.monotonic() + budget_sec

    def over_budget() -> bool:
        return time.monotonic() > deadline

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)
        if over_budget():
            log.debug("enrich_from_detail: бюджет исчерпан на загрузке %s", org.name)
            return org

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

        if over_budget():
            return org

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

        if over_budget():
            return org

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

        # Телефоны — все номера через "; "
        phones = company.get("Phones", company.get("phones", []))
        if phones:
            phone_list = []
            for ph in phones:
                num = ph.get("formatted") or ph.get("number") or ph.get("value", "")
                if num and num not in phone_list:
                    phone_list.append(num)
            org.phone = "; ".join(phone_list)

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

        # Рейтинг и отзывы
        rating_val = (
            company.get("rating", {}) if isinstance(company.get("rating"), dict)
            else props.get("rating", {}) if isinstance(props.get("rating"), dict)
            else {}
        )
        if isinstance(rating_val, dict):
            org.rating = str(rating_val.get("value", rating_val.get("score", "")))
            org.reviews_count = str(rating_val.get("ratings", rating_val.get("count", "")))
        # Fallback: рейтинг как простое число
        if not org.rating:
            for key in ("rating", "Rating", "score"):
                val = company.get(key) or props.get(key)
                if val and not isinstance(val, dict):
                    org.rating = str(val)
                    break
        # Fallback: кол-во отзывов
        if not org.reviews_count:
            for key in ("reviewCount", "ratingCount", "totalRatings"):
                val = company.get(key) or props.get(key)
                if val:
                    org.reviews_count = str(val)
                    break

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
    on_org: Any = None,
) -> list[Organization]:
    """Скроллить и перехватывать JSON из XHR-ответов."""
    all_orgs: list[Organization] = []
    seen_names: set[str] = set()

    # Расширенный список паттернов API Яндекс Карт (актуально на 2025).
    # Сюда попадают и старые ручки `/maps/api/...`, и новые pmaps2.
    API_PATTERNS = (
        "/maps/api/search",
        "/maps/api/business",
        "/maps/api/searchOrgs",
        "searchBusinesses",
        "search?",
        "/search/v2",
        "pmaps2",
        "yandsearch",
    )
    SKIP_PATTERNS = ("csrfToken", "suggest", "geocode", "metrika", "matomo")

    def on_response(response: Response) -> None:
        url = response.url
        if any(s in url for s in SKIP_PATTERNS):
            return
        if not any(p in url for p in API_PATTERNS):
            return
        if response.status != 200:
            return
        ct = (response.headers or {}).get("content-type", "")
        if "json" not in ct.lower():
            return

        try:
            body = response.json()
        except Exception:
            return

        orgs = _extract_orgs_from_api_response(body)
        added = 0
        for org in orgs:
            key = _dedup_key(org)
            if key not in seen_names:
                seen_names.add(key)
                all_orgs.append(org)
                added += 1
                if on_org:
                    try:
                        on_org(org, len(all_orgs))
                    except Exception as e:
                        log.debug("on_org callback error: %s", e)

        if added:
            log.info("API перехвачено: +%d (всего %d)", added, len(all_orgs))

    page.on("response", on_response)

    container_sel = _find_scroll_container(page)
    stale_rounds = 0

    pbar = None
    if HAS_TQDM:
        pbar = tqdm(total=max_results, desc="API-перехват", unit="орг")

    while len(all_orgs) < max_results:
        # Graceful shutdown
        if _browser._shutdown_requested:
            log.warning("Graceful shutdown — прерываю API-перехват (собрано %d)", len(all_orgs))
            break

        prev = len(all_orgs)
        _human_scroll(page, container_sel)
        _click_show_more(page)

        # Рандомизированная пауза
        jitter = scroll_pause * random.uniform(0.7, 1.3)
        page.wait_for_timeout(int(jitter * 1000))

        new_count = len(all_orgs) - prev
        if new_count > 0:
            stale_rounds = 0
            if pbar:
                pbar.update(new_count)
        else:
            stale_rounds += 1

        if stale_rounds >= 12:
            log.info("API: новые данные не поступают, завершаем (всего %d)", len(all_orgs))
            break

    if pbar:
        pbar.close()

    page.remove_listener("response", on_response)
    return all_orgs[:max_results]


def _search_and_collect(
    page: Page,
    query: str,
    max_results: int,
    scroll_pause: float,
    api_intercept: bool,
    on_org: Any = None,
    headless: bool = True,
    geo: Any = None,
) -> list[Organization]:
    """Выполнить поиск и собрать результаты (общая логика для всех режимов).

    ``geo`` — опциональный GeoEntry для ограничения поиска регионом.
    Если задан, прогрев и fallback-goto используют URL
    ``/maps/<geo_id>/<slug>/`` вместо общего ``/maps/``.
    """
    engine = get_selector_engine()

    # Прогрев при первом запросе
    _browser._warmup(page, headless, geo=geo)

    log.info("Поиск: %s", query)

    # Адаптивная пауза перед запросом (замедляемся если были капчи)
    throttle = _browser.get_throttle()
    pre_pause = throttle.get_pause(base_ms=1000)
    page.wait_for_timeout(pre_pause)

    # Вводим запрос через строку поиска (как человек)
    _browser._do_search(page, query, headless, geo=geo)
    page.wait_for_timeout(random.randint(2500, 4500))

    # Проверка CAPTCHA
    if _browser.detect_captcha(page):
        throttle.on_captcha()
        if not _browser.handle_captcha(page, headless):
            log.error("CAPTCHA не решена, пропускаю запрос: %s", query)
            return []
    else:
        throttle.on_success()

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
        if _browser.detect_captcha(page):
            if not _browser.handle_captcha(page, headless):
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
        orgs = run_api_intercept(page, max_results, scroll_pause, on_org=on_org)
    else:
        # Стриминг: скроллим + парсим на лету
        orgs = scroll_and_parse(page, max_results, scroll_pause, on_org=on_org)

    log.info("Извлечено организаций: %d", len(orgs))
    return orgs


def _search_with_retry(
    page: Page,
    query: str,
    max_results: int,
    scroll_pause: float,
    api_intercept: bool,
    on_org: Any = None,
    headless: bool = True,
    max_retries: int = 3,
    geo: Any = None,
) -> list[Organization]:
    """Обёртка над _search_and_collect с retry при ошибках."""
    for attempt in range(1, max_retries + 1):
        try:
            orgs = _search_and_collect(
                page, query, max_results, scroll_pause,
                api_intercept, on_org=on_org, headless=headless, geo=geo,
            )
            if orgs:
                return orgs
            # Пустой результат — может стоит попробовать ещё
            if attempt < max_retries:
                log.warning("Пустой результат для «%s», попытка %d/%d…",
                            query, attempt, max_retries)
                page.wait_for_timeout(random.randint(5000, 10000))
            else:
                return []
        except Exception as exc:
            if attempt < max_retries:
                wait_sec = attempt * 10
                log.warning("Ошибка при поиске «%s»: %s. Retry через %d сек (попытка %d/%d)",
                            query, exc, wait_sec, attempt, max_retries)
                page.wait_for_timeout(wait_sec * 1000)
                # Возвращаемся на карты перед retry (в регион если задан geo)
                try:
                    page.goto(
                        _browser._maps_url(geo),
                        wait_until="domcontentloaded", timeout=15000,
                    )
                    page.wait_for_timeout(random.randint(2000, 4000))
                except Exception:
                    pass
            else:
                log.error("Не удалось выполнить поиск «%s» после %d попыток: %s",
                          query, max_retries, exc)
                return []
    return []


def _enrich_orgs(ctx: BrowserContext, orgs: list[Organization], n_tabs: int = 3) -> None:
    """Обогатить организации данными с карточек.

    Использует n_tabs параллельных вкладок для ускорения.
    """
    if not orgs:
        return
    log.info("Обогащаю данные с карточек организаций (%d шт, %d вкладок)…", len(orgs), n_tabs)

    # Создаём пул вкладок
    pages: list[Page] = []
    for _ in range(min(n_tabs, len(orgs))):
        try:
            pages.append(ctx.new_page())
        except Exception:
            break
    if not pages:
        pages.append(ctx.new_page())

    detail_detected = [False]
    pbar = None
    if HAS_TQDM:
        pbar = tqdm(total=len(orgs), desc="Обогащение карточек", unit="орг")

    # Распределяем организации по вкладкам round-robin
    for idx, org in enumerate(orgs):
        # Graceful shutdown — останавливаем обогащение
        if _browser._shutdown_requested:
            log.warning(
                "Graceful shutdown — прерываю обогащение (%d/%d)",
                idx, len(orgs),
            )
            break

        page = pages[idx % len(pages)]
        enrich_from_detail(page, org, _detail_detected=detail_detected)
        if pbar:
            pbar.update(1)
        elif (idx + 1) % 20 == 0:
            log.info("Обогащено: %d / %d", idx + 1, len(orgs))
        # Небольшая пауза между карточками (не бомбить сервер)
        page.wait_for_timeout(random.randint(300, 800))

    if pbar:
        pbar.close()

    for p in pages:
        try:
            p.close()
        except Exception:
            pass


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


