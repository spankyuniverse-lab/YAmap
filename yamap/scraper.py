"""Search + scroll + parse + enrich + API intercept."""

from __future__ import annotations

import logging
import random
import re
import urllib.parse
from typing import Any

from ._playwright import BrowserContext, Page, Response
from .browser import type_like_human, warmup
from .captcha import detect_captcha, handle_captcha
from .models import Organization, dedup_key
from .selectors import (
    ITEM_SEL,
    _JS_DETECT_SCROLL,
    _probe_selector,
    get_selector_engine,
)
from .throttle import get_throttle


log = logging.getLogger("yandex_parser")


try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


# ---------------------------------------------------------------------------
# Scroll helpers
# ---------------------------------------------------------------------------

def _find_scroll_container(page: Page) -> str | None:
    """Найти скроллящийся контейнер с результатами поиска."""
    engine = get_selector_engine()
    sel = engine.get("scroll_container")
    if sel and _probe_selector(page, sel):
        return sel
    return page.evaluate(_JS_DETECT_SCROLL)


def _do_scroll_step(page: Page, container_sel: str | None, delta: int) -> None:
    """Один шаг прокрутки (через контейнер или mouse.wheel)."""
    if container_sel:
        loc = page.locator(container_sel).first
        if loc.count() > 0:
            loc.evaluate(f"el => el.scrollTop += {delta}")
            return
    page.mouse.wheel(0, delta)


def _human_scroll(page: Page, container_sel: str | None) -> None:
    """Плавный скролл с имитацией поведения человека.

    Микро-шаги (3-6 шт), случайные движения мыши, иногда обратный скролл.
    """
    mouse_x = random.randint(150, 420)
    mouse_y = random.randint(200, 700)
    page.mouse.move(mouse_x, mouse_y)
    page.wait_for_timeout(random.randint(50, 200))

    total_delta = random.randint(200, 700)

    # 15%: скроллим немного вверх — как будто пересматриваем
    if random.random() < 0.15:
        up_delta = random.randint(50, 150)
        _do_scroll_step(page, container_sel, -up_delta)
        page.wait_for_timeout(random.randint(300, 800))

    steps = random.randint(3, 6)
    for _ in range(steps):
        step_delta = total_delta // steps
        step_delta += random.randint(-20, 20)
        step_delta = max(30, step_delta)
        _do_scroll_step(page, container_sel, step_delta)
        page.wait_for_timeout(random.randint(50, 150))

    # 10%: «задумываемся»
    if random.random() < 0.10:
        think_ms = random.randint(1500, 3500)
        log.debug("Имитация паузы: %d мс", think_ms)
        page.wait_for_timeout(think_ms)


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
# Snippet parsing (DOM)
# ---------------------------------------------------------------------------

def _safe_text(loc) -> str:
    if loc.count() > 0:
        return loc.first.inner_text().strip()
    return ""


def parse_snippet(page: Page, index: int) -> Organization:
    """Извлечь данные из одного сниппета по индексу."""
    engine = get_selector_engine()
    card = page.locator(engine.get("item")).nth(index)
    org = Organization()

    org.name = _safe_text(card.locator(engine.get("snippet_title")))

    sel = engine.get("snippet_address")
    if sel:
        org.address = _safe_text(card.locator(sel))

    sel = engine.get("snippet_category")
    if sel:
        org.category = _safe_text(card.locator(sel))

    sel = engine.get("snippet_rating")
    if sel:
        org.rating = _safe_text(card.locator(sel))

    sel = engine.get("snippet_reviews")
    if sel:
        raw_reviews = _safe_text(card.locator(sel))
        if raw_reviews:
            org.reviews_count = re.sub(r"[^\d]", "", raw_reviews)

    sel = engine.get("snippet_hours")
    if sel:
        org.working_hours = _safe_text(card.locator(sel))

    sel = engine.get("link")
    if sel:
        link = card.locator(sel).first
        if link.count() > 0:
            org.yandex_url = link.get_attribute("href") or ""

    return org


def scroll_and_parse(
    page: Page,
    max_results: int,
    scroll_pause: float = 1.0,
    on_org: Any = None,
) -> list[Organization]:
    """Скроллить и парсить сниппеты на лету по мере появления."""
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

    pbar = None
    if HAS_TQDM:
        pbar = tqdm(total=max_results, desc="Сбор организаций", unit="орг")

    while True:
        cur_count = page.locator(item_sel).count()

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
            if not _click_show_more(page):
                stale_rounds += 1
            else:
                stale_rounds = 0

        if len(orgs) >= max_results:
            log.info("Достигнут лимит: %d / %d", len(orgs), max_results)
            break

        if stale_rounds >= max_stale:
            log.info("Новые результаты не появляются, завершаем (всего %d)", len(orgs))
            break

        _human_scroll(page, container_sel)

        jitter = scroll_pause * random.uniform(0.7, 1.3)
        page.wait_for_timeout(int(jitter * 1000))

    if pbar:
        pbar.close()

    return orgs


# ---------------------------------------------------------------------------
# Detail page enrichment
# ---------------------------------------------------------------------------

def _extract_coords_from_url(url: str) -> tuple[str, str] | None:
    """Извлечь координаты из URL Яндекс.Карт (ll=lon,lat или pt=lon,lat)."""
    for param in ("ll", "pt"):
        match = re.search(rf'{param}=([\d.]+)%2C([\d.]+)|{param}=([\d.]+),([\d.]+)', url)
        if match:
            lon = match.group(1) or match.group(3)
            lat = match.group(2) or match.group(4)
            return (lat, lon)
    return None


def enrich_from_detail(
    page: Page,
    org: Organization,
    _detail_detected: list[bool] | None = None,
) -> Organization:
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

        if _detail_detected is not None and not _detail_detected[0]:
            engine.detect_detail(page)
            _detail_detected[0] = True

        sel = engine.get("detail_phone")
        if sel:
            phone_loc = page.locator(sel).first
            if phone_loc.count() > 0:
                org.phone = phone_loc.inner_text().strip()
                if not org.phone:
                    href = phone_loc.get_attribute("href") or ""
                    if href.startswith("tel:"):
                        org.phone = href[4:]

        sel = engine.get("detail_website")
        if sel:
            site_loc = page.locator(sel).first
            if site_loc.count() > 0:
                org.website = site_loc.inner_text().strip()
                if not org.website:
                    org.website = site_loc.get_attribute("href") or ""

        if not org.address:
            sel = engine.get("detail_address")
            if sel:
                org.address = _safe_text(page.locator(sel))

        if not org.rating:
            sel = engine.get("detail_rating")
            if sel:
                org.rating = _safe_text(page.locator(sel))

        if not org.reviews_count:
            sel = engine.get("detail_reviews_count")
            if sel:
                raw = _safe_text(page.locator(sel))
                if raw:
                    org.reviews_count = re.sub(r"[^\d]", "", raw)

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

            if not org.working_hours:
                hours_meta = page.locator("meta[itemprop='openingHours']")
                if hours_meta.count() > 0:
                    parts = []
                    for i in range(hours_meta.count()):
                        val = hours_meta.nth(i).get_attribute("content") or ""
                        if val:
                            parts.append(val)
                    org.working_hours = "; ".join(parts)

        if not org.category:
            sel = engine.get("detail_category")
            if sel:
                org.category = _safe_text(page.locator(sel))

        # Координаты — из URL или meta-тегов / JSON-LD
        if not org.latitude:
            coords = _extract_coords_from_url(page.url)
            if coords:
                org.latitude, org.longitude = coords[0], coords[1]
            else:
                meta_coords = page.evaluate("""() => {
                    const lat = document.querySelector('meta[itemprop="latitude"]');
                    const lng = document.querySelector('meta[itemprop="longitude"]');
                    if (lat && lng) return [lat.content, lng.content];
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
                if meta_coords:
                    org.latitude, org.longitude = meta_coords[0], meta_coords[1]

        if not org.email:
            emails = page.evaluate("""() => {
                const links = document.querySelectorAll('a[href^="mailto:"]');
                return Array.from(links).map(a => a.href.replace('mailto:', '')).filter(Boolean);
            }""")
            if emails:
                org.email = "; ".join(emails)

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


# ---------------------------------------------------------------------------
# API intercept
# ---------------------------------------------------------------------------

def _extract_orgs_from_api_response(data: dict) -> list[Organization]:
    """Извлечь организации из JSON-ответа внутреннего API."""
    orgs: list[Organization] = []

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

        phones = company.get("Phones", company.get("phones", []))
        if phones:
            phone_list = []
            for ph in phones:
                num = ph.get("formatted") or ph.get("number") or ph.get("value", "")
                if num and num not in phone_list:
                    phone_list.append(num)
            org.phone = "; ".join(phone_list)

        url_obj = company.get("url", company.get("Url", ""))
        if isinstance(url_obj, str):
            org.website = url_obj
        elif isinstance(url_obj, dict):
            org.website = url_obj.get("value", "")

        hours = company.get("Hours", company.get("hours", {}))
        if isinstance(hours, dict):
            org.working_hours = hours.get("text", "")

        company_rating = company.get("rating")
        props_rating = props.get("rating")
        if isinstance(company_rating, dict):
            rating_val = company_rating
        elif isinstance(props_rating, dict):
            rating_val = props_rating
        else:
            rating_val = {}
        if isinstance(rating_val, dict):
            org.rating = str(rating_val.get("value", rating_val.get("score", "")))
            org.reviews_count = str(rating_val.get("ratings", rating_val.get("count", "")))
        if not org.rating:
            for key in ("rating", "Rating", "score"):
                val = company.get(key) or props.get(key)
                if val and not isinstance(val, dict):
                    org.rating = str(val)
                    break
        if not org.reviews_count:
            for key in ("reviewCount", "ratingCount", "totalRatings"):
                val = company.get(key) or props.get(key)
                if val:
                    org.reviews_count = str(val)
                    break

        categories = company.get("Categories", company.get("categories", []))
        if categories:
            cat_names = [c.get("name", "") for c in categories if c.get("name")]
            org.category = ", ".join(cat_names)

        geometry = feat.get("geometry", {})
        coords = geometry.get("coordinates", [])
        if coords and len(coords) >= 2:
            org.longitude = str(coords[0])
            org.latitude = str(coords[1])

        emails = company.get("Emails", company.get("emails", []))
        if emails:
            org.email = "; ".join(
                e.get("value", e) if isinstance(e, dict) else str(e)
                for e in emails
            )

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
    import json as _json

    all_orgs: list[Organization] = []
    seen_names: set[str] = set()

    def on_response(response: Response) -> None:
        url = response.url
        api_patterns = [
            "/maps/api/search",
            "/maps/api/business",
            "searchBusinesses",
            "/search/",
        ]
        if not any(p in url for p in api_patterns):
            return
        if response.status != 200:
            return

        try:
            body = response.json()
        except (_json.JSONDecodeError, ValueError) as exc:
            log.debug("API-ответ не JSON (%s): %s", exc, url[:80])
            return

        orgs = _extract_orgs_from_api_response(body)
        for org in orgs:
            key = dedup_key(org)
            if key not in seen_names:
                seen_names.add(key)
                all_orgs.append(org)

        if orgs:
            log.info("API перехвачено: +%d (всего %d)", len(orgs), len(all_orgs))

    page.on("response", on_response)

    container_sel = _find_scroll_container(page)
    stale_rounds = 0

    pbar = None
    if HAS_TQDM:
        pbar = tqdm(total=max_results, desc="API-перехват", unit="орг")

    while len(all_orgs) < max_results:
        prev = len(all_orgs)
        _human_scroll(page, container_sel)
        _click_show_more(page)

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


# ---------------------------------------------------------------------------
# Search orchestration
# ---------------------------------------------------------------------------

def _do_search(page: Page, query: str, headless: bool) -> bool:
    """Выполнить поиск: ввести запрос в строку поиска как человек.

    Fallback: если строка поиска не найдена — goto по URL.
    """
    search_sels = [
        "input[class*='input__control']",
        "input[class*='search-form']",
        "[class*='search-form-view__input'] input",
        "input[placeholder*='Поиск']",
        "input[aria-label*='Поиск']",
    ]
    last_exc: Exception | None = None
    for sel in search_sels:
        try:
            inp = page.locator(sel).first
            if inp.count() > 0 and inp.is_visible():
                log.info("Ввожу запрос в строку поиска: %s", query)
                type_like_human(page, sel, query)
                page.wait_for_timeout(random.randint(300, 700))
                inp.press("Enter")
                return True
        except Exception as exc:
            last_exc = exc
            continue

    if last_exc:
        log.debug("Не удалось использовать строку поиска (%s) — fallback goto", last_exc)
    else:
        log.debug("Строка поиска не найдена — используем goto")
    encoded_query = urllib.parse.quote(query)
    page.goto(
        f"https://yandex.ru/maps/?text={encoded_query}",
        wait_until="domcontentloaded", timeout=30000,
    )
    return True


def search_and_collect(
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

    warmup(page, headless)

    log.info("Поиск: %s", query)

    throttle = get_throttle()
    pre_pause = throttle.get_pause(base_ms=1000)
    page.wait_for_timeout(pre_pause)

    _do_search(page, query, headless)
    page.wait_for_timeout(random.randint(2500, 4500))

    if detect_captcha(page):
        throttle.on_captcha()
        if not handle_captcha(page, headless):
            log.error("CAPTCHA не решена, пропускаю запрос: %s", query)
            return []
    else:
        throttle.on_success()

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
        if detect_captcha(page):
            if not handle_captcha(page, headless):
                return []
            try:
                page.wait_for_selector(item_sel or ITEM_SEL, timeout=10000)
                found = True
            except Exception:
                pass

    if not found:
        log.warning("Результаты не найдены для запроса: %s", query)
        return []

    engine.probe_and_detect(page, mode="list")

    if api_intercept:
        log.info("Режим API-перехвата")
        orgs = run_api_intercept(page, max_results, scroll_pause)
    else:
        orgs = scroll_and_parse(page, max_results, scroll_pause, on_org=on_org)

    log.info("Извлечено организаций: %d", len(orgs))
    return orgs


def search_with_retry(
    page: Page,
    query: str,
    max_results: int,
    scroll_pause: float,
    api_intercept: bool,
    on_org: Any = None,
    headless: bool = True,
    max_retries: int = 3,
) -> list[Organization]:
    """Обёртка над search_and_collect с retry при ошибках."""
    for attempt in range(1, max_retries + 1):
        try:
            orgs = search_and_collect(
                page, query, max_results, scroll_pause,
                api_intercept, on_org=on_org, headless=headless,
            )
            if orgs:
                return orgs
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
                try:
                    page.goto("https://yandex.ru/maps/", wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(random.randint(2000, 4000))
                except Exception as goto_exc:
                    log.debug("Не удалось вернуться на /maps/ перед retry: %s", goto_exc)
            else:
                log.error("Не удалось выполнить поиск «%s» после %d попыток: %s",
                          query, max_retries, exc)
                return []
    return []


def enrich_orgs(ctx: BrowserContext, orgs: list[Organization], n_tabs: int = 3) -> None:
    """Обогатить организации данными с карточек.

    Использует n_tabs параллельных вкладок для ускорения.
    """
    if not orgs:
        return
    log.info("Обогащаю данные с карточек организаций (%d шт, %d вкладок)…", len(orgs), n_tabs)

    pages: list[Page] = []
    for _ in range(min(n_tabs, len(orgs))):
        try:
            pages.append(ctx.new_page())
        except Exception as exc:
            log.debug("Не удалось создать вкладку (имеется %d): %s", len(pages), exc)
            break
    if not pages:
        pages.append(ctx.new_page())

    detail_detected = [False]
    pbar = None
    if HAS_TQDM:
        pbar = tqdm(total=len(orgs), desc="Обогащение карточек", unit="орг")

    for idx, org in enumerate(orgs):
        page = pages[idx % len(pages)]
        enrich_from_detail(page, org, _detail_detected=detail_detected)
        if pbar:
            pbar.update(1)
        elif (idx + 1) % 20 == 0:
            log.info("Обогащено: %d / %d", idx + 1, len(orgs))
        page.wait_for_timeout(random.randint(300, 800))

    if pbar:
        pbar.close()

    for p in pages:
        try:
            p.close()
        except Exception as exc:
            log.debug("Не удалось закрыть вкладку: %s", exc)
